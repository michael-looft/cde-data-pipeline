"""
Annual enrollment update: process one CDE Census Day Enrollment file and
import Enrollment data into the datadashboard MySQL database
(public_datasets table, Dataset='Enrollment', Indicator='Enrollment').

Connects directly to MySQL — no phpMyAdmin needed.

Usage:
  python update_enrollment.py [source_file]

  source_file — (optional) path to CDEnrollXXXX.txt downloaded from CDE.
                Year is derived automatically from the filename:
                CDEnroll2526.txt → Year 2026
                If omitted, the script downloads the most recent file from
                https://www3.cde.ca.gov/demo-downloads/census/

Examples:
  python update_enrollment.py                    # auto-download latest
  python update_enrollment.py CDEnroll2526.txt   # use a local file

Requirements:
  pip3 install pandas pymysql

Credentials:
  DB_PASSWORD is read from an environment variable, or from a `.env`
  file placed next to this script. The `.env` file should contain:

      DB_PASSWORD=your-password-here

  (Optional overrides: DB_HOST, DB_PORT, DB_USER, DB_NAME.)
  Keep `.env` out of version control — add it to .gitignore.
"""

import sys
import os
import re
import subprocess
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path
import pandas as pd
import pymysql

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file(path):
    """Minimal .env loader — KEY=VALUE lines, no dependencies.

    Lines starting with '#' are ignored. Existing environment variables
    take precedence (so real env vars always win over the file)."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# Load .env sitting next to this script (if present)
load_env_file(os.path.join(SCRIPT_DIR, ".env"))

# ── Database connection settings ──────────────────────────────────────────────
# Host/user/db have sensible defaults; password MUST come from env or .env.
DB_HOST     = os.environ.get("DB_HOST",  "your-db-host.example.com")
DB_PORT     = int(os.environ.get("DB_PORT", "3306"))
DB_USER     = os.environ.get("DB_USER",  "your-db-user")
DB_NAME     = os.environ.get("DB_NAME",  "datadashboard")
DB_PASSWORD = os.environ.get("DB_PASSWORD")

BATCH_SIZE  = 500

# ── public_datasets insert columns (id and LastUpdated set by MySQL) ──────────
ENR_INSERT_COLS = [
    "Dataset", "District", "School", "Year", "Indicator",
    "DemographicCategory", "Demographic", "ItemDescription",
    "Result", "Group_Total", "Active",
]
ENR_NUMERIC = {"Year", "Result", "Group_Total", "Active"}


def derive_year(filename):
    """CDEnroll2526.txt → 2026  |  CDEnroll2425.txt → 2025"""
    m = re.search(r"CDEnroll(\d{4})", os.path.basename(filename), re.IGNORECASE)
    if not m:
        raise ValueError(
            f"Cannot derive year from filename '{filename}'.\n"
            "Expected format: CDEnrollYYYY.txt (e.g. CDEnroll2526.txt)"
        )
    yy = m.group(1)[-2:]   # last two digits: "26"
    return 2000 + int(yy)  # → 2026


# ── Auto-download helpers ─────────────────────────────────────────────────────
CDE_URL_TEMPLATE = "https://www3.cde.ca.gov/demo-downloads/census/cdenroll{code}.txt"


def current_school_year_code(today=None):
    """Return the 4-digit CDE school-year code to try first.

    Always try the most recent POSSIBLE school year; the 404 fallback
    will step back if the file isn't posted yet.

      • Aug–Dec : new school year just started → try the one that just ended
                  e.g. Oct 2026 → 2025-26 → "2526"
      • Jan–Jul : we're in the current school year → try it (it may have
                  just been posted, as in April 2026) and fall back if 404
                  e.g. Apr 2026 → 2025-26 → "2526"
    """
    if today is None:
        today = date.today()
    if today.month >= 8:
        start = today.year
        end   = today.year + 1
    else:
        start = today.year - 1
        end   = today.year
    return f"{start % 100:02d}{end % 100:02d}"


def previous_year_code(code):
    """'2425' → '2324'  (subtract one school year)"""
    start = int(code[:2])
    end   = int(code[2:])
    return f"{(start - 1) % 100:02d}{(end - 1) % 100:02d}"


def download_cde_file(dest_dir, today=None, max_fallbacks=3):
    """Download the most recent CDE enrollment file into dest_dir.

    Builds the URL from today's date. If that URL returns 404, steps back
    one school year and retries, up to max_fallbacks times.

    Returns the local path to the saved file.
    """
    code = current_school_year_code(today)
    tried = []
    for attempt in range(max_fallbacks + 1):
        url  = CDE_URL_TEMPLATE.format(code=code)
        dest = os.path.join(dest_dir, f"CDEnroll{code}.txt")
        tried.append(url)
        print(f"  Trying: {url}")
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (enrollment-updater)"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  ✗ 404 Not Found — stepping back one school year")
                code = previous_year_code(code)
                continue
            raise

        with open(dest, "wb") as f:
            f.write(data)
        print(f"  ✓ Downloaded {len(data):,} bytes → {dest}")
        return dest

    raise RuntimeError(
        "Could not locate a CDE enrollment file. Tried:\n  - "
        + "\n  - ".join(tried)
    )


def get_connection():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, charset="utf8mb4",
        autocommit=False,
        connect_timeout=30,
    )


def coerce_row(row, cols, numeric_cols):
    values = []
    for col in cols:
        v = row.get(col, "")
        if col in numeric_cols:
            if pd.isna(v) or str(v).strip() in ("", "*"):
                values.append(None)
            else:
                try:
                    values.append(int(float(str(v))))
                except (ValueError, TypeError):
                    values.append(None)
        else:
            s = str(v).strip() if not pd.isna(v) else None
            values.append(s if s != "" else None)
    return tuple(values)


def batch_insert(cur, sql, df, cols, numeric_cols):
    inserted = 0
    batch = []
    for _, row in df.iterrows():
        batch.append(coerce_row(row, cols, numeric_cols))
        if len(batch) >= BATCH_SIZE:
            cur.executemany(sql, batch)
            inserted += len(batch)
            print(f"    … {inserted} rows inserted", end="\r")
            batch = []
    if batch:
        cur.executemany(sql, batch)
        inserted += len(batch)
    print(f"    … {inserted} rows inserted")
    return inserted


def import_enrollment(conn, csv_path, year):
    """DELETE existing enrollment rows for this year, then INSERT from CSV."""
    df = pd.read_csv(csv_path, dtype=str)

    # Group_Total is NOT NULL — default nulls/blanks to 0
    df["Group_Total"] = df["Group_Total"].apply(
        lambda v: "0" if (pd.isna(v) or str(v).strip() == "") else str(v)
    )

    ph  = ", ".join(["%s"] * len(ENR_INSERT_COLS))
    sql = (f"INSERT INTO public_datasets "
           f"({', '.join(ENR_INSERT_COLS)}) VALUES ({ph})")

    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM public_datasets "
            "WHERE Dataset = 'Enrollment' AND Indicator = 'Enrollment' AND Year = %s",
            (year,),
        )
        print(f"  Deleted {cur.rowcount} existing rows for Year={year}.")

        n = batch_insert(cur, sql, df, ENR_INSERT_COLS, ENR_NUMERIC)
        conn.commit()

    print(f"  ✓ public_datasets (Enrollment): {n} rows inserted.")
    return n


def main():
    # Accept an optional source file. If omitted, auto-download from CDE.
    if len(sys.argv) >= 2:
        if sys.argv[1] in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        source_file = sys.argv[1]
        if not os.path.exists(source_file):
            print(f"Error: source file not found: {source_file}")
            sys.exit(1)
    else:
        print()
        print("=" * 62)
        print("  No source file given — downloading latest from CDE …")
        print("=" * 62)
        source_file = download_cde_file(SCRIPT_DIR)

    year = derive_year(source_file)
    base = os.path.splitext(source_file)[0]
    enrollment_csv = base + "_import.csv"

    print()
    print("=" * 62)
    print(f"  Enrollment update — {os.path.basename(source_file)}")
    print(f"  Year: {year}")
    print("=" * 62)

    # ── Step 1: Generate CSV ───────────────────────────────────────────────────
    print(f"\n[1/4] Generating Enrollment import CSV …")
    subprocess.run(
        [sys.executable,
         os.path.join(SCRIPT_DIR, "process_cde_enrollment.py"),
         "--input",  source_file,
         "--output", enrollment_csv,
         "--year",   str(year)],
        check=True,
    )

    # ── Step 2: Always generate SQL fallback file ──────────────────────────────
    # phpMyAdmin import bypasses Remote MySQL entirely; keep this ready so the
    # fallback path is one step away even when everything else works.
    enrollment_sql = base + "_import.sql"
    print(f"\n[2/4] Generating SQL fallback file (for phpMyAdmin) …")
    subprocess.run(
        [sys.executable,
         os.path.join(SCRIPT_DIR, "csv_to_sql.py"),
         enrollment_csv,
         "--output", enrollment_sql,
         "--year",   str(year)],
        check=True,
    )

    # ── Step 3: Connect to MySQL ───────────────────────────────────────────────
    if not DB_PASSWORD:
        print("\n  ✗ DB_PASSWORD is not set.")
        print("    Either export it in your shell:")
        print("        export DB_PASSWORD='your-password'")
        print("    or create a `.env` file next to this script containing:")
        print("        DB_PASSWORD=your-password")
        sys.exit(1)

    print(f"\n[3/4] Connecting to MySQL ({DB_HOST}) …")
    try:
        conn = get_connection()
    except pymysql.err.OperationalError as e:
        print(f"\n  ✗ Direct connection failed: {e}")
        print()
        print("  ────────────────────────────────────────────────────────────")
        print("  FALLBACK: import via phpMyAdmin (bypasses Remote MySQL)")
        print("  ────────────────────────────────────────────────────────────")
        print("  1. Open:  https://your-phpmyadmin-url?"
              "route=/database/structure&db=datadashboard")
        print("  2. Log in with your Tigertech 'My Account' credentials.")
        print(f"  3. Click the 'Import' tab and upload:\n       {enrollment_sql}")
        print("  4. Click 'Go'. The SQL is transaction-wrapped, so it either")
        print("     fully applies or fully rolls back.")
        print()
        print("  To avoid this fallback next time, enable Remote MySQL in")
        print("  Tigertech 'My Account' → MySQL Databases → Change → check")
        print("  'Allow connections from any computer on the Internet'.")
        sys.exit(2)
    print("  ✓ Connected.")

    # ── Step 4: Import ─────────────────────────────────────────────────────────
    print("\n[4/4] Importing Enrollment data …")
    try:
        import_enrollment(conn, enrollment_csv, year)
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"\n  ✗ Error during import — rolled back: {e}")
        print(f"  The SQL fallback file is ready at: {enrollment_sql}")
        print("  You can retry via phpMyAdmin:")
        print("    https://your-phpmyadmin-url?"
              "route=/database/structure&db=datadashboard")
        raise

    conn.close()

    print()
    print("=" * 62)
    print("  ✓  Enrollment update complete!")
    print("=" * 62)
    print(f"\n  CSV saved for reference: {enrollment_csv}")
    print(f"  SQL fallback (unused):   {enrollment_sql}")
    print()


if __name__ == "__main__":
    main()

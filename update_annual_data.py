"""
Annual data update: process one CDE ACGR source file and import both
  • College Readiness  →  public_datasets  (Dataset='Milestone', Indicator='College Readiness')
  • Graduates          →  public_datasets_graduates

Connects directly to MySQL — no phpMyAdmin needed.

Usage:
  python update_annual_data.py [<source_file>] [--full-reload]

  source_file   — optional path to acgrYY.txt. If omitted, the script
                  determines the current school year from today's date and
                  downloads the file from
                  https://www3.cde.ca.gov/demo-downloads/acgr/acgrYY.txt
                  (falling back one year on a 404). If given but the file
                  does not exist locally, the script downloads it under
                  that name.
  --full-reload — TRUNCATE public_datasets_graduates and delete ALL College
                  Readiness rows before inserting (default: only replaces
                  the year(s) found in the source file)

Examples:
  python update_annual_data.py                          # auto-download + update
  python update_annual_data.py acgr25.txt               # update from local file
  python update_annual_data.py --full-reload            # auto-download + full rebuild

Requirements:
  pip install pandas pymysql

Credentials:
  DB_PASSWORD is read from an environment variable, or from a `.env`
  file placed next to this script. The `.env` file should contain:

      DB_PASSWORD=your-password-here

  (Optional overrides: DB_HOST, DB_PORT, DB_USER, DB_NAME.)
  Keep `.env` out of version control — add it to .gitignore.
"""

import sys
import os
import subprocess
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
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

BATCH_SIZE  = 500   # rows per INSERT batch


# ── Graduates table columns (auto-increment id and LastUpdated are excluded) ──
GRAD_INSERT_COLS = [
    "Dataset", "District", "School", "Year", "Indicator",
    "DemographicCategory", "Demographic",
    "Cohort", "Graduates", "MetAG", "Biliteracy", "GoldenStateSealMerit",
    "CHSPECompleter", "AdultEdDiploma", "SPEDCertificate",
    "GEDCompleter", "OtherTransfer", "Dropouts", "StillEnrolled",
    "AggregateLevel",
]
GRAD_NUMERIC = {
    "Year", "Cohort", "Graduates", "MetAG", "Biliteracy", "GoldenStateSealMerit",
    "CHSPECompleter", "AdultEdDiploma", "SPEDCertificate",
    "GEDCompleter", "OtherTransfer", "Dropouts", "StillEnrolled",
}

# ── public_datasets columns (auto-increment id and LastUpdated excluded) ──────
ACGR_INSERT_COLS = [
    "Dataset", "District", "School", "Year", "Indicator",
    "DemographicCategory", "Demographic", "ItemDescription",
    "Result", "Group_Total", "Active",
]
ACGR_NUMERIC = {"Year", "Result", "Group_Total", "Active"}


def get_connection():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, charset="utf8mb4",
        autocommit=False,
        connect_timeout=30,
    )


def coerce_row(row, cols, numeric_cols):
    """Convert a pandas row to a tuple of Python-native values for MySQL."""
    values = []
    for col in cols:
        v = row.get(col, "")
        if col in numeric_cols:
            if pd.isna(v) or str(v).strip() == "":
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
    """Insert all rows from df in BATCH_SIZE chunks. Returns row count."""
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


def import_graduates(conn, csv_path, full_reload, years):
    """Update public_datasets_graduates from csv_path."""
    df = pd.read_csv(csv_path, dtype=str)

    ph  = ", ".join(["%s"] * len(GRAD_INSERT_COLS))
    sql = (f"INSERT INTO public_datasets_graduates "
           f"({', '.join(GRAD_INSERT_COLS)}) VALUES ({ph})")

    with conn.cursor() as cur:
        if full_reload:
            print("  Truncating public_datasets_graduates …")
            cur.execute("TRUNCATE TABLE public_datasets_graduates")
        else:
            for yr in sorted(years):
                cur.execute(
                    "DELETE FROM public_datasets_graduates WHERE Year = %s", (yr,)
                )
                print(f"  Deleted existing rows for Year={yr} "
                      f"({cur.rowcount} rows removed)")

        n = batch_insert(cur, sql, df, GRAD_INSERT_COLS, GRAD_NUMERIC)
        conn.commit()

    print(f"  ✓ public_datasets_graduates: {n} rows inserted.")
    return n


def import_acgr(conn, csv_path, full_reload, years):
    """Update public_datasets (College Readiness rows) from csv_path."""
    df = pd.read_csv(csv_path, dtype=str)

    # Group_Total is NOT NULL in the table — default nulls to 0
    df["Group_Total"] = df["Group_Total"].apply(
        lambda v: "0" if (pd.isna(v) or str(v).strip() == "") else str(v)
    )

    ph  = ", ".join(["%s"] * len(ACGR_INSERT_COLS))
    sql = (f"INSERT INTO public_datasets "
           f"({', '.join(ACGR_INSERT_COLS)}) VALUES ({ph})")

    with conn.cursor() as cur:
        if full_reload:
            print("  Deleting all College Readiness rows from public_datasets …")
            cur.execute(
                "DELETE FROM public_datasets "
                "WHERE Dataset = 'Milestone' AND Indicator = 'College Readiness'"
            )
            print(f"  Deleted {cur.rowcount} rows.")
        else:
            for yr in sorted(years):
                cur.execute(
                    "DELETE FROM public_datasets "
                    "WHERE Dataset = 'Milestone' AND Indicator = 'College Readiness' "
                    "AND Year = %s",
                    (yr,),
                )
                print(f"  Deleted existing rows for Year={yr} "
                      f"({cur.rowcount} rows removed)")

        n = batch_insert(cur, sql, df, ACGR_INSERT_COLS, ACGR_NUMERIC)
        conn.commit()

    print(f"  ✓ public_datasets (College Readiness): {n} rows inserted.")
    return n


ACGR_URL_TEMPLATE = "https://www3.cde.ca.gov/demo-downloads/acgr/acgr{yy:02d}.txt"


def determine_acgr_year(today=None):
    """Return the most-recent ACGR calendar year.

    If today's month is November or later, the CDE will have posted the file
    for the current calendar year (e.g., acgr25.txt in Nov 2025). Otherwise
    the most recent available file is from the prior calendar year (e.g.,
    acgr25.txt is still the latest in April 2026)."""
    today = today or date.today()
    return today.year if today.month >= 11 else today.year - 1


def _try_download(url, dest_path):
    """Attempt to download url → dest_path. Returns True on 200, False on 404.
    Raises on any other error."""
    req = Request(url, headers={"User-Agent": "acgr-annual-update/1.0"})
    try:
        with urlopen(req, timeout=60) as resp:
            data = resp.read()
        with open(dest_path, "wb") as f:
            f.write(data)
        return True
    except HTTPError as e:
        if e.code == 404:
            return False
        raise


def download_acgr_file(year=None, dest_dir=None):
    """Download the most recent ACGR file into dest_dir.

    Tries acgrYY.txt for the determined year; on 404 falls back one year.
    Returns the local file path of the downloaded file."""
    if year is None:
        year = determine_acgr_year()
    dest_dir = dest_dir or SCRIPT_DIR

    for attempt_year in (year, year - 1):
        yy = attempt_year % 100
        filename = f"acgr{yy:02d}.txt"
        url = ACGR_URL_TEMPLATE.format(yy=yy)
        dest_path = os.path.join(dest_dir, filename)
        print(f"  Trying {url} …")
        ok = _try_download(url, dest_path)
        if ok:
            size_kb = os.path.getsize(dest_path) / 1024
            print(f"  ✓ Downloaded {filename} ({size_kb:,.0f} KB)")
            return dest_path
        print(f"  … 404 for {filename}, trying previous year")

    raise RuntimeError(
        f"Could not download ACGR file for {year} or {year - 1}. "
        f"Check https://www.cde.ca.gov/ds/ad/filesacgr.asp manually."
    )


def main():
    # ── Parse args ─────────────────────────────────────────────────────────────
    args = sys.argv[1:]
    full_reload = "--full-reload" in args
    positional  = [a for a in args if not a.startswith("--")]

    if positional:
        source_file = positional[0]
        if not os.path.exists(source_file):
            # Treat the argument as the target filename and download it
            print(f"\n[0/4] Source file '{source_file}' not found locally — "
                  f"attempting download …")
            source_file = download_acgr_file(
                dest_dir=os.path.dirname(os.path.abspath(source_file)) or SCRIPT_DIR
            )
    else:
        # No argument — auto-determine the year and download
        year = determine_acgr_year()
        print(f"\n[0/4] No source file given — auto-downloading most recent ACGR "
              f"(target year {year}) …")
        source_file = download_acgr_file(year=year)

    base         = os.path.splitext(source_file)[0]
    acgr_csv     = base + "_import.csv"
    graduates_csv = base + "_graduates_import.csv"

    print()
    print("=" * 62)
    print(f"  Annual data update — {os.path.basename(source_file)}")
    if full_reload:
        print("  Mode: FULL RELOAD (replaces all years)")
    print("=" * 62)

    # ── Step 1: Generate CSVs ──────────────────────────────────────────────────
    print("\n[1/4] Generating College Readiness import CSV …")
    subprocess.run(
        [sys.executable,
         os.path.join(SCRIPT_DIR, "process_acgr.py"),
         source_file, acgr_csv],
        check=True,
    )

    print("\n[2/4] Generating Graduates import CSV …")
    subprocess.run(
        [sys.executable,
         os.path.join(SCRIPT_DIR, "process_graduates.py"),
         source_file, graduates_csv],
        check=True,
    )

    # Detect year(s) in the output so we can do a targeted delete
    df_yr   = pd.read_csv(graduates_csv, usecols=["Year"], dtype=str)
    years   = sorted(set(int(y) for y in df_yr["Year"].dropna()))
    print(f"\n  Year(s) found in source: {years}")

    # ── Step 2: Connect ────────────────────────────────────────────────────────
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
        print(f"\n  ✗ Connection failed: {e}")
        print("  Check that your IP is allowed under cPanel → Remote MySQL.")
        sys.exit(1)
    print("  ✓ Connected.")

    # ── Step 3: Import ─────────────────────────────────────────────────────────
    print("\n[4/4] Importing data …")
    try:
        print("\n  — Graduates —")
        import_graduates(conn, graduates_csv, full_reload, years)

        print("\n  — College Readiness —")
        import_acgr(conn, acgr_csv, full_reload, years)

    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"\n  ✗ Error during import — rolled back: {e}")
        raise

    conn.close()

    # ── Done ───────────────────────────────────────────────────────────────────
    print()
    print("=" * 62)
    print("  ✓  Annual data update complete!")
    print("=" * 62)
    print(f"\n  CSVs saved for reference:")
    print(f"    College Readiness : {acgr_csv}")
    print(f"    Graduates         : {graduates_csv}")
    print()


if __name__ == "__main__":
    main()

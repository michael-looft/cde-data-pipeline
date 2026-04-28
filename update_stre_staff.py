"""
Annual STRE staff update: process one CDE STRE staff data file and import
into the datadashboard MySQL database (public_datasets table, Dataset='Staff',
Indicator='Staff', ItemDescription IN ('Administrators','All',
'Non-Instructional Support','Pupil Services','Teachers')).

Connects directly to MySQL — no phpMyAdmin needed.

Usage:
  python update_stre_staff.py <source_file>

  source_file — path to stre####.txt downloaded from CDE
                (https://www.cde.ca.gov/ds/ad/filesstretextdata.asp)
                Year is derived automatically from the filename:
                stre2425.txt → Year 2025

Example:
  python update_stre_staff.py stre2425.txt

Requirements:
  pip install pandas pymysql

Credentials:
  DB_PASSWORD is read from an environment variable, or from a `.env`
  file placed next to this script. The `.env` file should contain:

      DB_PASSWORD=your-password-here

  (Optional overrides: DB_HOST, DB_PORT, DB_USER, DB_NAME.)
  Keep `.env` out of version control — add it to .gitignore.

Note on scope:
  This script manages STRE-derived staff types only:
    Administrators, All, Non-Instructional Support, Pupil Services, Teachers
  Paraeducators (sourced from CBEDS, not STRE) are handled separately by
  update_paraeducators.py and are never touched by this script.
"""

import sys
import os
import re
import subprocess
from pathlib import Path
import pandas as pd
import pymysql

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Staff types managed by this script (Paraeducators excluded — handled by CBEDS)
STRE_ITEM_DESCRIPTIONS = [
    "Administrators",
    "All",
    "Non-Instructional Support",
    "Pupil Services",
    "Teachers",
]


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
DB_HOST     = os.environ.get("DB_HOST",  "your-db-host.example.com")
DB_PORT     = int(os.environ.get("DB_PORT", "3306"))
DB_USER     = os.environ.get("DB_USER",  "your-db-user")
DB_NAME     = os.environ.get("DB_NAME",  "datadashboard")
DB_PASSWORD = os.environ.get("DB_PASSWORD")

BATCH_SIZE  = 500

# ── public_datasets insert columns (id and LastUpdated set by MySQL) ──────────
INSERT_COLS = [
    "Dataset", "District", "School", "Year", "Indicator",
    "DemographicCategory", "Demographic", "ItemDescription",
    "Result", "Group_Total", "Active",
]
NUMERIC_COLS = {"Year", "Result", "Group_Total", "Active"}


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


def import_stre_staff(conn, csv_path, year):
    """DELETE existing STRE staff rows for this year, then INSERT from CSV.

    Only touches ItemDescription values managed by this script
    (Administrators, All, Non-Instructional Support, Pupil Services, Teachers).
    Paraeducators are never deleted or inserted here.
    """
    df = pd.read_csv(csv_path, dtype=str)

    ph  = ", ".join(["%s"] * len(INSERT_COLS))
    sql = (f"INSERT INTO public_datasets "
           f"({', '.join(INSERT_COLS)}) VALUES ({ph})")

    placeholders = ", ".join(["%s"] * len(STRE_ITEM_DESCRIPTIONS))

    with conn.cursor() as cur:
        # Preflight count
        cur.execute(
            f"SELECT COUNT(*) FROM public_datasets "
            f"WHERE Dataset = 'Staff' AND Indicator = 'Staff' "
            f"AND ItemDescription IN ({placeholders}) AND Year = %s",
            (*STRE_ITEM_DESCRIPTIONS, year),
        )
        existing = cur.fetchone()[0]
        print(f"  Existing rows for Year={year}: {existing}")

        cur.execute(
            f"DELETE FROM public_datasets "
            f"WHERE Dataset = 'Staff' AND Indicator = 'Staff' "
            f"AND ItemDescription IN ({placeholders}) AND Year = %s",
            (*STRE_ITEM_DESCRIPTIONS, year),
        )
        print(f"  Deleted {cur.rowcount} rows.")

        n = batch_insert(cur, sql, df, INSERT_COLS, NUMERIC_COLS)
        conn.commit()

    print(f"  ✓ public_datasets (STRE staff): {n} rows inserted.")
    return n


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    source_file = sys.argv[1]
    if not os.path.exists(source_file):
        print(f"Error: source file not found: {source_file}")
        sys.exit(1)

    # Derive year from filename: stre2425.txt → Year 2025
    name = os.path.basename(source_file).lower()
    m = re.search(r"stre(\d{2})(\d{2})", name)
    if not m:
        print(f"Error: cannot derive year from filename '{source_file}'.")
        print("Expected format: stre####.txt (e.g. stre2425.txt)")
        sys.exit(1)

    year      = 2000 + int(m.group(2))
    stem      = f"stre{m.group(1)}{m.group(2)}"
    stre_csv  = os.path.join(SCRIPT_DIR, stem + "_import.csv")

    print()
    print("=" * 62)
    print(f"  STRE staff update — {os.path.basename(source_file)}")
    print(f"  Year: {year}")
    print("=" * 62)

    # ── Step 1: Generate CSV ───────────────────────────────────────────────────
    print(f"\n[1/3] Generating STRE staff import CSV …")
    subprocess.run(
        [sys.executable,
         os.path.join(SCRIPT_DIR, "process_stre_staff.py"),
         source_file, stre_csv],
        check=True,
    )

    # ── Step 2: Connect ────────────────────────────────────────────────────────
    if not DB_PASSWORD:
        print("\n  ✗ DB_PASSWORD is not set.")
        print("    Either export it in your shell:")
        print("        export DB_PASSWORD='your-password'")
        print("    or create a `.env` file next to this script containing:")
        print("        DB_PASSWORD=your-password")
        sys.exit(1)

    print(f"\n[2/3] Connecting to MySQL ({DB_HOST}) …")
    try:
        conn = get_connection()
    except pymysql.err.OperationalError as e:
        print(f"\n  ✗ Connection failed: {e}")
        print("  Check that your IP is allowed under cPanel → Remote MySQL.")
        sys.exit(1)
    print("  ✓ Connected.")

    # ── Step 3: Import ─────────────────────────────────────────────────────────
    print("\n[3/3] Importing STRE staff data …")
    try:
        import_stre_staff(conn, stre_csv, year)
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"\n  ✗ Error during import — rolled back: {e}")
        raise

    conn.close()

    print()
    print("=" * 62)
    print("  ✓  STRE staff update complete!")
    print("=" * 62)
    print(f"\n  CSV saved for reference: {stre_csv}")
    print()


if __name__ == "__main__":
    main()

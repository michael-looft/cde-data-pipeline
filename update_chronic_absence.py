"""
Annual chronic absence update: process one CDE Chronic Absenteeism file and
import Chronic Absences data into the datadashboard MySQL database
(public_datasets table, Dataset='Absences', Indicator='Chronic Absences').

Connects directly to MySQL — no phpMyAdmin needed.

Usage:
  python update_chronic_absence.py <source_file>

  source_file — path to chronicabsenteeismYY-v2.txt downloaded from CDE
                (https://www.cde.ca.gov/ds/ad/filesabd.asp)
                Year is derived automatically from the filename:
                chronicabsenteeism25-v2.txt → Year 2025

Example:
  python update_chronic_absence.py chronicabsenteeism25-v2.txt

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
import re
import subprocess
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
CA_INSERT_COLS = [
    "Dataset", "District", "School", "Year", "Indicator",
    "DemographicCategory", "Demographic", "ItemDescription",
    "Result", "Group_Total", "Active",
]
CA_NUMERIC = {"Year", "Result", "Group_Total", "Active"}


def derive_year(filename):
    """chronicabsenteeism25-v2.txt → 2025
       chronicabsenteeism2425-v2.txt → 2025
    """
    name = os.path.basename(filename)
    # Try four-digit (e.g., 2425) first, then two-digit (e.g., 25)
    m = re.search(r"chronicabsenteeism(\d{4})", name, re.IGNORECASE)
    if m:
        return 2000 + int(m.group(1)[-2:])
    m = re.search(r"chronicabsenteeism(\d{2})", name, re.IGNORECASE)
    if m:
        return 2000 + int(m.group(1))
    raise ValueError(
        f"Cannot derive year from filename '{filename}'.\n"
        "Expected format: chronicabsenteeismYY-v2.txt (e.g. chronicabsenteeism25-v2.txt)"
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


def import_chronic_absence(conn, csv_path, year):
    """DELETE existing chronic absence rows for this year, then INSERT from CSV."""
    df = pd.read_csv(csv_path, dtype=str)

    # Group_Total is NOT NULL — default nulls/blanks to 0
    df["Group_Total"] = df["Group_Total"].apply(
        lambda v: "0" if (pd.isna(v) or str(v).strip() == "") else str(v)
    )

    ph  = ", ".join(["%s"] * len(CA_INSERT_COLS))
    sql = (f"INSERT INTO public_datasets "
           f"({', '.join(CA_INSERT_COLS)}) VALUES ({ph})")

    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM public_datasets "
            "WHERE Dataset = 'Absences' AND Indicator = 'Chronic Absences' AND Year = %s",
            (year,),
        )
        print(f"  Deleted {cur.rowcount} existing rows for Year={year}.")

        n = batch_insert(cur, sql, df, CA_INSERT_COLS, CA_NUMERIC)
        conn.commit()

    print(f"  ✓ public_datasets (Chronic Absences): {n} rows inserted.")
    return n


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    source_file = sys.argv[1]
    if not os.path.exists(source_file):
        print(f"Error: source file not found: {source_file}")
        sys.exit(1)

    year = derive_year(source_file)
    base = os.path.splitext(source_file)[0]
    chronic_csv = base + "_import.csv"

    print()
    print("=" * 62)
    print(f"  Chronic absence update — {os.path.basename(source_file)}")
    print(f"  Year: {year}")
    print("=" * 62)

    # ── Step 1: Generate CSV ───────────────────────────────────────────────────
    print(f"\n[1/3] Generating Chronic Absence import CSV …")
    subprocess.run(
        [sys.executable,
         os.path.join(SCRIPT_DIR, "process_chronic_absence.py"),
         source_file, chronic_csv],
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
    print("\n[3/3] Importing Chronic Absence data …")
    try:
        import_chronic_absence(conn, chronic_csv, year)
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"\n  ✗ Error during import — rolled back: {e}")
        raise

    conn.close()

    print()
    print("=" * 62)
    print("  ✓  Chronic absence update complete!")
    print("=" * 62)
    print(f"\n  CSV saved for reference: {chronic_csv}")
    print()


if __name__ == "__main__":
    main()

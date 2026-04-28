"""
Process CDE CBEDS Paraeducator data file into public_datasets format.
Filters to Marin County (county 21) + derives a county aggregate.

Usage:
  python process_paraeducators.py <source_file> [output_file] [target_file]

  source_file  — path to cbedsora##a.txt downloaded from CDE
                 (https://www.cde.ca.gov/ds/ad/filescbedsoraa.asp)
  output_file  — (optional) path for the output CSV; defaults to
                 <source>_import.csv in the same folder as source_file
  target_file  — (optional) v_staff_data.csv dump for comparison mode

Example:
  python process_paraeducators.py cbedsora24a.txt
  python process_paraeducators.py cbedsora24a.txt cbedsora24a_import.csv v_staff_data.csv

Source file columns used:
  Cdscode, CountyName, DistrictName, SchoolName, Description, Level,
  AmericanIndian, Asian, PacificIslander, Filipino, Hispanic,
  AfricanAmerican, White, MultorNoResp, Total, Year

Business rules:
  - Filter Description to rows containing "Paraprofessionals"
    (Male Paraprofessionals, Female Paraprofessionals, Nonbinary Paraprofessionals)
  - Aggregate across genders at each level
  - School-level records: Level=S rows only, grouped by Cdscode+SchoolName
  - District "All Schools": sum of both S and D (district office) rows per district
  - Marin County "All Schools": sum of all S and D rows in county 21
  - Total = sum of Total column (rounded to int)
  - Persons of Color = Total - White (rounded to int; MultorNoResp is counted as PoC)
  - Group_Total = same as Total for this dataset
"""

import sys
import os
import re
import pandas as pd
from io import StringIO

# ── Resolve paths from command-line args or defaults ─────────────────────────
if len(sys.argv) >= 2:
    SOURCE_FILE = sys.argv[1]
else:
    SOURCE_FILE = os.path.join(os.path.dirname(__file__), "cbedsora24a.txt")

if len(sys.argv) >= 3:
    OUTPUT_FILE = sys.argv[2]
else:
    # Name the output using the *corrected* year number (filename digit + 1),
    # because CDE names these files one year behind the academic year they cover:
    # cbedsora24a.txt = 2024-25 data  →  output: cbedsora25a_import.csv
    # cbedsora25a.txt = 2025-26 data  →  output: cbedsora26a_import.csv
    # Output is always written to the current working directory (not the source
    # file's directory, which may be read-only).
    src_name = os.path.basename(SOURCE_FILE)
    m = re.search(r"cbedsora(\d{2})a", src_name, re.IGNORECASE)
    if m:
        corrected_num  = int(m.group(1)) + 1
        corrected_stem = f"cbedsora{corrected_num:02d}a"
        OUTPUT_FILE    = corrected_stem + "_import.csv"
    else:
        OUTPUT_FILE = os.path.splitext(src_name)[0] + "_import.csv"

TARGET_FILE = None
if len(sys.argv) >= 4:
    TARGET_FILE = sys.argv[3]


# ── District name mapping (CBEDS source → public_datasets display) ───────────
DISTRICT_NAME_MAP = {
    "Bolinas-Stinson Union":          "Bolinas-Stinson Union School District",
    "Kentfield Elementary":           "Kentfield Elementary School District",
    "Laguna Joint Elementary":        "Laguna Joint Elementary School District",
    "Lagunitas Elementary":           "Lagunitas Elementary School District",
    "Larkspur-Corte Madera":          "Larkspur-Corte Madera",
    "Marin County Office of Education": "Marin County Office Of Education",
    "Mill Valley Elementary":         "Mill Valley Elementary School District",
    "Miller Creek Elementary":        "Miller Creek Elementary School District",
    "Nicasio":                        "Nicasio School District",
    "Novato Unified":                 "Novato Unified School District",
    "Reed Union Elementary":          "Reed Union Elementary School District",
    "Ross Elementary":                "Ross Elementary School District",
    "Ross Valley Elementary":         "Ross Valley Elementary School District",
    "San Rafael City Elementary":     "San Rafael City Elementary",
    "San Rafael City High":           "San Rafael City High",
    "Sausalito Marin City":           "Sausalito Marin City School District",
    "Shoreline Unified":              "Shoreline Unified School District",
    "Tamalpais Union High":           "Tamalpais Union High School District",
}

# ── School name remapping (CBEDS SchoolName → display name) ─────────────────
# Add entries here if CDE renames a school between years.
SCHOOL_NAME_MAP = {
    # Example: "Old CDE Name": "Dashboard Display Name",
}

NUMERIC_COLS = [
    "AmericanIndian", "Asian", "PacificIslander", "Filipino",
    "Hispanic", "AfricanAmerican", "White", "MultorNoResp", "Total",
]


def load_source(path):
    """Load CBEDS text file, stripping Windows CRs.

    Normalises the CdsCode/Cdscode column name difference between file years
    (2024-25 file uses 'Cdscode'; 2025-26 uses 'CdsCode').
    """
    with open(path, "r", encoding="latin1") as f:
        content = f.read().replace("\r\n", "\n").replace("\r", "\n")
    df = pd.read_csv(StringIO(content), sep="\t", dtype=str, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    # Normalise CdsCode column name (case varies by year)
    rename = {c: "Cdscode" for c in df.columns if c.lower() == "cdscode"}
    df = df.rename(columns=rename)
    return df


def get_year(year_field):
    """'2425' → 2025  (last two digits + 2000)"""
    s = str(year_field).strip()
    # Could be '2425' (four-digit) or '25' (two-digit)
    if len(s) >= 4:
        return 2000 + int(s[-2:])
    return 2000 + int(s)


def derive_year_from_filename(filename):
    """cbedsora24a.txt → 2025   cbedsora25a.txt → 2026"""
    name = os.path.basename(filename).lower()
    m = re.search(r"cbedsora(\d{2})a", name)
    if m:
        return 2000 + int(m.group(1)) + 1
    raise ValueError(
        f"Cannot derive year from filename '{filename}'.\n"
        "Expected format: cbedsora##a.txt (e.g. cbedsora24a.txt)"
    )


def make_rows(total_f, white_f, district_display, school_display, year):
    """Return a pair of output dicts: (Total row, Persons of Color row)."""
    total_i = round(total_f)
    poc_i   = round(total_f - white_f)
    rows = []
    # Total row
    rows.append({
        "District":           district_display,
        "School":             school_display,
        "Year":               year,
        "DemographicCategory": "Total",
        "Demographic":        "Total",
        "Result":             total_i,
        "Group_Total":        total_i,
    })
    # Persons of Color row
    rows.append({
        "District":           district_display,
        "School":             school_display,
        "Year":               year,
        "DemographicCategory": "Persons of Color",
        "Demographic":        "Ethnicity: Persons of Color",
        "Result":             poc_i,
        "Group_Total":        total_i,
    })
    return rows


def process():
    print("Loading source file...")
    df = load_source(SOURCE_FILE)

    # Derive year (use filename as authoritative source)
    year = derive_year_from_filename(SOURCE_FILE)
    print(f"Year (from filename): {year}")

    # ── Filter to Marin County (county = first 2 digits of Cdscode = 21) ──────
    df["_county"] = df["Cdscode"].str.strip().str[:2]
    df = df[df["_county"] == "21"].copy()

    # ── Filter to Paraprofessional descriptions only ──────────────────────────
    df = df[df["Description"].str.contains("Paraprofessionals", na=False)].copy()

    # ── Filter to Section A (staff data; exclude Rider Demographics section D) ─
    df = df[df["Section"].str.strip() == "A"].copy()

    # Coerce numeric columns
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    print(f"Filtered rows (Marin, Paraprofessionals): {len(df)}")

    # ── Summarise helper ──────────────────────────────────────────────────────
    def agg_group(subdf):
        return subdf["Total"].sum(), subdf["White"].sum()

    output_rows = []

    # ── School-level records (Level = S only) ─────────────────────────────────
    school_df = df[df["Level"].str.strip() == "S"].copy()
    for (cdscode, dist_name, sch_name), grp in school_df.groupby(
            ["Cdscode", "DistrictName", "SchoolName"]):
        total_f, white_f = agg_group(grp)
        if total_f == 0:
            continue
        district_display = DISTRICT_NAME_MAP.get(dist_name.strip(), dist_name.strip())
        school_display   = SCHOOL_NAME_MAP.get(sch_name.strip(), sch_name.strip())
        output_rows.extend(make_rows(total_f, white_f, district_display, school_display, year))

    # ── District "All Schools" records (sum S + D within each district) ───────
    for dist_name, grp in df.groupby("DistrictName"):
        total_f, white_f = agg_group(grp)
        if total_f == 0:
            continue
        district_display = DISTRICT_NAME_MAP.get(dist_name.strip(), dist_name.strip())
        output_rows.extend(make_rows(total_f, white_f, district_display, "All Schools", year))

    # ── Marin County aggregate (all rows) ─────────────────────────────────────
    total_f = df["Total"].sum()
    white_f = df["White"].sum()
    output_rows.extend(make_rows(total_f, white_f, "Marin County", "All Schools", year))

    # ── Assemble final output ─────────────────────────────────────────────────
    all_rows = pd.DataFrame(output_rows)

    output = pd.DataFrame({
        "public_datasets_id":  "",
        "Dataset":             "Staff",
        "District":            all_rows["District"],
        "School":              all_rows["School"],
        "Year":                all_rows["Year"].astype(int),
        "Indicator":           "Staff",
        "DemographicCategory": all_rows["DemographicCategory"],
        "Demographic":         all_rows["Demographic"],
        "ItemDescription":     "Paraeducators",
        "Result":              all_rows["Result"].astype(int),
        "Group_Total":         all_rows["Group_Total"].astype(int),
        "Active":              1,
    })

    output = output.sort_values(
        ["District", "School", "DemographicCategory"]
    ).reset_index(drop=True)

    return output


def compare_with_target(output, target_path):
    """Compare generated output with the Year=<year> paraeducator rows
    from v_staff_data.csv.  Reports exact matches and differences."""
    target = pd.read_csv(target_path, dtype=str)

    # Keep only Paraeducator rows for the relevant year
    year_val = str(output["Year"].iloc[0])
    target = target[
        (target["Dataset"] == "Staff") &
        (target["Indicator"] == "Staff") &
        (target["ItemDescription"] == "Paraeducators") &
        (target["Year"] == year_val)
    ].copy()

    compare_cols = [
        "Dataset", "District", "School", "Year", "Indicator",
        "DemographicCategory", "Demographic", "ItemDescription",
        "Result", "Group_Total",
    ]

    def normalize(df):
        df = df[compare_cols].copy()
        for col in ["Year", "Result", "Group_Total"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values(compare_cols, na_position="last").reset_index(drop=True)

    out_norm = normalize(output)
    tgt_norm = normalize(target)

    merged = tgt_norm.merge(out_norm, on=compare_cols, how="outer", indicator=True)
    only_target = merged[merged["_merge"] == "left_only"]
    only_output = merged[merged["_merge"] == "right_only"]

    print(f"\n=== COMPARISON RESULTS (Year {year_val}) ===")
    print(f"Target rows (from v_staff_data.csv): {len(tgt_norm)}")
    print(f"Output rows:                         {len(out_norm)}")
    print(f"Rows in target only (missing):       {len(only_target)}")
    print(f"Rows in output only (extra):         {len(only_output)}")

    if len(only_target) > 0:
        print("\nSAMPLE ROWS IN TARGET ONLY (missing from output):")
        print(only_target.drop("_merge", axis=1).head(30).to_string())
    if len(only_output) > 0:
        print("\nSAMPLE ROWS IN OUTPUT ONLY (extra):")
        print(only_output.drop("_merge", axis=1).head(30).to_string())

    return len(only_target) == 0 and len(only_output) == 0


# ── SQL generation ────────────────────────────────────────────────────────────
# Columns written to the SQL INSERT — excludes auto-increment id and
# LastUpdated (MySQL sets that automatically via DEFAULT CURRENT_TIMESTAMP).
SQL_INSERT_COLS = [
    "Dataset", "District", "School", "Year", "Indicator",
    "DemographicCategory", "Demographic", "ItemDescription",
    "Result", "Group_Total", "Active",
]
SQL_INT_COLS = {"Year", "Result", "Group_Total", "Active"}


def _sql_escape(v):
    if pd.isna(v) or str(v).strip() == "":
        return "NULL"
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _sql_int(v):
    if pd.isna(v) or str(v).strip() == "":
        return "NULL"
    try:
        return str(int(float(str(v))))
    except (ValueError, TypeError):
        return "NULL"


def write_sql(df, sql_path):
    """Write a phpMyAdmin-ready INSERT .sql file from the output DataFrame."""
    cols_sql = ", ".join(f"`{c}`" for c in SQL_INSERT_COLS)
    rows = []
    for _, row in df.iterrows():
        vals = [
            _sql_int(row.get(c, "")) if c in SQL_INT_COLS
            else _sql_escape(row.get(c, ""))
            for c in SQL_INSERT_COLS
        ]
        rows.append(f"  ({', '.join(vals)})")
    body = f"INSERT INTO `public_datasets` ({cols_sql}) VALUES\n"
    body += ",\n".join(rows) + ";"
    with open(sql_path, "w", encoding="utf-8") as f:
        f.write(body)


if __name__ == "__main__":
    output = process()
    print(f"\nTotal output rows: {len(output)}")

    if TARGET_FILE and os.path.exists(TARGET_FILE):
        print("\n--- Comparing with target ---")
        match = compare_with_target(output, TARGET_FILE)
        if match:
            print("\n✓ Output matches target exactly!")
        else:
            print("\n✗ Differences found — see above.")

    # Save CSV
    output.to_csv(OUTPUT_FILE, index=False)
    print(f"\nOutput saved to:  {OUTPUT_FILE}")

    # Save SQL (same path, .sql extension)
    sql_file = os.path.splitext(OUTPUT_FILE)[0] + ".sql"
    write_sql(output, sql_file)
    print(f"SQL file saved to: {sql_file}")

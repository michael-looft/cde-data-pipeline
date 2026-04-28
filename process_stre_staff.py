"""
Process CDE STRE (Statewide Teacher and Administrator Report) staff data
into public_datasets format.  Filters to Marin County (county code 21)
and produces one output row per school/district × staff type × demographic.

Usage:
  python process_stre_staff.py <source_file> [output_file] [target_file]

  source_file  — path to stre####.txt downloaded from CDE
                 (https://www.cde.ca.gov/ds/ad/filesstretextdata.asp)
                 Filename convention: stre{yy_start}{yy_end}.txt
                 e.g. stre2425.txt = 2024-25 data → Year 2025
  output_file  — (optional) path for the output CSV; defaults to
                 stre####_import.csv in the current working directory
  target_file  — (optional) v_staff_data.csv dump for comparison mode

Example:
  python process_stre_staff.py stre2425.txt
  python process_stre_staff.py stre2425.txt stre2425_import.csv v_staff_data.csv

Source file columns used:
  Academic Year, Aggregate Level, County Code, County Name,
  District Name, School Name, Charter School, DASS,
  Staff Type, School Grade Span, Staff Gender,
  Total Staff, African American, American Indian or Alaska Native,
  Asian, Filipino, Hispanic or Latino, Pacific Islander, White,
  Two or More Races, Not Reported

Business rules:
  - Filter to Marin County (County Code == '21')
  - Filter to Staff Gender == 'ALL' (gender-aggregated rows only)
  - School-level records:
      Aggregate Level == 'S', School Name != 'District Office'
      (all school types: charter, DASS, regular)
  - District "All Schools" records:
      Aggregate Level == 'D', Charter School == 'ALL',
      DASS == 'ALL', School Grade Span == 'ALL'
      (top-level district aggregate; already includes district office)
  - Marin County "All Schools":
      Aggregate Level == 'C', Charter School == 'ALL',
      DASS == 'ALL', School Grade Span == 'ALL'
  - One row per school/district × staff type combination
  - Skip rows where Total Staff == 0
  - Total = Total Staff (rounded to int)
  - Persons of Color = Total - White (rounded to int)
  - Group_Total = Total for each staff type row
  - Staff types: ADM→Administrators, ALL→All, OTH→Non-Instructional Support,
                 PSV→Pupil Services, TCH→Teachers
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
    SOURCE_FILE = os.path.join(os.path.dirname(__file__), "stre2425.txt")

if len(sys.argv) >= 3:
    OUTPUT_FILE = sys.argv[2]
else:
    src_name = os.path.basename(SOURCE_FILE)
    m = re.search(r"stre(\d{4})", src_name, re.IGNORECASE)
    if m:
        OUTPUT_FILE = f"stre{m.group(1)}_import.csv"
    else:
        OUTPUT_FILE = os.path.splitext(src_name)[0] + "_import.csv"

TARGET_FILE = None
if len(sys.argv) >= 4:
    TARGET_FILE = sys.argv[3]


# ── Staff type → ItemDescription mapping ─────────────────────────────────────
STAFF_TYPE_MAP = {
    "ADM": "Administrators",
    "ALL": "All",
    "OTH": "Non-Instructional Support",
    "PSV": "Pupil Services",
    "TCH": "Teachers",
}

# ── District name mapping (STRE source → public_datasets display) ─────────────
DISTRICT_NAME_MAP = {
    "Bolinas-Stinson Union":            "Bolinas-Stinson Union School District",
    "Kentfield Elementary":             "Kentfield Elementary School District",
    "Laguna Joint Elementary":          "Laguna Joint Elementary School District",
    "Lagunitas Elementary":             "Lagunitas Elementary School District",
    "Larkspur-Corte Madera":            "Larkspur-Corte Madera",
    "Marin County Office of Education": "Marin County Office Of Education",
    "Mill Valley Elementary":           "Mill Valley Elementary School District",
    "Miller Creek Elementary":          "Miller Creek Elementary School District",
    "Nicasio":                          "Nicasio School District",
    "Novato Unified":                   "Novato Unified School District",
    "Reed Union Elementary":            "Reed Union Elementary School District",
    "Ross Elementary":                  "Ross Elementary School District",
    "Ross Valley Elementary":           "Ross Valley Elementary School District",
    "San Rafael City Elementary":       "San Rafael City Elementary",
    "San Rafael City High":             "San Rafael City High",
    "Sausalito Marin City":             "Sausalito Marin City School District",
    "Shoreline Unified":                "Shoreline Unified School District",
    "Tamalpais Union High":             "Tamalpais Union High School District",
}

# ── School name remapping (STRE SchoolName → display name) ───────────────────
# Add entries here if CDE renames a school between years.
SCHOOL_NAME_MAP = {
    # Example: "Old CDE Name": "Dashboard Display Name",
}

NUMERIC_COLS = [
    "Total Staff", "African American", "American Indian or Alaska Native",
    "Asian", "Filipino", "Hispanic or Latino", "Pacific Islander",
    "White", "Two or More Races", "Not Reported",
]


def load_source(path):
    """Load STRE text file, stripping Windows CRs."""
    with open(path, "r", encoding="latin1") as f:
        content = f.read().replace("\r\n", "\n").replace("\r", "\n")
    df = pd.read_csv(StringIO(content), sep="\t", dtype=str, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    return df


def derive_year_from_filename(filename):
    """stre2425.txt → 2025   stre2526.txt → 2026

    The filename encodes the two-digit start and end of the school year.
    The Year stored in public_datasets is the end year (e.g. 2024-25 → 2025).
    """
    name = os.path.basename(filename).lower()
    m = re.search(r"stre(\d{2})(\d{2})", name)
    if m:
        return 2000 + int(m.group(2))
    raise ValueError(
        f"Cannot derive year from filename '{filename}'.\n"
        "Expected format: stre####.txt (e.g. stre2425.txt)"
    )


def make_rows(total_f, white_f, not_rep_f, all_total_f,
              district_display, school_display, item_description, year):
    """Return a pair of output dicts: (Total row, Persons of Color row).

    Business rules for STRE:
      - PoC = Total - White - Not Reported  (Two or More Races counts as PoC)
      - PoC Group_Total = Total - Not Reported  (denominator excludes Not Reported)
      - Total Group_Total = the ALL-staff-type total for this entity
    """
    total_i   = round(total_f)
    poc_i     = round(total_f - white_f - not_rep_f)
    poc_denom = round(total_f - not_rep_f)
    all_tot_i = round(all_total_f)
    rows = []
    rows.append({
        "District":            district_display,
        "School":              school_display,
        "Year":                year,
        "ItemDescription":     item_description,
        "DemographicCategory": "Total",
        "Demographic":         "Total",
        "Result":              total_i,
        "Group_Total":         all_tot_i,
    })
    rows.append({
        "District":            district_display,
        "School":              school_display,
        "Year":                year,
        "ItemDescription":     item_description,
        "DemographicCategory": "Persons of Color",
        "Demographic":         "Ethnicity: Persons of Color",
        "Result":              poc_i,
        "Group_Total":         poc_denom,
    })
    return rows


def _agg(grp):
    """Aggregate one STRE group → (total, white, not_reported)."""
    return (
        grp["Total Staff"].sum(),
        grp["White"].sum(),
        grp["Not Reported"].sum(),
    )


def _build_entity_rows(entity_df, district_display, school_display, year):
    """Build all staff-type rows for a single school/district/county entity.

    entity_df must already be filtered to the correct Aggregate Level /
    Charter / DASS / GS / Gender constraints for this entity.
    Looks up the ALL total first, then emits rows for each staff type.
    """
    rows = []
    # Build per-staff-type aggregates
    by_type = {}
    for staff_type, grp in entity_df.groupby("Staff Type"):
        staff_type = staff_type.strip()
        if STAFF_TYPE_MAP.get(staff_type) is None:
            continue
        by_type[staff_type] = _agg(grp)

    # ALL total is the Group_Total denominator for Total rows
    all_total_f = by_type.get("ALL", (0, 0, 0))[0]

    for staff_type, (total_f, white_f, not_rep_f) in by_type.items():
        if total_f == 0:
            continue
        item_desc = STAFF_TYPE_MAP[staff_type]
        rows.extend(
            make_rows(total_f, white_f, not_rep_f, all_total_f,
                      district_display, school_display, item_desc, year)
        )
    return rows


def process():
    print("Loading source file...")
    df = load_source(SOURCE_FILE)

    year = derive_year_from_filename(SOURCE_FILE)
    print(f"Year (from filename): {year}")

    # ── Filter to Marin County ───────────────────────────────────────────────
    df = df[df["County Code"].str.strip() == "21"].copy()

    # ── Filter to gender-aggregated rows only ────────────────────────────────
    df = df[df["Staff Gender"].str.strip() == "ALL"].copy()

    # Coerce numeric columns
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    print(f"Marin rows (Gender=ALL): {len(df)}")

    output_rows = []

    # ── School-level records ─────────────────────────────────────────────────
    # Aggregate Level = S, exclude "District Office" pseudo-schools
    school_df = df[
        (df["Aggregate Level"].str.strip() == "S") &
        (df["School Name"].str.strip() != "District Office")
    ].copy()

    for (dist_name, sch_name), grp in school_df.groupby(
            ["District Name", "School Name"]):
        district_display = DISTRICT_NAME_MAP.get(dist_name.strip(), dist_name.strip())
        school_display   = SCHOOL_NAME_MAP.get(sch_name.strip(),    sch_name.strip())
        output_rows.extend(
            _build_entity_rows(grp, district_display, school_display, year)
        )

    print(f"School-level output rows: {len(output_rows)}")

    # ── District "All Schools" records ───────────────────────────────────────
    # Aggregate Level = D, Charter = ALL, DASS = ALL, School Grade Span = ALL
    district_df = df[
        (df["Aggregate Level"].str.strip() == "D") &
        (df["Charter School"].str.strip() == "ALL") &
        (df["DASS"].str.strip() == "ALL") &
        (df["School Grade Span"].str.strip() == "ALL")
    ].copy()

    before = len(output_rows)
    for dist_name, grp in district_df.groupby("District Name"):
        district_display = DISTRICT_NAME_MAP.get(dist_name.strip(), dist_name.strip())
        output_rows.extend(
            _build_entity_rows(grp, district_display, "All Schools", year)
        )

    print(f"District-level output rows: {len(output_rows) - before}")

    # ── Marin County "All Schools" ───────────────────────────────────────────
    # Aggregate Level = C, Charter = ALL, DASS = ALL, School Grade Span = ALL
    county_df = df[
        (df["Aggregate Level"].str.strip() == "C") &
        (df["Charter School"].str.strip() == "ALL") &
        (df["DASS"].str.strip() == "ALL") &
        (df["School Grade Span"].str.strip() == "ALL")
    ].copy()

    before = len(output_rows)
    output_rows.extend(
        _build_entity_rows(county_df, "Marin County", "All Schools", year)
    )

    print(f"County-level output rows: {len(output_rows) - before}")

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
        "ItemDescription":     all_rows["ItemDescription"],
        "Result":              all_rows["Result"].astype(int),
        "Group_Total":         all_rows["Group_Total"].astype(int),
        "Active":              1,
    })

    output = output.sort_values(
        ["ItemDescription", "District", "School", "DemographicCategory"]
    ).reset_index(drop=True)

    return output


def compare_with_target(output, target_path):
    """Compare generated output with v_staff_data.csv Staff rows for this year.
    Excludes Paraeducators (handled by process_paraeducators.py)."""
    target = pd.read_csv(target_path, dtype=str)

    year_val = str(output["Year"].iloc[0])
    target = target[
        (target["Dataset"] == "Staff") &
        (target["Indicator"] == "Staff") &
        (target["ItemDescription"] != "Paraeducators") &
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
    print(f"Target rows (from v_staff_data.csv, excl Paraeducators): {len(tgt_norm)}")
    print(f"Output rows:                                              {len(out_norm)}")
    print(f"Rows in target only (missing from output):               {len(only_target)}")
    print(f"Rows in output only (extra):                             {len(only_output)}")

    if len(only_target) > 0:
        print("\nROWS IN TARGET ONLY (missing from output):")
        print(only_target.drop("_merge", axis=1).to_string())
    if len(only_output) > 0:
        print("\nROWS IN OUTPUT ONLY (extra):")
        print(only_output.drop("_merge", axis=1).to_string())

    return len(only_target) == 0 and len(only_output) == 0


# ── SQL generation ────────────────────────────────────────────────────────────
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

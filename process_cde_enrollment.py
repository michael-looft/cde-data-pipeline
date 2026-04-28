"""
CDE Annual Enrollment Data Processor
=====================================
Transforms the California Department of Education's Census Day Enrollment
data file into the internal format used for Marin County nonprofit reporting.

SOURCE DATA:
  https://www.cde.ca.gov/ds/ad/filesenrcensus.asp
  Download the latest CDEnrollXXXX.txt file and save it locally.

USAGE:
  python process_cde_enrollment.py --input CDEnroll2526.txt --year 2026 --output v_enrollment_2026.csv

  Or edit the CONFIG section below and run:
  python process_cde_enrollment.py

NOTES:
  - "Students of Color" = all non-White, non-Not Reported students
    (RE_I + RE_A + RE_F + RE_B + RE_H + RE_P + RE_T)
  - Filipino (RE_F) is reported under "Ethnicity: Asian" in two rows
    (matching the source format of the existing data)
  - Suppressed values (*) are excluded from Students of Color sums
  - Script includes State of California, Marin County aggregate,
    all Marin district aggregates, and all individual Marin schools
"""

import csv
import sys
import argparse
from collections import defaultdict

# ─────────────────────────────────────────────────────────────────
# CONFIG — edit these defaults or pass as command-line arguments
# ─────────────────────────────────────────────────────────────────
DEFAULT_INPUT       = "CDEnroll2425.txt"      # Path to downloaded CDE file
DEFAULT_OUTPUT      = "v_enrollment_2025.csv" # Output CSV path
DEFAULT_YEAR        = "2025"                  # Label year (2024-25 data → "2025")
DEFAULT_COUNTY_CODE = "21"                    # Marin County FIPS code

# ─────────────────────────────────────────────────────────────────
# DISTRICT NAME MAPPING
# CDE uses shortened district names; map to full names used in database
# ─────────────────────────────────────────────────────────────────
DISTRICT_NAME_MAP = {
    "Bolinas-Stinson Union":          "Bolinas-Stinson Union School District",
    "Kentfield Elementary":           "Kentfield Elementary School District",
    "Laguna Joint Elementary":        "Laguna Joint Elementary School District",
    "Lagunitas Elementary":           "Lagunitas Elementary School District",
    "Larkspur-Corte Madera":          "Larkspur-Corte Madera",          # no suffix
    "Marin County Office of Education": "Marin County Office Of Education",
    "Mill Valley Elementary":         "Mill Valley Elementary School District",
    "Miller Creek Elementary":        "Miller Creek Elementary School District",
    "Nicasio":                        "Nicasio School District",
    "Novato Unified":                 "Novato Unified School District",
    "Reed Union Elementary":          "Reed Union Elementary School District",
    "Ross Elementary":                "Ross Elementary School District",
    "Ross Valley Elementary":         "Ross Valley Elementary School District",
    "San Rafael City Elementary":     "San Rafael City Elementary",      # no suffix
    "San Rafael City High":           "San Rafael City High",            # no suffix
    "Sausalito Marin City":           "Sausalito Marin City School District",
    "Shoreline Unified":              "Shoreline Unified School District",
    "Tamalpais Union High":           "Tamalpais Union High School District",
}

# ─────────────────────────────────────────────────────────────────
# REPORTING CATEGORY → (DemographicCategory, Demographic)
# ─────────────────────────────────────────────────────────────────
# Note: RE_F (Filipino) and RE_A (Asian) both map to "Ethnicity: Asian"
# producing two rows with that label — this matches the source data format.
REPORTING_MAP = {
    # Age Range
    "AR_03":   ("AgeRange", "AgeRange: Children enrolled in K-12 grades who are 0 to 3 years old"),
    "AR_0418": ("AgeRange", "AgeRange: Students enrolled in K-12 grades who are 4 to 18 years old"),
    "AR_1922": ("AgeRange", "AgeRange: Continuing students enrolled in K-12 grades who are 19 to 22 years old"),
    "AR_2329": ("AgeRange", "AgeRange: Non-traditional adult students enrolled in K-12 grades who are 23 to 29 years old"),
    "AR_3039": ("AgeRange", "AgeRange: Non-traditional adult students enrolled in K-12 grades who are 30 to 39 years o"),
    "AR_4049": ("AgeRange", "AgeRange: Non-traditional adult students enrolled in K-12 grades who are 40 to 49 years ol"),
    "AR_50P":  ("AgeRange", "AgeRange: Non-traditional adult students enrolled in K-12 grades who are 50 or more years old"),
    # English Language Acquisition Status
    "ELAS_ADEL": ("ELAS", "ELAS: Adult English Learner"),
    "ELAS_EL":   ("ELAS", "ELAS: English Learner (Duplicative of SG_EL)"),
    "ELAS_EO":   ("ELAS", "ELAS: English Only"),
    "ELAS_IFEP": ("ELAS", "ELAS: Initial Fluent English Proficient"),
    "ELAS_MISS": ("ELAS", "ELAS: Missing"),
    "ELAS_RFEP": ("ELAS", "ELAS: Reclassified Fluent English Proficient"),
    "ELAS_TBD":  ("ELAS", "ELAS: To Be Determined"),
    # Gender
    "GN_F": ("Gender", "Gender: Female"),
    "GN_M": ("Gender", "Gender: Male"),
    "GN_X": ("Gender", "Gender: Non-Binary Gender"),
    # Race / Ethnicity
    # RE_F (Filipino) and RE_A (Asian) both appear as "Ethnicity: Asian"
    "RE_I": ("Ethnicity", "Ethnicity: American Indian or Native Alaskan"),
    "RE_F": ("Ethnicity", "Ethnicity: Asian"),   # Filipino → Asian row 1
    "RE_A": ("Ethnicity", "Ethnicity: Asian"),   # Asian    → Asian row 2
    "RE_B": ("Ethnicity", "Ethnicity: Black or African American"),
    "RE_H": ("Ethnicity", "Ethnicity: Hispanic/Latino"),
    "RE_P": ("Ethnicity", "Ethnicity: Native Hawaiian or Pacific Islander"),
    "RE_T": ("Ethnicity", "Ethnicity: Two or More Races"),
    "RE_D": ("Ethnicity", "Ethnicity: Unreported"),
    "RE_W": ("Ethnicity", "Ethnicity: White"),
    # Student Groups
    "SG_DS": ("Disability", "Disability: Disabled"),
    "SG_SD": ("Income",     "Income: Economically Disadvantaged"),
    "SG_EL": ("Language",   "Language: English Language Learner"),
    "SG_MG": ("Other",      "Other: Migrant Children"),
    "SG_FS": ("Other",      "Other: Foster Youth"),
    "SG_HM": ("Other",      "Other: Homeless Youth"),
    # Total
    "TA": ("Total", "Total"),
}

# ─────────────────────────────────────────────────────────────────
# GRADE COLUMN ORDER
# Each CDE row is exploded into one output row per grade level
# ─────────────────────────────────────────────────────────────────
GRADE_COLS = [
    ("TOTAL_ENR", "Enrollment All"),
    ("GR_TK",     "Enrollment TK"),
    ("GR_KN",     "Enrollment K"),
    ("GR_01",     "Enrollment 1"),
    ("GR_02",     "Enrollment 2"),
    ("GR_03",     "Enrollment 3"),
    ("GR_04",     "Enrollment 4"),
    ("GR_05",     "Enrollment 5"),
    ("GR_06",     "Enrollment 6"),
    ("GR_07",     "Enrollment 7"),
    ("GR_08",     "Enrollment 8"),
    ("GR_09",     "Enrollment 9"),
    ("GR_10",     "Enrollment 10"),
    ("GR_11",     "Enrollment 11"),
    ("GR_12",     "Enrollment 12"),
]

# Race/Ethnicity codes that count toward Students of Color
# (all non-White, non-Not Reported)
SOC_CATEGORIES = {"RE_I", "RE_A", "RE_F", "RE_B", "RE_H", "RE_P", "RE_T"}


def parse_args():
    parser = argparse.ArgumentParser(description="Transform CDE enrollment data into internal format.")
    parser.add_argument("--input",  default=DEFAULT_INPUT,       help="Path to CDE CDEnrollXXXX.txt file")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,      help="Output CSV file path")
    parser.add_argument("--year",   default=DEFAULT_YEAR,        help="Label year (e.g. 2025 for 2024-25 data)")
    parser.add_argument("--county", default=DEFAULT_COUNTY_CODE, help="Two-digit county code (default: 21 for Marin)")
    return parser.parse_args()


def get_dist_school(row, county_code):
    """Return (district_name, school_name) for an output row."""
    lvl = row["AggregateLevel"]
    if lvl == "T":
        return "State of California", "All Schools"
    if lvl == "C":
        county = row["CountyName"].strip()
        # CDE dropped the " County" suffix starting 2025-26; internal DB
        # expects e.g. "Marin County" (not "Marin"). Normalize both shapes.
        if county and not county.lower().endswith("county"):
            county = f"{county} County"
        return county, "All Schools"
    raw_dist = (row.get("DistrictName") or "").strip()
    dist = DISTRICT_NAME_MAP.get(raw_dist, raw_dist)
    if lvl == "D":
        return dist, "All Schools"
    # School level
    school = (row.get("SchoolName") or "").strip()
    return dist, school


def should_include(row, county_code):
    """Return True if this row should be included in the output."""
    lvl     = row["AggregateLevel"]
    cc      = row["CountyCode"]
    charter = row["Charter"]

    # State-level aggregate (Charter = ALL only)
    if lvl == "T" and charter == "ALL":
        return True
    # Marin county-level aggregate
    if cc == county_code and lvl == "C" and charter == "ALL":
        return True
    # Marin district-level aggregates (combined charter + non-charter)
    if cc == county_code and lvl == "D" and charter == "ALL":
        return True
    # All Marin individual schools (charter = Y or N)
    if cc == county_code and lvl == "S":
        return True
    return False


def process(input_path, output_path, year, county_code):
    print(f"Reading {input_path}...")

    # ── Pass 1: collect relevant rows ──────────────────────────────
    relevant = []
    with open(input_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if should_include(row, county_code):
                relevant.append(row)

    print(f"  Relevant rows: {len(relevant)}")

    # ── Pass 2: build group totals (TA rows) ───────────────────────
    # Key: (district, school, ItemDescription)
    group_totals = {}
    for row in relevant:
        if row["ReportingCategory"] != "TA":
            continue
        dist, sch = get_dist_school(row, county_code)
        for gr_col, item_desc in GRADE_COLS:
            val = row.get(gr_col, "0")
            group_totals[(dist, sch, item_desc)] = val

    # ── Pass 3: build Students of Color totals ────────────────────
    # Sum all SOC race/ethnicity categories per (district, school, grade)
    soc_totals = defaultdict(int)
    for row in relevant:
        rc = row["ReportingCategory"]
        if rc not in SOC_CATEGORIES:
            continue
        dist, sch = get_dist_school(row, county_code)
        for gr_col, item_desc in GRADE_COLS:
            val = row.get(gr_col, "")
            if val == "*" or not val:
                continue
            try:
                soc_totals[(dist, sch, item_desc)] += int(val)
            except ValueError:
                pass

    # ── Pass 4: generate output rows ──────────────────────────────
    output_rows = []

    for row in relevant:
        rc = row["ReportingCategory"]
        if rc not in REPORTING_MAP:
            continue

        demo_cat, demo = REPORTING_MAP[rc]
        dist, sch = get_dist_school(row, county_code)

        for gr_col, item_desc in GRADE_COLS:
            result    = row.get(gr_col, "0")
            grp_total = group_totals.get((dist, sch, item_desc), "0")
            output_rows.append({
                "Dataset":            "Enrollment",
                "District":           dist,
                "School":             sch,
                "Year":               year,
                "Indicator":          "Enrollment",
                "DemographicCategory": demo_cat,
                "Demographic":        demo,
                "ItemDescription":    item_desc,
                "Result":             result,
                "Group_Total":        grp_total,
                "Active":             "1",
            })

        # After the White row (RE_W), insert Students of Color rows
        if rc == "RE_W":
            for gr_col, item_desc in GRADE_COLS:
                soc_val   = soc_totals.get((dist, sch, item_desc), 0)
                grp_total = group_totals.get((dist, sch, item_desc), "0")
                output_rows.append({
                    "Dataset":            "Enrollment",
                    "District":           dist,
                    "School":             sch,
                    "Year":               year,
                    "Indicator":          "Enrollment",
                    "DemographicCategory": "Ethnicity",
                    "Demographic":        "Ethnicity: Students of Color",
                    "ItemDescription":    item_desc,
                    "Result":             str(soc_val),
                    "Group_Total":        grp_total,
                    "Active":             "1",
                })

    # ── Write output CSV ──────────────────────────────────────────
    fieldnames = [
        "Dataset", "District", "School", "Year",
        "Indicator", "DemographicCategory", "Demographic", "ItemDescription",
        "Result", "Group_Total", "Active",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"  Output rows written: {len(output_rows)}")
    print(f"  Output file: {output_path}")

    # ── Quick verification summary ────────────────────────────────
    print("\nVerification spot-checks:")
    checks = [
        ("Marin County", "All Schools", "Total",     "Total",                          "Enrollment All"),
        ("Marin County", "All Schools", "Ethnicity",  "Ethnicity: White",               "Enrollment All"),
        ("Marin County", "All Schools", "Ethnicity",  "Ethnicity: Students of Color",   "Enrollment All"),
        ("Marin County", "All Schools", "Ethnicity",  "Ethnicity: Hispanic/Latino",     "Enrollment All"),
        ("State of California", "All Schools", "Total", "Total",                        "Enrollment All"),
    ]
    for dist, sch, cat, demo, item in checks:
        match = next((r for r in output_rows
                      if r["District"] == dist and r["School"] == sch
                      and r["DemographicCategory"] == cat
                      and r["Demographic"] == demo
                      and r["ItemDescription"] == item), None)
        val = match["Result"] if match else "NOT FOUND"
        print(f"  {dist} | {demo} | {item}: {val}")

    return output_rows


if __name__ == "__main__":
    args = parse_args()
    process(
        input_path  = args.input,
        output_path = args.output,
        year        = args.year,
        county_code = args.county,
    )

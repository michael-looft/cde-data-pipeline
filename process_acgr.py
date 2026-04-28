"""
Process CDE ACGR data file into public_datasets format.
Filters to Marin County (county 21) + State of California.
DASS = All, CharterSchool = All only.

Usage:
  python process_acgr.py <source_file> [output_file]

  source_file  — path to acgrYY.txt downloaded from CDE
  output_file  — (optional) path for the output CSV; defaults to
                 acgrYY_import.csv in the same folder as source_file

Example:
  python process_acgr.py acgr26.txt
"""

import sys
import os
import pandas as pd
import numpy as np

# ── Resolve paths from command-line args or defaults ─────────────────────────
if len(sys.argv) >= 2:
    SOURCE_FILE = sys.argv[1]
else:
    SOURCE_FILE = os.path.join(os.path.dirname(__file__), "acgr25.txt")

if len(sys.argv) >= 3:
    OUTPUT_FILE = sys.argv[2]
else:
    base = os.path.splitext(SOURCE_FILE)[0]
    OUTPUT_FILE = base + "_import.csv"

TARGET_FILE = None  # only used when running in verify mode (pass as 4th arg)
if len(sys.argv) >= 4:
    TARGET_FILE = sys.argv[3]

# ── Reporting category → (DemographicCategory, Demographic) ──────────────────
CATEGORY_MAP = {
    "GF": ("Gender",     "Gender: Female"),
    "GM": ("Gender",     "Gender: Male"),
    "GX": ("Gender",     "Gender: Missing"),
    "RA": ("Ethnicity",  "Ethnicity: Asian"),
    "RB": ("Ethnicity",  "Ethnicity: Black or African American"),
    "RD": ("Ethnicity",  "Ethnicity: Not Reported"),
    "RF": ("Ethnicity",  "Ethnicity: Filipino"),
    "RH": ("Ethnicity",  "Ethnicity: Hispanic/Latino"),
    "RI": ("Ethnicity",  "Ethnicity: American Indian or Native Alaskan"),
    "RP": ("Ethnicity",  "Ethnicity: Native Hawaiian or Pacific Islander"),
    "RT": ("Ethnicity",  "Ethnicity: Two or More Races"),
    "RW": ("Ethnicity",  "Ethnicity: White"),
    "SD": ("Disability", "Disability: Disabled"),
    "SE": ("Language",   "Language: English Language Learner"),
    "SF": ("Other",      "Other: Foster Youth"),
    "SH": ("Other",      "Other: Homeless Youth"),
    "SM": ("Other",      "Other: Migrant Children"),
    "SS": ("Income",     "Income: Economically Disadvantaged"),
    "TA": ("Total",      "Total"),
}

# ── District name mapping (source → display) ─────────────────────────────────
DISTRICT_NAME_MAP = {
    "Marin County Office of Education": "Marin County Office Of Education",
    "Novato Unified":                   "Novato Unified School District",
    "San Rafael City High":             "San Rafael City High",
    "Tamalpais Union High":             "Tamalpais Union High School District",
    "Shoreline Unified":                "Shoreline Unified School District",
}

# ── Schools to exclude at S-level ─────────────────────────────────────────────
EXCLUDE_SCHOOL_CODES = {"0000000", "0000001"}  # District Office, Nonpublic
# MCOE schools, Marin Oaks High, continuation schools are all DASS=Yes → excluded by DASS filter

def load_source(path):
    """Load ACGR text file, stripping Windows CRs."""
    with open(path, "r", encoding="latin1") as f:
        content = f.read().replace("\r\n", "\n").replace("\r", "\n")
    from io import StringIO
    df = pd.read_csv(StringIO(content), sep="\t", dtype=str, low_memory=False)
    return df

def clean_value(v):
    """Return float or None; treat '*' and blank as None."""
    if pd.isna(v) or str(v).strip() in ("*", "", "–", "-"):
        return None
    try:
        return float(str(v).strip())
    except ValueError:
        return None

def get_year(academic_year):
    """'2024-25' → 2025"""
    return int(str(academic_year).strip().split("-")[1]) + 2000

def get_district_and_school(row):
    """Return (District display name, School display name) for a row."""
    agg   = row["AggregateLevel"].strip()
    dname = str(row["DistrictName"]).strip() if not pd.isna(row["DistrictName"]) else ""
    sname = str(row["SchoolName"]).strip()  if not pd.isna(row["SchoolName"])  else ""

    if agg == "T":
        return "State of California", "All Schools"
    elif agg == "C":
        return "Marin County", "All Schools"
    elif agg == "D":
        district = DISTRICT_NAME_MAP.get(dname, dname)
        return district, "All Schools"
    elif agg == "S":
        district = DISTRICT_NAME_MAP.get(dname, dname)
        return district, sname
    return dname, sname

def process():
    print("Loading source file...")
    df = load_source(SOURCE_FILE)
    df.columns = [c.strip() for c in df.columns]

    # ── Geography filter: county 21 OR state (T) ───────────────────────────────
    county21 = df["CountyCode"].str.strip() == "21"
    state_t  = df["AggregateLevel"].str.strip() == "T"
    df = df[county21 | state_t].copy()

    agg = df["AggregateLevel"].str.strip()

    # ── For C/D/T aggregate levels: CharterSchool=All AND DASS=All ─────────────
    # (These rows aggregate across all charter/DASS statuses)
    agg_mask = agg.isin(["C", "D", "T"])
    agg_keep = agg_mask & (df["CharterSchool"].str.strip() == "All") & (df["DASS"].str.strip() == "All")

    # ── For S-level (individual schools): DASS=No (non-DASS schools only) ──────
    # Schools with DASS=Yes are direct-aid/special programs (juvenile court, etc.)
    # Also exclude District Office (0000000) and Nonpublic (0000001) school codes
    s_mask = agg == "S"
    s_keep = (s_mask
              & (df["DASS"].str.strip() == "No")
              & (~df["SchoolCode"].str.strip().isin(EXCLUDE_SCHOOL_CODES)))

    df = df[agg_keep | s_keep].copy()

    print(f"Rows after filtering: {len(df)}")

    # ── Parse numeric columns ─────────────────────────────────────────────────
    cohort_col  = "Regular HS Diploma Graduates (Count)"
    met_col     = "Met UC/CSU Grad Req's (Count)"

    df["_cohort"] = df[cohort_col].apply(clean_value)
    df["_met"]    = df[met_col].apply(clean_value)
    df["_rc"]     = df["ReportingCategory"].str.strip()

    # ── Derive display fields ──────────────────────────────────────────────────
    df[["_district", "_school"]] = df.apply(
        lambda r: pd.Series(get_district_and_school(r)), axis=1
    )
    df["_year"] = df["AcademicYear"].apply(get_year)

    # ── Build Group_Total lookup: Total (TA) cohort per (district, school, year) ─
    ta_rows = df[df["_rc"] == "TA"][["_district", "_school", "_year", "_cohort"]].copy()
    ta_rows = ta_rows.rename(columns={"_cohort": "_group_total"})
    df = df.merge(ta_rows, on=["_district", "_school", "_year"], how="left")

    # ── Build Students of Color and Not Economically Disadvantaged ───────────
    # Pivot to get Total and White side-by-side for derived calculations
    pivot_cohort = df.pivot_table(
        index=["_district", "_school", "_year", "_group_total"],
        columns="_rc", values="_cohort", aggfunc="first"
    ).reset_index()
    pivot_met = df.pivot_table(
        index=["_district", "_school", "_year"],
        columns="_rc", values="_met", aggfunc="first"
    ).reset_index()

    derived_rows = []

    def val(series, col):
        """Get float value or None from a pivot series column."""
        v = series.get(col, None)
        return None if (v is None or (isinstance(v, float) and pd.isna(v))) else float(v)

    for _, pr in pivot_cohort.iterrows():
        dist  = pr["_district"]
        sch   = pr["_school"]
        yr    = pr["_year"]
        gt    = pr.get("_group_total", None)

        ta_c  = val(pr, "TA")
        rw_c  = val(pr, "RW")
        ss_c  = val(pr, "SS")

        # Get met values from pivot_met
        pm = pivot_met[
            (pivot_met["_district"] == dist) &
            (pivot_met["_school"]   == sch)  &
            (pivot_met["_year"]     == yr)
        ]
        ta_m = val(pm.iloc[0], "TA") if len(pm) > 0 else None
        rw_m = val(pm.iloc[0], "RW") if len(pm) > 0 else None
        ss_m = val(pm.iloc[0], "SS") if len(pm) > 0 else None

        # Students of Color = Total - White
        # When White is suppressed (*), treat as 0 (business rule: all students are SoC)
        if ta_c is not None:
            soc_cohort = ta_c - (rw_c if rw_c is not None else 0)
            derived_rows.append({
                "_district": dist, "_school": sch, "_year": yr,
                "_group_total": gt,
                "DemographicCategory": "Ethnicity",
                "Demographic": "Ethnicity: Students of Color",
                "ItemDescription": "Number of Students in Cohort",
                "Result": soc_cohort
            })
        if ta_m is not None:
            soc_met = ta_m - (rw_m if rw_m is not None else 0)
            derived_rows.append({
                "_district": dist, "_school": sch, "_year": yr,
                "_group_total": gt,
                "DemographicCategory": "Ethnicity",
                "Demographic": "Ethnicity: Students of Color",
                "ItemDescription": "Number of Students Meeting Outcome",
                "Result": soc_met
            })

        # Not Economically Disadvantaged = Total - Economically Disadvantaged
        # Only calculate when both TA and SS are non-suppressed
        if ta_c is not None and ss_c is not None:
            ned_cohort = ta_c - ss_c
            derived_rows.append({
                "_district": dist, "_school": sch, "_year": yr,
                "_group_total": gt,
                "DemographicCategory": "Income",
                "Demographic": "Income: Not Economically Disadvantaged",
                "ItemDescription": "Number of Students in Cohort",
                "Result": ned_cohort
            })
        if ta_m is not None and ss_m is not None:
            ned_met = ta_m - ss_m
            derived_rows.append({
                "_district": dist, "_school": sch, "_year": yr,
                "_group_total": gt,
                "DemographicCategory": "Income",
                "Demographic": "Income: Not Economically Disadvantaged",
                "ItemDescription": "Number of Students Meeting Outcome",
                "Result": ned_met
            })

    # ── Build base rows from mapped categories ─────────────────────────────────
    base_rows = []
    for _, row in df.iterrows():
        rc = row["_rc"]
        if rc not in CATEGORY_MAP:
            continue
        dem_cat, dem = CATEGORY_MAP[rc]

        cohort = row["_cohort"]
        met    = row["_met"]
        gt     = row.get("_group_total", None)

        if cohort is not None and not pd.isna(cohort):
            base_rows.append({
                "_district": row["_district"], "_school": row["_school"],
                "_year": row["_year"], "_group_total": gt,
                "DemographicCategory": dem_cat, "Demographic": dem,
                "ItemDescription": "Number of Students in Cohort",
                "Result": cohort
            })
        if met is not None and not pd.isna(met):
            base_rows.append({
                "_district": row["_district"], "_school": row["_school"],
                "_year": row["_year"], "_group_total": gt,
                "DemographicCategory": dem_cat, "Demographic": dem,
                "ItemDescription": "Number of Students Meeting Outcome",
                "Result": met
            })

    # ── Combine and assemble final output ─────────────────────────────────────
    all_rows = pd.DataFrame(base_rows + derived_rows)

    output = pd.DataFrame({
        "public_datasets_id": "",
        "Dataset":            "Milestone",
        "District":           all_rows["_district"],
        "School":             all_rows["_school"],
        "Year":               all_rows["_year"].astype(int),
        "Indicator":          "College Readiness",
        "DemographicCategory": all_rows["DemographicCategory"],
        "Demographic":        all_rows["Demographic"],
        "ItemDescription":    all_rows["ItemDescription"],
        "Result":             all_rows["Result"].apply(lambda x: int(x) if pd.notna(x) else ""),
        "Group_Total":        all_rows["_group_total"].apply(lambda x: int(x) if pd.notna(x) else ""),
        "Active":             1,
    })

    # ── Sort to match target order ─────────────────────────────────────────────
    # Target sort: Students of Color first (by district), then by ItemDescription, then demographics
    # Replicate target ordering heuristically: Cohort first, then Meeting Outcome,
    # within each group sorted by district/school/demographic
    soc_cohort = output[
        (output["Demographic"] == "Ethnicity: Students of Color") &
        (output["ItemDescription"] == "Number of Students in Cohort")
    ]
    soc_outcome = output[
        (output["Demographic"] == "Ethnicity: Students of Color") &
        (output["ItemDescription"] == "Number of Students Meeting Outcome")
    ]
    rest_cohort = output[
        (output["Demographic"] != "Ethnicity: Students of Color") &
        (output["ItemDescription"] == "Number of Students in Cohort")
    ]
    rest_outcome = output[
        (output["Demographic"] != "Ethnicity: Students of Color") &
        (output["ItemDescription"] == "Number of Students Meeting Outcome")
    ]

    def sort_block(block):
        return block.sort_values(["District", "School", "DemographicCategory", "Demographic"])

    output = pd.concat([
        sort_block(soc_cohort),
        sort_block(soc_outcome),
        sort_block(rest_cohort),
        sort_block(rest_outcome),
    ], ignore_index=True)

    return output


def compare_with_target(output, target_path):
    """Compare generated output with target CSV for validation."""
    target = pd.read_csv(target_path, dtype=str)

    # Normalize for comparison
    compare_cols = ["Dataset", "District", "School", "Year", "Indicator",
                    "DemographicCategory", "Demographic", "ItemDescription",
                    "Result", "Group_Total", "Active"]

    def normalize(df):
        df = df[compare_cols].copy()
        for col in ["Result", "Group_Total", "Year", "Active"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values(compare_cols).reset_index(drop=True)

    out_norm = normalize(output)
    tgt_norm = normalize(target)

    # Find rows in target not in output
    merged = tgt_norm.merge(out_norm, on=compare_cols, how="outer", indicator=True)
    only_target = merged[merged["_merge"] == "left_only"]
    only_output = merged[merged["_merge"] == "right_only"]

    print(f"\n=== COMPARISON RESULTS ===")
    print(f"Target rows:  {len(tgt_norm)}")
    print(f"Output rows:  {len(out_norm)}")
    print(f"Rows in target only (missing from output): {len(only_target)}")
    print(f"Rows in output only (extra): {len(only_output)}")

    if len(only_target) > 0:
        print("\nSAMPLE MISSING ROWS (in target but not in output):")
        print(only_target.drop("_merge", axis=1).head(20).to_string())
    if len(only_output) > 0:
        print("\nSAMPLE EXTRA ROWS (in output but not in target):")
        print(only_output.drop("_merge", axis=1).head(20).to_string())

    return len(only_target) == 0 and len(only_output) == 0


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

    output.to_csv(OUTPUT_FILE, index=False)
    print(f"\nOutput saved to: {OUTPUT_FILE}")

"""
Process CDE Chronic Absenteeism data file into public_datasets format.
Filters to Marin County (county 21) + State of California.
DASS = All, CharterSchool = All for aggregates; DASS = No at the school level.

Usage:
  python process_chronic_absence.py <source_file> [output_file] [target_file]

  source_file  — path to chronicabsenteeismYY-v2.txt downloaded from CDE
                 (https://www.cde.ca.gov/ds/ad/filesabd.asp)
  output_file  — (optional) path for the output CSV; defaults to
                 <source>_import.csv in the same folder as source_file
  target_file  — (optional) v_chronicabsences.csv dump for comparison mode

Example:
  python process_chronic_absence.py chronicabsenteeism25-v2.txt

Source file columns used:
  AcademicYear, AggregateLevel, CountyCode, DistrictCode, SchoolCode,
  CountyName, DistrictName, SchoolName, CharterSchool, DASS,
  ReportingCategory,
  ChronicAbsenteeismEligibleCumulativeEnrollment  → Cohort
  ChronicAbsenteeismCount                         → NumMeeting (chronically absent)

Business rules:
  - Students of Color = Total − White − Not Reported
    (new methodology; captures all non-white assuming Not Reported is excluded)
  - Not Economically Disadvantaged = Total − Economically Disadvantaged
  - Grade-level reporting categories (GR13, GR46, GR78, GR912, GRTK8, GRTKKN)
    are excluded — they have no destination in public_datasets.
"""

import sys
import os
import pandas as pd

# ── Resolve paths from command-line args or defaults ─────────────────────────
if len(sys.argv) >= 2:
    SOURCE_FILE = sys.argv[1]
else:
    SOURCE_FILE = os.path.join(os.path.dirname(__file__), "chronicabsenteeism25-v2.txt")

if len(sys.argv) >= 3:
    OUTPUT_FILE = sys.argv[2]
else:
    base = os.path.splitext(SOURCE_FILE)[0]
    OUTPUT_FILE = base + "_import.csv"

TARGET_FILE = None  # only used in verify mode
if len(sys.argv) >= 4:
    TARGET_FILE = sys.argv[3]

# ── Reporting category → (DemographicCategory, Demographic) ──────────────────
# Note: GX maps to Non-Binary Gender for chronic absence (differs from ACGR,
# where GX is "Gender: Missing"). Grade-level codes (GR*) are intentionally
# omitted — they have no corresponding row in public_datasets.
CATEGORY_MAP = {
    "GF": ("Gender",     "Gender: Female"),
    "GM": ("Gender",     "Gender: Male"),
    "GX": ("Gender",     "Gender: Non-Binary Gender"),
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
# Chronic absence covers all schools (elementary + middle + high), so this
# map is broader than ACGR's. A few districts don't take a "School District"
# suffix (Bolinas-Stinson Union, Larkspur-Corte Madera, San Rafael City
# Elementary, San Rafael City High); those are absent from this map on
# purpose and pass through unchanged.
DISTRICT_NAME_MAP = {
    "Marin County Office of Education": "Marin County Office Of Education",
    "Kentfield Elementary":              "Kentfield Elementary School District",
    "Laguna Joint Elementary":           "Laguna Joint Elementary School District",
    "Lagunitas Elementary":              "Lagunitas Elementary School District",
    "Mill Valley Elementary":            "Mill Valley Elementary School District",
    "Miller Creek Elementary":           "Miller Creek Elementary School District",
    "Nicasio":                           "Nicasio School District",
    "Novato Unified":                    "Novato Unified School District",
    "Reed Union Elementary":             "Reed Union Elementary School District",
    "Ross Elementary":                   "Ross Elementary School District",
    "Ross Valley Elementary":            "Ross Valley Elementary School District",
    "Sausalito Marin City":              "Sausalito Marin City School District",
    "Shoreline Unified":                 "Shoreline Unified School District",
    "Tamalpais Union High":              "Tamalpais Union High School District",
}

# ── School name remapping (source → display) ────────────────────────────────
# CDE occasionally renames schools between years. These map the current
# source names to the display names expected by the dashboard.
SCHOOL_NAME_MAP = {
    "Dr. Martin Luther King Jr. Academy": "Bayside Martin Luther King Jr. Academy",
    "San Jose Middle":                    "San Jose Intermediate",
    "Nova Education Center":              "NOVA Education Center",
}

# ── Schools to exclude at S-level ─────────────────────────────────────────────
EXCLUDE_SCHOOL_CODES = {"0000000", "0000001"}  # District Office, Nonpublic


def load_source(path):
    """Load chronic absenteeism text file, stripping Windows CRs."""
    with open(path, "r", encoding="latin1") as f:
        content = f.read().replace("\r\n", "\n").replace("\r", "\n")
    from io import StringIO
    df = pd.read_csv(StringIO(content), sep="\t", dtype=str, low_memory=False)
    # Normalize column names — source uses "Academic Year", "Aggregate Level" etc.
    df.columns = [c.strip().replace(" ", "") for c in df.columns]
    return df


def clean_value(v):
    """Return float or 0.0 for suppressed; None only for truly missing cells.

    CDE suppresses counts of 1–10 with '*' for privacy. The legacy view
    stores these as 0 (not NULL) for chronic absence, so we follow suit:
    '*' → 0.0. Blank cells still map to None.
    """
    if pd.isna(v) or str(v).strip() in ("", "–", "-"):
        return None
    s = str(v).strip()
    if s == "*":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def get_year(academic_year):
    """'2024-25' → 2025"""
    return int(str(academic_year).strip().split("-")[1]) + 2000


def get_district_and_school(row):
    """Return (District display name, School display name) for a row."""
    agg   = str(row["AggregateLevel"]).strip()
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
        school   = SCHOOL_NAME_MAP.get(sname, sname)
        return district, school
    return dname, sname


def process():
    print("Loading source file...")
    df = load_source(SOURCE_FILE)

    # ── Geography filter: county 21 OR state (T) ───────────────────────────────
    county21 = df["CountyCode"].str.strip() == "21"
    state_t  = df["AggregateLevel"].str.strip() == "T"
    df = df[county21 | state_t].copy()

    # DASS and CharterSchool columns in this source sometimes have trailing
    # spaces ("No "). Normalize before comparing.
    df["_agg"]     = df["AggregateLevel"].str.strip()
    df["_dass"]    = df["DASS"].str.strip()
    df["_charter"] = df["CharterSchool"].str.strip()

    # ── For C/D/T aggregate levels: CharterSchool=All AND DASS=All ─────────────
    agg_mask = df["_agg"].isin(["C", "D", "T"])
    agg_keep = agg_mask & (df["_charter"] == "All") & (df["_dass"] == "All")

    # ── For S-level (individual schools): include all DASS statuses ────────────
    # Chronic absence includes DASS=Yes schools (juvenile court, special ed,
    # continuation) where ACGR excludes them. At S level each school has
    # exactly one DASS value, so there's no double-counting risk.
    # Only exclude placeholder school codes (District Office, Nonpublic).
    s_mask = df["_agg"] == "S"
    s_keep = (s_mask
              & (~df["SchoolCode"].str.strip().isin(EXCLUDE_SCHOOL_CODES)))

    df = df[agg_keep | s_keep].copy()

    print(f"Rows after filtering: {len(df)}")

    # ── Parse numeric columns ─────────────────────────────────────────────────
    # EligibleCumulativeEnrollment is the denominator (Cohort).
    # ChronicAbsenteeismCount is the numerator (students chronically absent).
    cohort_col = "ChronicAbsenteeismEligibleCumulativeEnrollment"
    met_col    = "ChronicAbsenteeismCount"

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
    # SoC methodology (NEW): Total − White − Not Reported
    #   This captures all non-white students on the assumption that Not
    #   Reported is excluded; differs from ACGR (Total − White only).
    # Not Economically Disadvantaged: Total − Economically Disadvantaged
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
        dist = pr["_district"]
        sch  = pr["_school"]
        yr   = pr["_year"]
        gt   = pr.get("_group_total", None)

        ta_c = val(pr, "TA")
        rw_c = val(pr, "RW")
        rd_c = val(pr, "RD")
        ss_c = val(pr, "SS")

        # Get met values from pivot_met
        pm = pivot_met[
            (pivot_met["_district"] == dist) &
            (pivot_met["_school"]   == sch)  &
            (pivot_met["_year"]     == yr)
        ]
        ta_m = val(pm.iloc[0], "TA") if len(pm) > 0 else None
        rw_m = val(pm.iloc[0], "RW") if len(pm) > 0 else None
        rd_m = val(pm.iloc[0], "RD") if len(pm) > 0 else None
        ss_m = val(pm.iloc[0], "SS") if len(pm) > 0 else None

        # Students of Color = Total − White − Not Reported
        # When White or Not Reported is suppressed (*), treat as 0.
        if ta_c is not None:
            soc_cohort = ta_c - (rw_c if rw_c is not None else 0) \
                              - (rd_c if rd_c is not None else 0)
            derived_rows.append({
                "_district": dist, "_school": sch, "_year": yr,
                "_group_total": gt,
                "DemographicCategory": "Ethnicity",
                "Demographic": "Ethnicity: Students of Color",
                "ItemDescription": "Number of Students in Cohort",
                "Result": soc_cohort
            })
        if ta_m is not None:
            soc_met = ta_m - (rw_m if rw_m is not None else 0) \
                           - (rd_m if rd_m is not None else 0)
            derived_rows.append({
                "_district": dist, "_school": sch, "_year": yr,
                "_group_total": gt,
                "DemographicCategory": "Ethnicity",
                "Demographic": "Ethnicity: Students of Color",
                "ItemDescription": "Number of Students Meeting Outcome",
                "Result": soc_met
            })

        # Not Economically Disadvantaged = Total − Economically Disadvantaged
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
        "Dataset":            "Absences",
        "District":           all_rows["_district"],
        "School":             all_rows["_school"],
        "Year":               all_rows["_year"].astype(int),
        "Indicator":          "Chronic Absences",
        "DemographicCategory": all_rows["DemographicCategory"],
        "Demographic":        all_rows["Demographic"],
        "ItemDescription":    all_rows["ItemDescription"],
        "Result":             all_rows["Result"].apply(lambda x: int(x) if pd.notna(x) else ""),
        "Group_Total":        all_rows["_group_total"].apply(lambda x: int(x) if pd.notna(x) else ""),
        "Active":             1,
    })

    # ── Sort: SoC block first, then remaining rows ────────────────────────────
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
    """Compare generated output with target CSV for validation.

    Note: the target v_chronicabsences.csv is a database-view export using
    NumMeeting/Cohort (pivoted from the two-row ItemDescription layout).
    This comparison re-pivots our output to that shape before comparing.

    Also note: SoC rows will differ when the target CSV was generated under
    the old methodology (sum-of-non-white) — this is expected.
    """
    target = pd.read_csv(target_path, dtype=str)

    # Keep only columns that exist in both shapes (ignore Spanish translations)
    target = target[[
        "Dataset", "District", "School", "Year", "Indicator",
        "DemographicCategory", "Demographic",
        "NumMeeting", "Cohort",
    ]].copy()

    # Pivot our output from two rows per demographic to one
    pivot = output.pivot_table(
        index=["Dataset", "District", "School", "Year", "Indicator",
               "DemographicCategory", "Demographic"],
        columns="ItemDescription",
        values="Result",
        aggfunc="first",
    ).reset_index()
    pivot = pivot.rename(columns={
        "Number of Students Meeting Outcome": "NumMeeting",
        "Number of Students in Cohort":       "Cohort",
    })

    compare_cols = ["Dataset", "District", "School", "Year", "Indicator",
                    "DemographicCategory", "Demographic", "NumMeeting", "Cohort"]

    def normalize(df):
        df = df[compare_cols].copy()
        for col in ["Year", "NumMeeting", "Cohort"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values(compare_cols, na_position="last").reset_index(drop=True)

    out_norm = normalize(pivot)
    tgt_norm = normalize(target)

    merged = tgt_norm.merge(out_norm, on=compare_cols, how="outer", indicator=True)
    only_target = merged[merged["_merge"] == "left_only"]
    only_output = merged[merged["_merge"] == "right_only"]

    print(f"\n=== COMPARISON RESULTS ===")
    print(f"Target rows:  {len(tgt_norm)}")
    print(f"Output rows:  {len(out_norm)}")
    print(f"Rows in target only (missing from output): {len(only_target)}")
    print(f"Rows in output only (extra): {len(only_output)}")

    if len(only_target) > 0:
        print("\nSAMPLE ROWS IN TARGET ONLY:")
        print(only_target.drop("_merge", axis=1).head(25).to_string())
    if len(only_output) > 0:
        print("\nSAMPLE ROWS IN OUTPUT ONLY:")
        print(only_output.drop("_merge", axis=1).head(25).to_string())

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

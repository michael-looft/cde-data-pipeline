"""
Process CDE ACGR data file into public_datasets_graduates format.
Filters to Marin County (county 21) + State of California.
DASS = All for county/district/state rows; DASS = No for school-level rows.
CharterSchool = All only for aggregate rows.

Output is wide-format: one row per geography × year × demographic,
with all count columns side by side (no ItemDescription).

Usage:
  python process_graduates.py <source_file> [output_file]

  source_file  — path to acgrYY.txt downloaded from CDE
  output_file  — (optional) path for the output CSV; defaults to
                 acgrYY_graduates_import.csv in the same folder as source_file

Example:
  python process_graduates.py acgr26.txt
"""

import sys
import os
import pandas as pd
import numpy as np

# ── Resolve paths ─────────────────────────────────────────────────────────────
if len(sys.argv) >= 2:
    SOURCE_FILE = sys.argv[1]
else:
    SOURCE_FILE = os.path.join(os.path.dirname(__file__), "acgr25.txt")

if len(sys.argv) >= 3:
    OUTPUT_FILE = sys.argv[2]
else:
    base = os.path.splitext(SOURCE_FILE)[0]
    OUTPUT_FILE = base + "_graduates_import.csv"

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

# ── Source column → graduates table column mapping ────────────────────────────
# Note: "Graduates Meeting Local Requirements Exemption (Count)" is a newer CDE
# column not captured in the graduates table and is intentionally omitted.
SOURCE_TO_GRAD = {
    "CohortStudents":                              "Cohort",
    "Regular HS Diploma Graduates (Count)":        "Graduates",
    "Met UC/CSU Grad Req's (Count)":               "MetAG",
    "Seal of Biliteracy (Count)":                  "Biliteracy",
    "Golden State Seal Merit Diploma (Count)":     "GoldenStateSealMerit",
    "CPP Completer (Count)":                       "CHSPECompleter",
    "Adult Ed. HS Diploma (Count)":                "AdultEdDiploma",
    "SPED Certificate (Count)":                    "SPEDCertificate",
    "GED Completer (Count)":                       "GEDCompleter",
    "Other Transfer (Count)":                      "OtherTransfer",
    "Dropout (Count)":                             "Dropouts",
    "Still Enrolled (Count)":                      "StillEnrolled",
}
GRAD_COLS = list(SOURCE_TO_GRAD.values())

# Students of Color = direct sum of these ethnicities (excludes White, Not Reported)
SOC_REPORTING_CODES = {"RA", "RB", "RF", "RH", "RI", "RP", "RT"}

# Schools to exclude at S-level (district admin / nonpublic placeholders)
EXCLUDE_SCHOOL_NAMES = {"District Office", "Nonpublic, Nonsectarian Schools"}
EXCLUDE_SCHOOL_CODES = {"0000000", "0000001"}


def load_source(path):
    """Load ACGR text file, stripping Windows CRs."""
    with open(path, "r", encoding="latin1") as f:
        content = f.read().replace("\r\n", "\n").replace("\r", "\n")
    from io import StringIO
    df = pd.read_csv(StringIO(content), sep="\t", dtype=str, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
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
    """Return (District, School) using raw CDE names — no normalization."""
    agg   = row["AggregateLevel"].strip()
    dname = str(row["DistrictName"]).strip() if not pd.isna(row["DistrictName"]) else ""
    sname = str(row["SchoolName"]).strip()  if not pd.isna(row["SchoolName"])  else ""

    if agg == "T":
        return "State of California", "All Schools"
    elif agg == "C":
        # CountyName for C-level (e.g. "Marin")
        cname = str(row["CountyName"]).strip() if not pd.isna(row["CountyName"]) else "Marin County"
        return cname + " County" if not cname.endswith("County") else cname, "All Schools"
    elif agg == "D":
        return dname, "All Schools"
    elif agg == "S":
        return dname, sname
    return dname, sname


def process():
    print(f"Loading {SOURCE_FILE} ...")
    df = load_source(SOURCE_FILE)

    # ── Geography filter ───────────────────────────────────────────────────────
    county21 = df["CountyCode"].str.strip() == "21"
    state_t  = df["AggregateLevel"].str.strip() == "T"
    df = df[county21 | state_t].copy()

    agg = df["AggregateLevel"].str.strip()

    # ── C/D/T: CharterSchool=All AND DASS=All ──────────────────────────────────
    agg_mask = agg.isin(["C", "D", "T"])
    agg_keep = agg_mask & (df["CharterSchool"].str.strip() == "All") & (df["DASS"].str.strip() == "All")

    # ── S-level: DASS=No, exclude admin/nonpublic entries ─────────────────────
    s_mask = agg == "S"
    s_keep = (s_mask
              & (df["DASS"].str.strip() == "No")
              & (~df["SchoolCode"].str.strip().isin(EXCLUDE_SCHOOL_CODES))
              & (~df["SchoolName"].str.strip().isin(EXCLUDE_SCHOOL_NAMES)))

    df = df[agg_keep | s_keep].copy()
    print(f"Rows after filtering: {len(df)}")

    # ── Parse numeric columns ──────────────────────────────────────────────────
    for src_col in SOURCE_TO_GRAD:
        if src_col in df.columns:
            df[f"_{SOURCE_TO_GRAD[src_col]}"] = df[src_col].apply(clean_value)
        else:
            # Column not present in this file version — fill with None
            df[f"_{SOURCE_TO_GRAD[src_col]}"] = None

    df["_rc"]       = df["ReportingCategory"].str.strip()
    df["_year"]     = df["AcademicYear"].apply(get_year)
    df[["_district", "_school"]] = df.apply(
        lambda r: pd.Series(get_district_and_school(r)), axis=1
    )

    # ── Build base rows (one row per rc × geography × year) ───────────────────
    base_rows = []
    for _, row in df.iterrows():
        rc = row["_rc"]
        if rc not in CATEGORY_MAP:
            continue
        dem_cat, dem = CATEGORY_MAP[rc]

        rec = {
            "public_datasets_graduates_id": "",
            "Dataset":            "Graduates",
            "District":           row["_district"],
            "School":             row["_school"],
            "Year":               row["_year"],
            "Indicator":          "Graduates",
            "DemographicCategory": dem_cat,
            "Demographic":        dem,
            "AggregateLevel":     row["AggregateLevel"].strip(),
        }
        for grad_col in GRAD_COLS:
            v = row.get(f"_{grad_col}", None)
            rec[grad_col] = int(v) if (v is not None and not pd.isna(v)) else ""
        base_rows.append(rec)

    base_df = pd.DataFrame(base_rows)

    # ── Derived rows: Students of Color and Not Economically Disadvantaged ─────
    derived_rows = []

    # Build a pivot: for each (district, school, year, agg), one column per rc
    pivot_df = pd.DataFrame(base_rows)
    # We'll work directly from the numeric columns and the Demographic field

    for (dist, sch, yr, agg_lvl), grp in base_df.groupby(
            ["District", "School", "Year", "AggregateLevel"]):

        # ── Students of Color ─────────────────────────────────────────────────
        # SoC = direct sum of SOC_REPORTING_CODES (all ethnicities except White and Not Reported)
        # Map back to original reporting codes via grp["Demographic"]
        soc_dem_values = {CATEGORY_MAP[rc][1] for rc in SOC_REPORTING_CODES}
        soc_rows = grp[grp["Demographic"].isin(soc_dem_values)]
        if len(soc_rows) > 0:
            soc_rec = {
                "public_datasets_graduates_id": "",
                "Dataset":            "Graduates",
                "District":           dist,
                "School":             sch,
                "Year":               yr,
                "Indicator":          "Graduates",
                "DemographicCategory": "Ethnicity",
                "Demographic":        "Ethnicity: Students of Color",
                "AggregateLevel":     agg_lvl,
            }
            for grad_col in GRAD_COLS:
                vals = pd.to_numeric(soc_rows[grad_col], errors="coerce").fillna(0)
                soc_rec[grad_col] = int(vals.sum())
            derived_rows.append(soc_rec)

        # ── Not Economically Disadvantaged = Total − Economically Disadvantaged ─
        total_row = grp[grp["Demographic"] == "Total"]
        econ_dis  = grp[grp["Demographic"] == "Income: Economically Disadvantaged"]
        if len(total_row) > 0 and len(econ_dis) > 0:
            ned_rec = {
                "public_datasets_graduates_id": "",
                "Dataset":            "Graduates",
                "District":           dist,
                "School":             sch,
                "Year":               yr,
                "Indicator":          "Graduates",
                "DemographicCategory": "Income",
                "Demographic":        "Income: Not Economically Disadvantaged",
                "AggregateLevel":     agg_lvl,
            }
            for grad_col in GRAD_COLS:
                t = pd.to_numeric(total_row[grad_col].iloc[0], errors="coerce")
                e = pd.to_numeric(econ_dis[grad_col].iloc[0],  errors="coerce")
                if pd.notna(t) and pd.notna(e):
                    ned_rec[grad_col] = int(t - e)
                else:
                    ned_rec[grad_col] = ""
            derived_rows.append(ned_rec)

    # ── Combine and output ─────────────────────────────────────────────────────
    all_rows = pd.concat([base_df, pd.DataFrame(derived_rows)], ignore_index=True) \
               if derived_rows else base_df

    COLS = ["public_datasets_graduates_id", "Dataset", "District", "School", "Year",
            "Indicator", "DemographicCategory", "Demographic"] + GRAD_COLS + \
           ["AggregateLevel"]
    output = all_rows[COLS].sort_values(
        ["Year", "AggregateLevel", "District", "School", "DemographicCategory", "Demographic"]
    ).reset_index(drop=True)

    return output


if __name__ == "__main__":
    output = process()
    print(f"\nTotal output rows: {len(output)}")
    print(f"Demographics present: {sorted(output['Demographic'].unique())}")
    output.to_csv(OUTPUT_FILE, index=False)
    print(f"\nOutput saved to: {OUTPUT_FILE}")
    print("\nReminder: import into MySQL `public_datasets_graduates` table (datadashboard database).")
    print("Leave `public_datasets_graduates_id` blank — MySQL sets the ID and LastUpdated automatically.")
    print("Dataset = Graduates, Indicator = Graduates.")
    print("In phpMyAdmin CSV import, set Column names to: public_datasets_graduates_id,Dataset,District,School,Year,Indicator,DemographicCategory,Demographic,Cohort,Graduates,MetAG,Biliteracy,GoldenStateSealMerit,CHSPECompleter,AdultEdDiploma,SPEDCertificate,GEDCompleter,OtherTransfer,Dropouts,StillEnrolled,AggregateLevel")

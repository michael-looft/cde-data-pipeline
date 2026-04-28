# CDE Data Pipeline

An ETL pipeline for ingesting California Department of Education (CDE) public datasets into a MySQL database for community data dashboards. Built and maintained by Marin Promise Partnership to power county-level education equity reporting.

## What This Does

Each year, the California Department of Education publishes several large public datasets covering enrollment, chronic absenteeism, graduation rates, and educator staffing. These files are tab-delimited text files with hundreds of thousands of rows spanning every school, district, and county in California.

This pipeline:
- Downloads the relevant CDE source files (or accepts a local copy)
- Filters and transforms data to the county, district, and school level
- Calculates derived indicators (Students of Color, Not Economically Disadvantaged, etc.)
- Generates both a CSV import file and a SQL import file as fallback
- Connects directly to MySQL and replaces the prior year's data with current data

## Datasets Covered

| Dataset | CDE Source | Scripts |
|---|---|---|
| Census Day Enrollment | `cdenroll{YYYY}.txt` | `process_cde_enrollment.py`, `update_enrollment.py` |
| Chronic Absenteeism | `chronicabsenteeism{YY}.txt` | `process_chronic_absence.py`, `update_chronic_absence.py` |
| Adjusted Cohort Graduation Rate (ACGR) | `acgr{YY}.txt` | `process_acgr.py`, `process_graduates.py`, `update_annual_data.py` |
| CBEDS Paraeducators | CBEDS ORA file | `process_paraeducators.py`, `update_paraeducators.py` |
| STRE Staff Data | STRE file | `process_stre_staff.py`, `update_stre_staff.py` |

## Architecture

Each dataset follows the same two-script pattern:

```
process_{dataset}.py   — transforms raw CDE file → clean import CSV
update_{dataset}.py    — imports CSV into MySQL (with phpMyAdmin fallback)
csv_to_sql.py          — utility: converts any import CSV to a SQL file
```

The `process_` scripts are pure data transformation with no database dependency — useful for inspection and debugging independent of the database layer.

## Key Design Decisions

**Students of Color methodology:** Calculated as Total minus White (RE_W) minus Not Reported (RE_D). This captures all students in named racial/ethnic categories and is consistent with MPP's equity reporting framework.

**Credential handling:** Database passwords are never hardcoded. All scripts read credentials from environment variables, optionally loaded from a local `.env` file. See `CREDENTIALS_SETUP.md` for the full pattern.

**SQL fallback:** Every update script generates a `.sql` file alongside the CSV. If direct MySQL access is blocked (common on shared hosting), the SQL file can be imported manually via phpMyAdmin with identical results.

**Idempotent imports:** Each update deletes existing rows for the relevant year before inserting new ones, so re-running the script is always safe.

## Setup

### Requirements

```bash
pip3 install pandas pymysql
```

### Credentials

Copy `.env.example` to `.env` and fill in your database credentials:

```
DB_PASSWORD=your-password-here
```

Optional overrides (defaults are set in each script):
```
DB_HOST=your-db-host
DB_PORT=3306
DB_USER=your-db-user
DB_NAME=your-database-name
```

See `CREDENTIALS_SETUP.md` for the full pattern explanation.

### Running

Each update script can auto-download the current year's file or accept a local path:

```bash
# Auto-download latest file and import
python3 update_enrollment.py

# Use a local file
python3 update_enrollment.py cdenroll2526.txt

# Process only (no database import)
python3 process_cde_enrollment.py cdenroll2526.txt
```

## Automated Operation

These scripts are designed to run as scheduled Cowork agents that check for new CDE file releases and trigger the full pipeline automatically. See the companion repository [cowork-agents](https://github.com/michael-looft/cowork-agents) for the agent definitions.

## Data Notes

- CDE source files are public and freely available at [cde.ca.gov](https://www.cde.ca.gov/ds/ad/)
- Files are large (10-35MB, hundreds of thousands of rows) and are not included in this repository
- The pipeline filters to Marin County plus California state totals by default; county codes are configurable
- CDE occasionally releases revised versions of files (e.g. `acgr24-v2.txt`); the scripts handle version suffixes automatically

## Context

This pipeline is part of a broader data infrastructure built at [Marin Promise Partnership](https://www.marinpromisepartnership.org), a collective impact backbone organization working to advance educational equity in Marin County, California.

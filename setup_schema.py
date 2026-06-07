#!/usr/bin/env python3
"""
Deploy-time schema bootstrap — runs during deploy.sh Step 7.

Creates the Unity Catalog schema, all Delta tables, UC Volume, and grants
the app service principal all required UC permissions via the SQL API.

All statements use IF NOT EXISTS — fully idempotent, safe to re-run on every deploy.
No Spark session required — uses Databricks SQL API via the CLI.

Usage (from deploy.sh):
    python3 setup_schema.py \
        --profile "$PROFILE" \
        --catalog "$CATALOG" \
        --schema  "$SCHEMA" \
        --warehouse-id "$WAREHOUSE_ID" \
        --app-sp-id "$APP_SP_CLIENT_ID"
"""

import argparse
import json
import subprocess
import sys

PROFILE = "DEFAULT"


def _info(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr)


def _sql(statement: str, warehouse_id: str, label: str) -> bool:
    """Execute a SQL statement via the SQL API. Returns True on success."""
    body = json.dumps({
        "statement": statement.strip(),
        "warehouse_id": warehouse_id,
        "wait_timeout": "30s",
    })
    cmd = ["databricks", "api", "post", "/api/2.0/sql/statements",
           f"--profile={PROFILE}", "--json", body]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        _info(f"⚠ {label}: {result.stderr.strip()[:100]}")
        return False

    try:
        d     = json.loads(result.stdout)
        state = d.get("status", {}).get("state", "")
        if state == "SUCCEEDED":
            _info(f"✔ {label}")
            return True
        err = d.get("status", {}).get("error", {}).get("message", state)
        _info(f"⚠ {label}: {str(err)[:100]}")
        return False
    except Exception as e:
        _info(f"⚠ {label}: {e}")
        return False


def run(catalog: str, schema: str, warehouse_id: str, app_sp_id: str) -> None:
    C = f"`{catalog}`"
    S = f"`{catalog}`.`{schema}`"

    def T(table: str) -> str:
        return f"`{catalog}`.`{schema}`.`{table}`"

    print(f"\n[Catalog + Schema + Tables]", file=sys.stderr)
    _info(f"Target: {catalog}.{schema}")

    # ── Catalog ───────────────────────────────────────────────────────────────
    # NOTE: if the catalog already exists, CREATE CATALOG IF NOT EXISTS may
    # return an INVALID_STATE warning — this is harmless (catalog is available).
    _sql(f"CREATE CATALOG IF NOT EXISTS {C}", warehouse_id,
         f"Catalog {catalog}")

    # ── Schema ────────────────────────────────────────────────────────────────
    _sql(f"CREATE SCHEMA IF NOT EXISTS {S}", warehouse_id,
         f"Schema {catalog}.{schema}")

    # ── Tables ────────────────────────────────────────────────────────────────
    _sql(f"""
        CREATE TABLE IF NOT EXISTS {T("patient_records")} (
            patient_id       STRING NOT NULL,
            mrn              STRING,
            dob              STRING,
            gender           STRING,
            message_datetime STRING,
            clinicalrecord   STRING
        ) USING DELTA COMMENT 'Synthetic patient clinical records — SOAP format notes'
    """, warehouse_id, "Table patient_records")

    _sql(f"""
        CREATE TABLE IF NOT EXISTS {T("care_gap_rules")} (
            rule_id           STRING NOT NULL,
            condition         STRING,
            gap_name          STRING,
            guideline         STRING,
            check_description STRING,
            priority          STRING,
            embedding_text    STRING
        ) USING DELTA COMMENT 'Evidence-based care gap rules (HEDIS / ACC / ADA)'
    """, warehouse_id, "Table care_gap_rules")

    _sql(f"""
        CREATE TABLE IF NOT EXISTS {T("icd10_analysis_results")} (
            patient_id  STRING NOT NULL,
            analyzed_at TIMESTAMP,
            code        STRING,
            diag_type   STRING,
            description STRING,
            confidence  STRING
        ) USING DELTA COMMENT 'ICD-10 code analysis results saved by the ICD-10 Analyzer'
    """, warehouse_id, "Table icd10_analysis_results")

    _sql(f"""
        CREATE TABLE IF NOT EXISTS {T("care_gap_findings")} (
            patient_id         STRING,
            rule_id            STRING,
            gap_name           STRING,
            condition          STRING,
            priority           STRING,
            guideline          STRING,
            finding            STRING,
            recommended_action STRING,
            created_at         TIMESTAMP
        ) USING DELTA COMMENT 'Care gaps identified per patient by the Care Gap Advisor'
    """, warehouse_id, "Table care_gap_findings")

    _sql(f"""
        CREATE TABLE IF NOT EXISTS {T("bootstrap_status")} (
            step        STRING NOT NULL,
            status      STRING,
            updated_at  TIMESTAMP,
            details     STRING
        ) USING DELTA COMMENT 'Tracks setup job progress — read by the Setup page'
    """, warehouse_id, "Table bootstrap_status")

    # ── UC Volume ─────────────────────────────────────────────────────────────
    _sql(f"CREATE VOLUME IF NOT EXISTS {T('icd10_reference_pdfs')}",
         warehouse_id, "Volume icd10_reference_pdfs")

    # ── UC Grants ─────────────────────────────────────────────────────────────
    if not app_sp_id:
        _info("app_sp_id not set — skipping UC grants")
        return

    print(f"\n[UC Grants → {app_sp_id}]", file=sys.stderr)
    SP = f"`{app_sp_id}`"

    grants = [
        (f"GRANT USAGE ON CATALOG {C} TO {SP}",                               "CATALOG        USAGE"),
        (f"GRANT USAGE ON SCHEMA {S} TO {SP}",                                "SCHEMA         USAGE"),
        (f"GRANT SELECT ON TABLE {T('patient_records')} TO {SP}",             "patient_records          SELECT"),
        (f"GRANT SELECT ON TABLE {T('care_gap_rules')} TO {SP}",              "care_gap_rules           SELECT"),
        (f"GRANT SELECT ON TABLE {T('bootstrap_status')} TO {SP}",            "bootstrap_status         SELECT"),
        (f"GRANT MODIFY  ON TABLE {T('bootstrap_status')} TO {SP}",           "bootstrap_status         MODIFY"),
        (f"GRANT SELECT ON TABLE {T('icd10_analysis_results')} TO {SP}",      "icd10_analysis_results   SELECT"),
        (f"GRANT MODIFY  ON TABLE {T('icd10_analysis_results')} TO {SP}",     "icd10_analysis_results   MODIFY"),
        (f"GRANT SELECT ON TABLE {T('care_gap_findings')} TO {SP}",           "care_gap_findings        SELECT"),
        (f"GRANT MODIFY  ON TABLE {T('care_gap_findings')} TO {SP}",          "care_gap_findings        MODIFY"),
        (f"GRANT READ VOLUME ON VOLUME {T('icd10_reference_pdfs')} TO {SP}",  "icd10_reference_pdfs     READ VOLUME"),
    ]
    for stmt, label in grants:
        _sql(stmt, warehouse_id, label)


def main() -> None:
    global PROFILE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile",      default="DEFAULT")
    parser.add_argument("--catalog",      required=True)
    parser.add_argument("--schema",       required=True)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--app-sp-id",    default="")
    args = parser.parse_args()
    PROFILE = args.profile
    run(args.catalog, args.schema, args.warehouse_id, args.app_sp_id)


if __name__ == "__main__":
    main()

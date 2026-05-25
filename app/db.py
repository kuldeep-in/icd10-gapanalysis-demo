from databricks.sdk.service.sql import StatementState
from config import w, CATALOG, SCHEMA, WAREHOUSE_ID, logger


def execute_sql(statement: str) -> list[dict]:
    if not WAREHOUSE_ID or WAREHOUSE_ID == "<your-warehouse-id>":
        raise RuntimeError(
            "SQL Warehouse not configured — set DATABRICKS_WAREHOUSE_ID in app.yaml and redeploy."
        )
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=statement, wait_timeout="30s"
    )
    if resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"SQL error: {resp.status.error}")
    if not resp.manifest or not resp.manifest.schema:
        return []
    if not resp.result:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in (resp.result.data_array or [])]


def _sql_esc(v) -> str:
    return str(v).replace("'", "''")


def save_care_gap_finding(patient_id: str, gap: dict) -> None:
    execute_sql(f"""
        INSERT INTO `{CATALOG}`.`{SCHEMA}`.care_gap_findings
            (patient_id, rule_id, gap_name, condition, priority, guideline,
             finding, recommended_action, created_at)
        VALUES (
            '{_sql_esc(patient_id)}',
            '{_sql_esc(gap.get("rule_id", ""))}',
            '{_sql_esc(gap.get("gap_name", ""))}',
            '{_sql_esc(gap.get("condition", ""))}',
            '{_sql_esc(gap.get("priority", ""))}',
            '{_sql_esc(gap.get("guideline", ""))}',
            '{_sql_esc(gap.get("finding", ""))}',
            '{_sql_esc(gap.get("recommended_action", ""))}',
            CURRENT_TIMESTAMP()
        )
    """)


def delete_care_gap_finding(patient_id: str, rule_id: str,
                            catalog: str = CATALOG, schema: str = SCHEMA) -> None:
    execute_sql(
        f"DELETE FROM `{catalog}`.`{schema}`.care_gap_findings "
        f"WHERE patient_id = '{_sql_esc(patient_id)}' "
        f"AND rule_id = '{_sql_esc(rule_id)}'"
    )


def get_patient_care_gap_findings(
    patient_id: str, catalog: str = CATALOG, schema: str = SCHEMA
) -> list[dict]:
    try:
        return execute_sql(
            f"SELECT rule_id, gap_name, condition, priority, guideline, finding, "
            f"recommended_action, created_at "
            f"FROM `{catalog}`.`{schema}`.care_gap_findings "
            f"WHERE patient_id = '{_sql_esc(patient_id)}' "
            f"ORDER BY created_at DESC"
        )
    except Exception as e:
        logger.warning(f"Could not load saved findings for {patient_id}: {e}")
        return []


def get_all_icd10_saved_codes(catalog: str = CATALOG, schema: str = SCHEMA) -> dict:
    """Return {patient_id: [code_rows]} for all patients with saved ICD-10 codes."""
    try:
        rows = execute_sql(
            f"SELECT patient_id, code, diag_type, description, confidence, analyzed_at "
            f"FROM `{catalog}`.`{schema}`.icd10_analysis_results "
            f"WHERE code IS NOT NULL "
            f"ORDER BY patient_id, analyzed_at DESC"
        )
        result: dict = {}
        for r in rows:
            result.setdefault(r["patient_id"], []).append(r)
        return result
    except Exception as e:
        logger.warning(f"Could not load saved ICD-10 codes: {e}")
        return {}


def delete_icd10_saved_code(patient_id: str, code: str,
                            catalog: str = CATALOG, schema: str = SCHEMA) -> None:
    execute_sql(
        f"DELETE FROM `{catalog}`.`{schema}`.icd10_analysis_results "
        f"WHERE patient_id = '{_sql_esc(patient_id)}' "
        f"AND code = '{_sql_esc(code)}'"
    )


def get_all_care_gap_findings(catalog: str = CATALOG, schema: str = SCHEMA) -> dict:
    """Return {patient_id: [findings]} for all patients, ordered by patient then date desc."""
    try:
        rows = execute_sql(
            f"SELECT patient_id, rule_id, gap_name, condition, priority, guideline, "
            f"finding, recommended_action, created_at "
            f"FROM `{catalog}`.`{schema}`.care_gap_findings "
            f"ORDER BY patient_id, created_at DESC"
        )
        result: dict = {}
        for r in rows:
            result.setdefault(r["patient_id"], []).append(r)
        return result
    except Exception as e:
        logger.warning(f"Could not load all care gap findings: {e}")
        return {}


def save_icd10_code(patient_id: str, code: dict,
                    catalog: str = CATALOG, schema: str = SCHEMA) -> None:
    execute_sql(f"""
        INSERT INTO `{catalog}`.`{schema}`.icd10_analysis_results
            (patient_id, analyzed_at, code, diag_type, description, confidence)
        VALUES (
            '{_sql_esc(patient_id)}',
            CURRENT_TIMESTAMP(),
            '{_sql_esc(code.get("code", ""))}',
            '{_sql_esc(code.get("type", ""))}',
            '{_sql_esc(code.get("description", ""))}',
            '{_sql_esc(code.get("confidence", ""))}'
        )
    """)


def get_saved_icd10_codes(patient_id: str,
                          catalog: str = CATALOG, schema: str = SCHEMA) -> set:
    """Return set of already-saved ICD-10 codes for a patient."""
    try:
        rows = execute_sql(
            f"SELECT DISTINCT code FROM `{catalog}`.`{schema}`.icd10_analysis_results "
            f"WHERE patient_id = '{_sql_esc(patient_id)}' AND code IS NOT NULL"
        )
        return {r["code"] for r in rows}
    except Exception as e:
        logger.warning(f"Could not load saved ICD-10 codes for {patient_id}: {e}")
        return set()


def load_patients(catalog: str = CATALOG, schema: str = SCHEMA) -> list[dict]:
    return execute_sql(
        f"SELECT patient_id, mrn, dob, gender, message_datetime "
        f"FROM `{catalog}`.`{schema}`.patient_records ORDER BY patient_id"
    )


def get_patient_record(patient_id: str, catalog: str = CATALOG, schema: str = SCHEMA) -> dict | None:
    rows = execute_sql(
        f"SELECT * FROM `{catalog}`.`{schema}`.patient_records "
        f"WHERE patient_id = '{patient_id}' LIMIT 1"
    )
    return rows[0] if rows else None


def get_care_gap_rules(catalog: str = CATALOG, schema: str = SCHEMA) -> list[dict]:
    return execute_sql(
        f"SELECT * FROM `{catalog}`.`{schema}`.care_gap_rules ORDER BY priority, condition"
    )


def patient_options(patients: list[dict]) -> list[dict]:
    return [
        {"label": f"{p['patient_id']} — {p['gender']}, DOB {p['dob']}", "value": p["patient_id"]}
        for p in patients
    ]

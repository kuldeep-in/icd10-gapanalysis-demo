# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 1 — Catalog & Schema Setup
# MAGIC Creates the Unity Catalog, schema, all Delta tables, UC Volume, and grants
# MAGIC app service principal access. All statements use `IF NOT EXISTS` — safe to re-run.

# COMMAND ----------

dbutils.widgets.text("catalog",    "my_catalog")
dbutils.widgets.text("schema",     "icd10_care_gap")
dbutils.widgets.text("app_sp_id",  "")

CATALOG   = dbutils.widgets.get("catalog")
SCHEMA    = dbutils.widgets.get("schema")
APP_SP_ID = dbutils.widgets.get("app_sp_id")

print(f"Target: {CATALOG}.{SCHEMA}")

# COMMAND ----------

# Catalog
existing_catalogs = [row.catalog for row in spark.sql("SHOW CATALOGS").collect()]
if CATALOG in existing_catalogs:
    print(f"Catalog '{CATALOG}' already exists — skipping creation")
else:
    spark.sql(f"CREATE CATALOG `{CATALOG}`")
    print(f"Catalog '{CATALOG}' created")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`")
print(f"Schema ready: {CATALOG}.{SCHEMA}")

# COMMAND ----------

# UC Volume for ICD-10 reference PDFs
spark.sql(f"CREATE VOLUME IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.icd10_reference_pdfs")
print(f"Volume ready: {CATALOG}.{SCHEMA}.icd10_reference_pdfs")

# COMMAND ----------

# Delta tables
spark.sql(f"""
CREATE TABLE IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.patient_records (
    patient_id       STRING NOT NULL,
    mrn              STRING,
    dob              STRING,
    gender           STRING,
    message_datetime STRING,
    clinicalrecord   STRING
)
USING DELTA
COMMENT 'Synthetic patient clinical records — SOAP format notes'
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.icd10_analysis_results (
    patient_id  STRING NOT NULL,
    analyzed_at TIMESTAMP,
    code        STRING,
    diag_type   STRING,
    description STRING,
    confidence  STRING
)
USING DELTA
COMMENT 'ICD-10 code analysis results saved by the ICD-10 Analyzer'
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.care_gap_rules (
    rule_id           STRING NOT NULL,
    condition         STRING,
    gap_name          STRING,
    guideline         STRING,
    check_description STRING,
    priority          STRING
)
USING DELTA
COMMENT 'Evidence-based care gap rules aligned to HEDIS / ACC / ADA guidelines'
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.care_gap_findings (
    patient_id         STRING,
    rule_id            STRING,
    gap_name           STRING,
    condition          STRING,
    priority           STRING,
    guideline          STRING,
    finding            STRING,
    recommended_action STRING,
    created_at         TIMESTAMP
)
USING DELTA
COMMENT 'Care gaps identified per patient, saved by user from the Care Gap Advisor'
""")

# Ensure new columns exist on tables created with the old schema
for _col in ["condition STRING", "guideline STRING", "finding STRING", "created_at TIMESTAMP"]:
    try:
        spark.sql(f"ALTER TABLE `{CATALOG}`.`{SCHEMA}`.care_gap_findings ADD COLUMN IF NOT EXISTS {_col}")
    except Exception:
        pass

spark.sql(f"""
CREATE TABLE IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.bootstrap_status (
    step        STRING NOT NULL,
    status      STRING,
    updated_at  TIMESTAMP,
    details     STRING
)
USING DELTA
COMMENT 'Tracks bootstrap progress and stores runtime config (e.g. KA endpoint URL)'
""")

print("All tables created")

# COMMAND ----------

# Grant app SP access to all resources
if APP_SP_ID:
    grants = [
        f"GRANT USAGE ON CATALOG `{CATALOG}` TO `{APP_SP_ID}`",
        f"GRANT USAGE ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `{APP_SP_ID}`",
        f"GRANT SELECT ON TABLE `{CATALOG}`.`{SCHEMA}`.patient_records TO `{APP_SP_ID}`",
        f"GRANT SELECT ON TABLE `{CATALOG}`.`{SCHEMA}`.care_gap_rules TO `{APP_SP_ID}`",
        f"GRANT SELECT ON TABLE `{CATALOG}`.`{SCHEMA}`.bootstrap_status TO `{APP_SP_ID}`",
        f"GRANT MODIFY ON TABLE `{CATALOG}`.`{SCHEMA}`.bootstrap_status TO `{APP_SP_ID}`",
        f"GRANT SELECT ON TABLE `{CATALOG}`.`{SCHEMA}`.icd10_analysis_results TO `{APP_SP_ID}`",
        f"GRANT SELECT ON TABLE `{CATALOG}`.`{SCHEMA}`.care_gap_findings TO `{APP_SP_ID}`",
        f"GRANT MODIFY ON TABLE `{CATALOG}`.`{SCHEMA}`.care_gap_findings TO `{APP_SP_ID}`",
        f"GRANT READ VOLUME ON VOLUME `{CATALOG}`.`{SCHEMA}`.icd10_reference_pdfs TO `{APP_SP_ID}`",
    ]
    for stmt in grants:
        try:
            spark.sql(stmt)
        except Exception as e:
            print(f"  WARN: {stmt[:60]}… → {e}")
    print(f"App SP permissions granted to {APP_SP_ID}")
else:
    print("app_sp_id not set — skipping grants")

# COMMAND ----------

spark.sql(f"""
MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
USING (SELECT 'create_catalog' AS step) AS s ON t.step = s.step
WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
    details = 'Catalog {CATALOG}, schema {SCHEMA}, tables, and volume icd10_reference_pdfs created'
WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
    VALUES ('create_catalog', 'COMPLETED', current_timestamp(),
            'Catalog {CATALOG}, schema {SCHEMA}, tables, and volume icd10_reference_pdfs created')
""")

print("Step 1 complete")



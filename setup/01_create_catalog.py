# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 1 — Create Unity Catalog Structure
# MAGIC Creates the catalog, a single configurable schema, Delta tables, UC Volume,
# MAGIC and seeds care gap rules. All statements use `IF NOT EXISTS` — safe to re-run.

# COMMAND ----------

dbutils.widgets.text("catalog",    "my_catalog")
dbutils.widgets.text("schema",     "icd10_care_gap")
dbutils.widgets.text("app_sp_id",  "")

CATALOG    = dbutils.widgets.get("catalog")
SCHEMA     = dbutils.widgets.get("schema")
APP_SP_ID  = dbutils.widgets.get("app_sp_id")

print(f"Target: {CATALOG}.{SCHEMA}")

# COMMAND ----------

# Catalog — check existence before creating
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
print(f"Volume created: {CATALOG}.{SCHEMA}.icd10_reference_pdfs")

# COMMAND ----------

# Patient records table
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

# ICD-10 analysis results table
spark.sql(f"""
CREATE TABLE IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.icd10_analysis_results (
    patient_id        STRING NOT NULL,
    analyzed_at       TIMESTAMP,
    icd10_suggestions STRING,
    raw_response      STRING
)
USING DELTA
COMMENT 'ICD-10 code suggestions returned by the Knowledge Assistant'
""")

# Care gap rules table
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

# Care gap findings table
spark.sql(f"""
CREATE TABLE IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.care_gap_findings (
    patient_id         STRING,
    rule_id            STRING,
    gap_name           STRING,
    identified_at      TIMESTAMP,
    recommended_action STRING,
    priority           STRING
)
USING DELTA
COMMENT 'Surfaced care gaps per patient identified by the Care Gap Advisor'
""")

# Bootstrap / app config table
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

# Seed care gap rules (idempotent)
from pyspark.sql import Row

rules = [
    ("CGR-001", "T2DM",         "Annual HbA1c",                     "ADA Standards of Care 2024",           "HbA1c measured within the last 12 months",                                "HIGH"),
    ("CGR-002", "T2DM",         "Diabetic Eye Exam",                 "ADA Standards of Care 2024",           "Dilated eye exam or ophthalmology visit within the last 12 months",       "HIGH"),
    ("CGR-003", "T2DM",         "Annual Foot Exam",                  "ADA Standards of Care 2024",           "Comprehensive foot exam with monofilament within the last 12 months",     "HIGH"),
    ("CGR-004", "T2DM",         "Urine ACR Screening",               "ADA Standards of Care 2024",           "Urine albumin-to-creatinine ratio within the last 12 months",             "MEDIUM"),
    ("CGR-005", "T2DM",         "eGFR Monitoring",                   "ADA Standards of Care 2024",           "Serum creatinine / eGFR within the last 6 months",                        "MEDIUM"),
    ("CGR-006", "HTN",          "BP at Target (<130/80)",            "ACC/AHA 2023 Hypertension Guidelines", "Last documented BP reading below 130/80 mmHg",                            "HIGH"),
    ("CGR-007", "HTN",          "Annual Renal Function Check",       "JNC 8 Guidelines",                    "BMP or CMP with creatinine within the last 12 months",                    "MEDIUM"),
    ("CGR-008", "POST-MI",      "High-Intensity Statin Prescribed",  "ACC/AHA ASCVD Guidelines 2023",        "High-intensity statin (atorvastatin 40-80mg or rosuvastatin 20-40mg)",    "HIGH"),
    ("CGR-009", "POST-MI",      "Beta-Blocker Therapy",              "ACC/AHA Heart Failure Guidelines",     "Beta-blocker prescribed for post-MI patients with reduced EF",            "HIGH"),
    ("CGR-010", "POST-MI",      "Dual Antiplatelet Therapy",         "ACC/AHA PCI Guidelines",               "Aspirin + P2Y12 inhibitor for 12 months post-PCI",                        "HIGH"),
    ("CGR-011", "AFIB",         "Anticoagulation (CHA2DS2-VASc≥2)", "AHA/ACC/HRS Afib Guidelines 2023",    "Anticoagulation prescribed when CHA2DS2-VASc ≥ 2 (men) or ≥ 3 (women)",  "HIGH"),
    ("CGR-012", "AFIB",         "Rate Control Goal HR<80",           "AHA/ACC/HRS Afib Guidelines 2023",    "Resting heart rate documented below 80 bpm",                              "MEDIUM"),
    ("CGR-013", "BREAST_CANCER","Annual Mammography",                "NCCN Breast Cancer Surveillance v2",   "Annual mammogram for breast cancer survivors",                            "HIGH"),
    ("CGR-014", "BREAST_CANCER","Bone Density on Endocrine Therapy", "ASCO Bone Health Guidelines",         "DEXA scan at baseline and every 1-2 years on aromatase inhibitor/tamoxifen","MEDIUM"),
    ("CGR-015", "COPD",         "Annual Spirometry",                 "GOLD COPD Guidelines 2024",            "Spirometry within the last 12 months for COPD staging and monitoring",    "MEDIUM"),
    ("CGR-016", "COPD",         "Influenza Vaccination",             "GOLD COPD Guidelines 2024",            "Annual influenza vaccine documented",                                      "HIGH"),
    ("CGR-017", "COPD",         "Pneumococcal Vaccination",          "GOLD COPD Guidelines 2024",            "PCV15 or PCV20 pneumococcal vaccine documented",                          "HIGH"),
    ("CGR-018", "CKD",          "eGFR Monitoring (Stage 3+)",        "KDIGO CKD Guidelines 2024",            "eGFR measured within the last 6 months for CKD Stage 3 and above",       "HIGH"),
    ("CGR-019", "CKD",          "ACE/ARB for Proteinuric CKD",      "KDIGO CKD Guidelines 2024",            "ACE inhibitor or ARB prescribed for CKD with ACR ≥ 30 mg/g",             "HIGH"),
    ("CGR-020", "DEPRESSION",   "PHQ-9 Follow-Up Screening",        "USPSTF Depression Screening 2023",     "PHQ-9 reassessment within 4-8 weeks of starting antidepressant therapy",  "HIGH"),
]

existing_rules = spark.sql(
    f"SELECT COUNT(*) as cnt FROM `{CATALOG}`.`{SCHEMA}`.care_gap_rules"
).collect()[0]["cnt"]

if existing_rules == 0:
    rules_df = spark.createDataFrame([
        Row(rule_id=r[0], condition=r[1], gap_name=r[2],
            guideline=r[3], check_description=r[4], priority=r[5])
        for r in rules
    ])
    rules_df.write.mode("overwrite").saveAsTable(f"`{CATALOG}`.`{SCHEMA}`.care_gap_rules")
    print(f"Seeded {len(rules)} care gap rules")
else:
    print(f"Care gap rules already present ({existing_rules} rows) — skipping seed")

# COMMAND ----------

# COMMAND ----------

# Grant the Databricks App service principal access to all created resources.
# Wrapped in try/except — fails gracefully if the SP doesn't exist or caller lacks MANAGE privilege.
if APP_SP_ID:
    grants = [
        f"GRANT USAGE ON CATALOG `{CATALOG}` TO `{APP_SP_ID}`",
        f"GRANT USAGE ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `{APP_SP_ID}`",
        f"GRANT SELECT ON TABLE `{CATALOG}`.`{SCHEMA}`.patient_records TO `{APP_SP_ID}`",
        f"GRANT SELECT ON TABLE `{CATALOG}`.`{SCHEMA}`.care_gap_rules TO `{APP_SP_ID}`",
        f"GRANT SELECT ON TABLE `{CATALOG}`.`{SCHEMA}`.bootstrap_status TO `{APP_SP_ID}`",
        f"GRANT SELECT ON TABLE `{CATALOG}`.`{SCHEMA}`.icd10_analysis_results TO `{APP_SP_ID}`",
        f"GRANT SELECT ON TABLE `{CATALOG}`.`{SCHEMA}`.care_gap_findings TO `{APP_SP_ID}`",
        f"GRANT READ VOLUME ON VOLUME `{CATALOG}`.`{SCHEMA}`.icd10_reference_pdfs TO `{APP_SP_ID}`",
    ]
    for stmt in grants:
        try:
            spark.sql(stmt)
        except Exception as e:
            print(f"  WARN: {stmt[:60]}... → {e}")
    print(f"App SP permissions granted to {APP_SP_ID}")
else:
    print("app_sp_id not set — skipping grants")

# COMMAND ----------

spark.sql(f"""
MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
USING (SELECT 'create_catalog' AS step) AS s ON t.step = s.step
WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
    details = 'Schema {SCHEMA}, tables, volume icd10_reference_pdfs, and {len(rules)} care gap rules created'
WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
    VALUES ('create_catalog', 'COMPLETED', current_timestamp(),
            'Schema {SCHEMA}, tables, volume icd10_reference_pdfs, and {len(rules)} care gap rules created')
""")

print("Step 1 complete")


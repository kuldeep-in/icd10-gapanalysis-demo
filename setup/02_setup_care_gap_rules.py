# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 2 — Care Gap Rules Setup
# MAGIC Seeds the `care_gap_rules` table with evidence-based clinical rules aligned to
# MAGIC HEDIS / ACC / ADA guidelines. Idempotent — skips if rules already present.

# COMMAND ----------

dbutils.widgets.text("catalog", "my_catalog")
dbutils.widgets.text("schema",  "icd10_care_gap")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA  = dbutils.widgets.get("schema")

print(f"Target: {CATALOG}.{SCHEMA}.care_gap_rules")

# COMMAND ----------

# Idempotency check
existing = spark.sql(f"""
    SELECT details FROM `{CATALOG}`.`{SCHEMA}`.bootstrap_status
    WHERE step = 'setup_care_gap_rules' AND status = 'COMPLETED'
    ORDER BY updated_at DESC LIMIT 1
""").collect()

if existing:
    print("Care gap rules already seeded — skipping")
    dbutils.notebook.exit("SKIPPED")

# COMMAND ----------

from pyspark.sql import Row

rules = [
    ("CGR-001", "T2DM",          "Annual HbA1c",                      "ADA Standards of Care 2024",            "HbA1c measured within the last 12 months",                                  "HIGH"),
    ("CGR-002", "T2DM",          "Diabetic Eye Exam",                  "ADA Standards of Care 2024",            "Dilated eye exam or ophthalmology visit within the last 12 months",         "HIGH"),
    ("CGR-003", "T2DM",          "Annual Foot Exam",                   "ADA Standards of Care 2024",            "Comprehensive foot exam with monofilament within the last 12 months",       "HIGH"),
    ("CGR-004", "T2DM",          "Urine ACR Screening",                "ADA Standards of Care 2024",            "Urine albumin-to-creatinine ratio within the last 12 months",               "MEDIUM"),
    ("CGR-005", "T2DM",          "eGFR Monitoring",                    "ADA Standards of Care 2024",            "Serum creatinine / eGFR within the last 6 months",                          "MEDIUM"),
    ("CGR-006", "HTN",           "BP at Target (<130/80)",             "ACC/AHA 2023 Hypertension Guidelines",  "Last documented BP reading below 130/80 mmHg",                              "HIGH"),
    ("CGR-007", "HTN",           "Annual Renal Function Check",        "JNC 8 Guidelines",                     "BMP or CMP with creatinine within the last 12 months",                      "MEDIUM"),
    ("CGR-008", "POST-MI",       "High-Intensity Statin Prescribed",   "ACC/AHA ASCVD Guidelines 2023",         "High-intensity statin (atorvastatin 40-80mg or rosuvastatin 20-40mg)",      "HIGH"),
    ("CGR-009", "POST-MI",       "Beta-Blocker Therapy",               "ACC/AHA Heart Failure Guidelines",      "Beta-blocker prescribed for post-MI patients with reduced EF",              "HIGH"),
    ("CGR-010", "POST-MI",       "Dual Antiplatelet Therapy",          "ACC/AHA PCI Guidelines",                "Aspirin + P2Y12 inhibitor for 12 months post-PCI",                          "HIGH"),
    ("CGR-011", "AFIB",          "Anticoagulation (CHA2DS2-VASc≥2)",  "AHA/ACC/HRS Afib Guidelines 2023",     "Anticoagulation prescribed when CHA2DS2-VASc ≥ 2 (men) or ≥ 3 (women)",    "HIGH"),
    ("CGR-012", "AFIB",          "Rate Control Goal HR<80",            "AHA/ACC/HRS Afib Guidelines 2023",     "Resting heart rate documented below 80 bpm",                                "MEDIUM"),
    ("CGR-013", "BREAST_CANCER", "Annual Mammography",                 "NCCN Breast Cancer Surveillance v2",    "Annual mammogram for breast cancer survivors",                              "HIGH"),
    ("CGR-014", "BREAST_CANCER", "Bone Density on Endocrine Therapy",  "ASCO Bone Health Guidelines",           "DEXA scan at baseline and every 1-2 years on aromatase inhibitor/tamoxifen", "MEDIUM"),
    ("CGR-015", "COPD",          "Annual Spirometry",                  "GOLD COPD Guidelines 2024",             "Spirometry within the last 12 months for COPD staging and monitoring",      "MEDIUM"),
    ("CGR-016", "COPD",          "Influenza Vaccination",              "GOLD COPD Guidelines 2024",             "Annual influenza vaccine documented",                                        "HIGH"),
    ("CGR-017", "COPD",          "Pneumococcal Vaccination",           "GOLD COPD Guidelines 2024",             "PCV15 or PCV20 pneumococcal vaccine documented",                            "HIGH"),
    ("CGR-018", "CKD",           "eGFR Monitoring (Stage 3+)",         "KDIGO CKD Guidelines 2024",             "eGFR measured within the last 6 months for CKD Stage 3 and above",         "HIGH"),
    ("CGR-019", "CKD",           "ACE/ARB for Proteinuric CKD",       "KDIGO CKD Guidelines 2024",             "ACE inhibitor or ARB prescribed for CKD with ACR ≥ 30 mg/g",               "HIGH"),
    ("CGR-020", "DEPRESSION",    "PHQ-9 Follow-Up Screening",         "USPSTF Depression Screening 2023",      "PHQ-9 reassessment within 4-8 weeks of starting antidepressant therapy",    "HIGH"),
]

existing_cnt = spark.sql(
    f"SELECT COUNT(*) as cnt FROM `{CATALOG}`.`{SCHEMA}`.care_gap_rules"
).collect()[0]["cnt"]

if existing_cnt == 0:
    rules_df = spark.createDataFrame([
        Row(rule_id=r[0], condition=r[1], gap_name=r[2],
            guideline=r[3], check_description=r[4], priority=r[5])
        for r in rules
    ])
    rules_df.write.mode("overwrite").saveAsTable(f"`{CATALOG}`.`{SCHEMA}`.care_gap_rules")
    print(f"Seeded {len(rules)} care gap rules")
else:
    print(f"Care gap rules already present ({existing_cnt} rows) — skipping seed")

# COMMAND ----------

spark.sql(f"""
MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
USING (SELECT 'setup_care_gap_rules' AS step) AS s ON t.step = s.step
WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
    details = '{len(rules)} care gap rules seeded (HEDIS / ACC / ADA guidelines)'
WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
    VALUES ('setup_care_gap_rules', 'COMPLETED', current_timestamp(),
            '{len(rules)} care gap rules seeded (HEDIS / ACC / ADA guidelines)')
""")

print("Step 2 complete")


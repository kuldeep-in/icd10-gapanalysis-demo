# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 1 — Catalog Setup
# MAGIC Ensures the Unity Catalog exists and records completion in `bootstrap_status`.
# MAGIC
# MAGIC **Schema, tables, volume, and all UC permissions are created by `deploy.sh`
# MAGIC (via `setup_schema.py`) at deployment time — this notebook only handles catalog
# MAGIC creation, which requires Spark and cannot run via the SQL API.**

# COMMAND ----------

dbutils.widgets.text("catalog",   "my_catalog")
dbutils.widgets.text("schema",    "icd10_care_gap")
dbutils.widgets.text("app_sp_id", "")

CATALOG   = dbutils.widgets.get("catalog")
SCHEMA    = dbutils.widgets.get("schema")

print(f"Target: {CATALOG}.{SCHEMA}")

# COMMAND ----------

# Create catalog if it doesn't exist
existing_catalogs = [row.catalog for row in spark.sql("SHOW CATALOGS").collect()]
if CATALOG in existing_catalogs:
    print(f"Catalog '{CATALOG}' already exists — skipping creation")
else:
    spark.sql(f"CREATE CATALOG `{CATALOG}`")
    print(f"Catalog '{CATALOG}' created")

# COMMAND ----------

spark.sql(f"""
    MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
    USING (SELECT 'create_catalog' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET
        status = 'COMPLETED', updated_at = current_timestamp(),
        details = 'Catalog {CATALOG} verified. Schema, tables and grants managed by deploy.sh.'
    WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
        VALUES ('create_catalog', 'COMPLETED', current_timestamp(),
                'Catalog {CATALOG} verified. Schema, tables and grants managed by deploy.sh.')
""")

print("Step 1 complete")

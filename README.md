# ICD-10 Gap Analysis Demo

A Databricks App that performs ICD-10 clinical coding and care gap identification on
synthetic patient records.

- **ICD-10 Analyzer** — selects a patient, loads their SOAP note, and returns ICD-10 code
  suggestions with citations sourced from uploaded reference PDFs (powered by the Databricks
  Knowledge Assistant).
- **Care Gap Advisor** — identifies evidence-based care gaps (ADA, ACC/AHA, GOLD, NCCN,
  KDIGO) from the patient's clinical notes using the Foundation Model API
  (`databricks-claude-sonnet-4-6`).

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   Databricks App (Dash)                     │
│   icd10-gap-advisor                                         │
│                                                             │
│   Home tab         — setup status + job triggers            │
│   ICD-10 Analyzer  — Knowledge Assistant (RAG over PDFs)    │
│   Care Gap Advisor — Foundation Model API (Claude Sonnet)   │
└──────┬─────────────────────┬──────────────────┬─────────────┘
       │                     │                  │
  SQL Warehouse          KA Endpoint       FMAPI Endpoint
  (Delta queries)   (ICD-10 code RAG)  (databricks-claude-
       │                                  sonnet-4-6)
       │
  Unity Catalog: my_catalog.icd10_care_gap
  ├── patient_records          (25 synthetic SOAP notes)
  ├── care_gap_rules           (20 evidence-based rules)
  ├── icd10_analysis_results   (runtime — populated by app)
  ├── care_gap_findings        (runtime — populated by app)
  ├── bootstrap_status         (setup state tracker)
  └── Volume: icd10_reference_pdfs  (ICD-10 PDFs → KA)
```

---

## Prerequisites

| Requirement | Details |
|---|---|
| Databricks workspace | Unity Catalog enabled; user has `CREATE CATALOG` privilege (or catalog pre-created) |
| SQL Warehouse | An existing SQL Warehouse — note its ID before deploying |
| Databricks CLI | Installed and authenticated (`databricks configure` or existing profile) |
| Python | 3.11+ (for local dev only — app runs serverless on Databricks) |
| ICD-10 PDFs | Placed in `data/icd10_pdfs/` — must be ≤ 50 MB each |

---

## Quick Start

```bash
git clone <repo-url>
cd icd10-gapanalysis-demo

# Add ICD-10 reference PDFs
cp /path/to/icd10-*.pdf data/icd10_pdfs/

# Deploy
chmod +x deploy.sh
./deploy.sh --profile DEFAULT \
            --catalog my_catalog \
            --warehouse <your-warehouse-id>
```

Then open the app URL from the Databricks Apps UI and follow the setup steps in the Home tab.

For a full walkthrough of each deployment stage, what gets created, and how to use each
app tab, see **[INSTALLATION.md](INSTALLATION.md)**.

---

## Configuration

All configurable values are declared as variables in `databricks.yml`. Override them at
deploy time using `--var="key=value"` flags passed to `databricks bundle deploy`, or via
the `deploy.sh` helper flags.

| Variable | Default | Description |
|---|---|---|
| `catalog` | `my_catalog` | Unity Catalog name |
| `schema` | `icd10_care_gap` | Schema for all tables and the UC volume |
| `warehouse_id` | `<your-warehouse-id>` | SQL Warehouse used by the app for Delta queries |
| `ai_gateway_route` | `databricks-claude-sonnet-4-6` | AI Gateway / FMAPI endpoint name for care gap analysis |
| `model_provider` | `databricks` | `databricks` (FMAPI, no key needed) or `anthropic` (external key via secret) |
| `secret_scope` | `care-gap-demo` | Secret scope containing `anthropic-api-key` (Anthropic mode only) |
| `app_service_principal` | `""` | App SP UUID — resolved automatically by `deploy.sh`, never hardcode |
| `data_setup_job_name` | `ICD-10 Gap Demo — Data Setup` | Display name for Job 1 |
| `ai_setup_job_name` | `ICD-10 Gap Demo — AI Setup` | Display name for Job 2 |

---

## Project Structure

```
icd10-gapanalysis-demo/
├── databricks.yml                        # Bundle config — variables, targets, includes
├── deploy.sh                             # Full deployment script (app → SP read → bundle)
├── INSTALLATION.md                       # Stage-by-stage deployment and usage guide
├── resources/
│   ├── workflows.yml                     # Job 1 (Data Setup) + Job 2 (AI Setup)
│   └── ai_gateway.yml                    # AI Gateway reference (created by Job 2)
├── app/
│   ├── app.py                            # Dash app — Home, ICD-10 Analyzer, Care Gap Advisor
│   ├── app.yaml                          # App config — env vars + warehouse resource
│   └── requirements.txt                  # Python dependencies for the app
├── setup/
│   ├── 01_create_catalog.py              # UC catalog, schema, tables, volume, grants
│   ├── 02_ingest_patient_json.py         # patient_records.json → patient_records table
│   ├── 03_load_icd10_pdfs_to_volume.py   # PDFs → icd10_reference_pdfs volume
│   ├── 04_create_knowledge_assistant.py  # KA agent + UC volume as knowledge source
│   └── 05_configure_ai_gateway.py        # AI Gateway / FMAPI serving endpoint
└── data/
    ├── patient_records.json              # 25 synthetic SOAP-format clinical records
    └── icd10_pdfs/                       # Drop ICD-10 reference PDFs here (≤ 50 MB each)
        └── .gitkeep
```

---

## Notes

- PDF indexing by the Knowledge Assistant runs asynchronously after Job 2 and typically
  takes 20–60 minutes. The ICD-10 Analyzer works with partial results during indexing but
  full PDF citations are only available once indexing completes.
- Care gap rules are stored in the `care_gap_rules` Delta table — add, edit, or deactivate
  rules with SQL without any code changes or redeployment.
- The default model provider is `databricks` — no external API keys are required.
  Set `model_provider=anthropic` and configure the `care-gap-demo` secret scope to use an
  external Anthropic key instead.
- All setup notebooks are idempotent and safe to re-run.

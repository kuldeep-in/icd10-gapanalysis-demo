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
  Unity Catalog: <catalog>.<schema>
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
| Databricks CLI | Installed and authenticated (`databricks configure` or existing profile) |
| Python | 3.8+ stdlib only — no additional packages required |
| Terraform | Installed locally — required by `databricks bundle deploy` |

> **SQL Warehouse and Knowledge Assistant** are resolved automatically by `setup_resources.py`
> during deployment. You do not need to create them in advance.

---

## Quick Start

```bash
git clone <repo-url>
cd icd10-gapanalysis-demo

# 1. Update workspace host in databricks.yml
#    targets.dev.workspace.host: https://adb-<id>.azuredatabricks.net

# 2. Deploy — warehouse and KA are auto-resolved
chmod +x deploy.sh
./deploy.sh --profile DEFAULT \
            --catalog my_catalog \
            --app-path /Workspace/Users/you@example.com/icd10-gapanalysis-demo/app
```

Then open the app URL from the Databricks Apps UI and follow the setup steps in the Home tab.

For a full walkthrough of each deployment stage, what gets created, and how to use each
app tab, see **[INSTALLATION.md](INSTALLATION.md)**.

---

## Configuration

All configurable values live in `databricks.yml` as bundle variables. `deploy.sh` reads
them at startup, resolves infrastructure (warehouse, KA endpoint), and injects final values
into `app.yaml` and the bundle — no manual editing of `app.yaml` is needed.

Override any variable at deploy time with a `deploy.sh` flag or a `--var` passed directly
to `databricks bundle deploy`.

| Variable | Default | Description |
|---|---|---|
| `catalog` | `my_catalog` | Unity Catalog name |
| `schema` | `icd10_care_gap` | Schema for all tables and the UC volume |
| `warehouse_id` | `""` | SQL Warehouse ID — auto-created if empty |
| `ka_display_name` | `ICD-10 Clinical Reference Assistant` | Display name of the Knowledge Assistant — looked up by `setup_resources.py`; created if not found |
| `ka_endpoint_name` | `""` | KA serving endpoint name — resolved automatically at deploy time; do not set manually |
| `ka_name` | `""` | KA resource name (`knowledge-assistants/<uuid>`) — resolved automatically at deploy time; do not set manually |
| `data_setup_job_name` | `ICD-10 Gap Demo — Data Setup` | Display name for Job 1 |
| `ai_setup_job_name` | `ICD-10 Gap Demo — AI Setup` | Display name for Job 2 |
| `app_service_principal` | `""` | App SP UUID — resolved automatically by `deploy.sh`, never hardcode |

---

## Project Structure

```
icd10-gapanalysis-demo/
├── databricks.yml                          # Bundle config — single source of truth for all variables
├── deploy.sh                               # Full deployment script (6-step, self-contained)
├── setup_resources.py                      # Pre-deploy: resolves SQL warehouse + KA endpoint
├── README.md
├── INSTALLATION.md                         # Stage-by-stage deployment and usage guide
├── resources/
│   ├── workflows.yml                       # Job 1 (Data Setup) + Job 2 (AI Setup)
│   └── ai_gateway.yml                      # AI Gateway reference (created by Job 2)
├── app/
│   ├── app.py                              # Dash app — Home, ICD-10 Analyzer, Care Gap Advisor
│   ├── app.yaml                            # Generated by deploy.sh — do not edit manually
│   └── requirements.txt                    # Python dependencies for the app
├── setup/
│   ├── 01_create_catalog.py                # UC catalog, schema, tables, volume, grants
│   ├── 02_setup_care_gap_rules.py          # Seed care_gap_rules with 20 evidence-based rules
│   ├── 02_ingest_patient_json.py           # patient_records.json → patient_records table
│   ├── 03_load_icd10_pdfs_to_volume.py     # PDFs from GitHub → icd10_reference_pdfs volume
│   ├── 04_configure_knowledge_source.py    # Attach UC volume to pre-existing KA, trigger sync
│   └── 05_configure_ai_gateway.py          # AI Gateway / FMAPI serving endpoint
└── data/
    ├── patient_records.json                # 25 synthetic SOAP-format clinical records
    └── icd10_pdfs/                         # ICD-10 reference PDFs (loaded from GitHub by Job 1)
```

---

## How Care Gap Analysis Works

The Care Gap Advisor uses the **Databricks Foundation Model API** (`databricks-claude-sonnet-4-6`) — no external API keys or AI Gateway required.

When a user selects a patient and clicks Analyze, the app:

1. Queries `patient_records` via the SQL Warehouse to load the patient's full SOAP note
2. Queries `care_gap_rules` to load all active evidence-based rules
3. Constructs a prompt that includes both the SOAP note and the full rule set
4. Sends the prompt to `databricks-claude-sonnet-4-6` via the FMAPI serving endpoint
5. The model evaluates the clinical note against each rule and returns identified care gaps with reasoning
6. Results are written to `care_gap_findings` and displayed in the app

The rules are small enough (20 rows) to fit directly in the model's context window — there is no vector search involved. This means:

- **Add a new guideline** → insert a row in `care_gap_rules` with SQL
- **Update a rule** → update the row
- **Disable a rule** → set `is_active = false`

Changes take effect immediately on the next analysis — no code change or redeployment needed.

---

## How ICD-10 Code Suggestions Work

The ICD-10 Analyzer uses the **Databricks Knowledge Assistant** — a managed RAG service that indexes the uploaded ICD-10 reference PDFs into a vector store.

When a user submits a clinical note:

1. The app sends the text to the KA serving endpoint
2. The KA performs semantic search over the indexed PDFs
3. It returns relevant ICD-10 codes with citations from the source documents

PDF indexing runs asynchronously after Job 2 attaches the UC Volume to the KA (typically 20–60 minutes). The Home tab step 6 shows live indexing status via the KA management API.

---

## Notes

- `setup_resources.py` runs **before** the app is deployed. It looks up the Knowledge
  Assistant by display name using `GET /api/2.1/knowledge-assistants`, reads the
  `endpoint_name` and resource `name` from the response, and passes both into `app.yaml`
  and the bundle. If no KA with that display name exists, a new one is created automatically.
- Volume attachment and PDF indexing are handled by Job 2, task `configure_knowledge_source`
  — not by `setup_resources.py`. This keeps pre-deploy lightweight and idempotent.
- Home tab setup checks use live API calls — the KA source status is read directly from
  `GET /api/2.1/{ka-name}/knowledge-sources`, not from a Delta table.
- All setup notebooks are idempotent and safe to re-run.

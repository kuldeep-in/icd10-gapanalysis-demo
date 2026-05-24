# Installation Guide

This guide walks through every stage of deploying and configuring the ICD-10 Gap Analysis Demo,
from running `deploy.sh` through having a fully operational app.

---

## Pre-Installation — Configuration Values to Update

Before running any deployment command, update the placeholder values in the three
configuration files listed below. These are the only files you need to edit — everything
else is resolved automatically at deploy time.

---

### 1. `databricks.yml`

This is the Asset Bundle configuration. One value must be updated:

| Parameter | Location in file | Placeholder | What to set |
|-----------|-----------------|-------------|-------------|
| `workspace.host` | `targets.dev.workspace.host` | `https://<your-workspace>.azuredatabricks.net` | Your Databricks workspace URL (e.g. `https://adb-1234567890.12.azuredatabricks.net`) |

**How to find your workspace URL:** Copy it from the browser address bar when logged into Databricks — everything up to and including `.net` or `.com`.

```yaml
# databricks.yml — update this block
targets:
  dev:
    mode: development
    workspace:
      host: https://adb-1234567890.12.azuredatabricks.net   # ← replace
```

The `catalog`, `schema`, and `warehouse_id` variables have defaults that are overridden
by `deploy.sh` flags — you do not need to edit them in the file.

---

### 2. `app/app.yaml`

This file configures the Databricks App's runtime environment. Two values must be updated:

| Parameter | Key in file | Placeholder | What to set |
|-----------|-------------|-------------|-------------|
| SQL Warehouse ID (env var) | `env.DATABRICKS_WAREHOUSE_ID` | `<your-warehouse-id>` | Your SQL Warehouse ID (e.g. `ab123456789`) |
| SQL Warehouse ID (resource) | `resources.sql_warehouse.id` | `<your-warehouse-id>` | Same SQL Warehouse ID as above |

**How to find your Warehouse ID:** In the Databricks UI go to **SQL → SQL Warehouses**, click your warehouse, then copy the ID from the **Connection details** tab or the URL.

```yaml
# app/app.yaml — update both occurrences of <your-warehouse-id>
env:
  - name: DATABRICKS_WAREHOUSE_ID
    value: "ab123456789"        # ← replace

resources:
  - name: "sql-warehouse"
    sql_warehouse:
      id: "ab123456789"         # ← replace (same value)
      permission: "CAN_USE"
```

Optionally, if you are using a non-default catalog or schema, also update:

| Parameter | Key in file | Default | When to change |
|-----------|-------------|---------|----------------|
| Unity Catalog name | `env.UC_CATALOG` | `my_catalog` | If your catalog has a different name |
| Schema name | `env.UC_SCHEMA` | `icd10_care_gap` | If you want a different schema name |
| AI endpoint name | `env.AI_GATEWAY_ROUTE` | `databricks-claude-sonnet-4-6` | Only if using Anthropic mode — change to `care-gap-advisor` |

---

### 3. `deploy.sh`

`deploy.sh` accepts all its key values as command-line flags — you do not need to edit
the file directly. However, one value **must** be set correctly before the script will work:

| Parameter | Variable in script | What to set | How |
|-----------|-------------------|-------------|-----|
| App source path | `APP_SOURCE_PATH` | Workspace path to the `app/` directory | Edit the default in the script **or** the bundle resolves it via `${workspace.file_path}/app` automatically at deploy time |
| SQL Warehouse ID | `WAREHOUSE_ID` | Your warehouse ID | Pass `--warehouse <id>` flag |
| Databricks CLI profile | `PROFILE` | Your configured CLI profile name | Pass `--profile <name>` flag |
| Catalog | `CATALOG` | Unity Catalog name | Pass `--catalog <name>` flag (default: `my_catalog`) |

The recommended invocation (no file edits needed):

```bash
./deploy.sh --profile DEFAULT \
            --catalog my_catalog \
            --warehouse ab123456789
```

---

### Optional — Anthropic API Key (Anthropic mode only)

By default the app uses the `databricks-claude-sonnet-4-6` Foundation Model API — **no
API key is required**. If you want to route care gap analysis through an external
Anthropic key instead, create a Databricks secret before running `deploy.sh`:

```bash
# Create the secret scope
databricks secrets create-scope care-gap-demo --profile DEFAULT

# Store the Anthropic API key
databricks secrets put-secret care-gap-demo anthropic-api-key --profile DEFAULT
# (enter your Anthropic API key when prompted)
```

Then pass `--model-provider anthropic` to `deploy.sh`:

```bash
./deploy.sh --profile DEFAULT \
            --catalog my_catalog \
            --warehouse <your-warehouse-id> \
            --model-provider anthropic
```

Also update `AI_GATEWAY_ROUTE` in `app/app.yaml` from `databricks-claude-sonnet-4-6` to
`care-gap-advisor` to match the AI Gateway endpoint that Job 2 will create.

---

### Summary Checklist

Before running `deploy.sh`, confirm:

- [ ] `databricks.yml` — `workspace.host` updated to your workspace URL
- [ ] `app/app.yaml` — `DATABRICKS_WAREHOUSE_ID` and `resources.sql_warehouse.id` both set to your warehouse ID
- [ ] `app/app.yaml` — `UC_CATALOG` matches the catalog you intend to use (if not `my_catalog`)
- [ ] ICD-10 reference PDFs placed in `data/icd10_pdfs/` (at least one PDF required for the ICD-10 Analyzer)
- [ ] Databricks CLI authenticated: `databricks auth profiles` shows a valid profile
- [ ] *(Anthropic mode only)* Secret scope `care-gap-demo` created with key `anthropic-api-key`

---

## Stage 1 — App Deployment (`deploy.sh`)

**Trigger:** Run `./deploy.sh` from the project root.

```bash
chmod +x deploy.sh
./deploy.sh --profile DEFAULT \
            --catalog my_catalog \
            --warehouse <your-warehouse-id>
```

### What happens

`deploy.sh` runs three sequential steps so the app service principal (SP) identity is
resolved before the Asset Bundle is deployed.

| Step | Command | What gets created |
|------|---------|-------------------|
| **Step 1** | `databricks apps deploy` | Databricks App (`icd10-gap-advisor`) — app container + dedicated service principal |
| **Step 2a** | `databricks apps get` | Reads the app SP `client_id` that was just provisioned |
| **Step 2b** | `databricks permissions update warehouses` | Grants `CAN_USE` on your SQL Warehouse to the app SP |
| **Step 3** | `databricks bundle deploy` | Asset Bundle — deploys Job 1 (Data Setup) and Job 2 (AI Setup) with `CAN_MANAGE_RUN` for the app SP |

### Resources created after this stage

| Resource | Type | Location / ID |
|----------|------|---------------|
| Databricks App | App | Workspace → Apps → `icd10-gap-advisor` |
| App Service Principal | IAM | Auto-created by the app; UUID available in Apps UI |
| Job 1: Data Setup | Workflow Job | Workspace → Workflows → `ICD-10 Gap Demo — Data Setup` |
| Job 2: AI Setup | Workflow Job | Workspace → Workflows → `ICD-10 Gap Demo — AI Setup` |

### Verify

```bash
databricks apps get icd10-gap-advisor --profile DEFAULT
databricks jobs list --profile DEFAULT | grep "ICD-10 Gap Demo"
```

---

## Stage 2 — App First Launch

**Trigger:** Open the app URL from the Databricks Apps UI.

Navigate to **Workspace → Apps → icd10-gap-advisor** and click **Open**. The app starts
and its Home tab loads automatically.

### What the app does on startup

1. Reads `CATALOG` and `SCHEMA` from environment variables (set in `app.yaml`).
2. Queries the `bootstrap_status` Delta table to determine which setup steps are complete.
3. If `bootstrap_status` does not yet exist (first ever launch, before Job 1 has run),
   the Home tab shows all six steps in **Not Started** state.

### What you see

- **Home tab** — Six accordion steps grouped by job, all showing grey "Not Started" badges.
- **ICD-10 Analyzer tab** — Disabled until steps 1–3 complete.
- **Care Gap Advisor tab** — Disabled until steps 1–2 complete.
- **Settings tab** — Read-only view of all configured values (catalog, schema, warehouse ID, model endpoint, app service principal). No editable fields — all values are set at deploy time via `app.yaml`.

---

## Stage 3 — Options Available from the Home Tab on First Launch

The Home tab is the control panel for all setup operations. It is always accessible, even
before any jobs have run.

### Controls

| Control | Description |
|---------|-------------|
| **Run Data Setup (Job 1)** | Triggers Job 1 — creates Unity Catalog objects, ingests patient data, uploads PDFs |
| **Run AI Setup (Job 2)** | Triggers Job 2 — creates Knowledge Assistant and AI Gateway endpoint |
| **Refresh Now** | Re-queries `bootstrap_status` and updates all step badges without a page reload |
| **View Run** links | Appear next to each step once a job has been triggered; link directly to the live Databricks run page |

### Step accordion

Each of the six bootstrap steps is shown as a collapsible accordion row:

- Steps that are **not yet started** or **in progress** are expanded by default.
- Steps that are **completed** collapse automatically.
- Click any step header to expand/collapse it and read the step description and last status message.

### Settings tab

The **Settings** tab shows the current deployment configuration as a read-only reference:

| Section | Values shown |
|---------|-------------|
| Unity Catalog | Catalog name, Schema name |
| Infrastructure | SQL Warehouse ID (shown in red if not set) |
| AI Configuration | Model endpoint name, Job 1 name, Job 2 name |
| App Identity | Service principal resolved at startup |

All values come from `app.yaml` environment variables and are applied at deploy time. To
change any value, update `app.yaml` and redeploy the app — no edits inside the app are
needed or supported.

---

## Stage 4 — Job 1: Data Setup

**Trigger:** Click **Run Data Setup (Job 1)** on the Home tab, or trigger manually:

```bash
databricks jobs run-now --job-id <job1-id> --profile DEFAULT
```

Job 1 runs one sequential task followed by three parallel tasks.

### Tasks

#### Task 1: `create_catalog` (sequential, runs first)

Notebook: `setup/01_create_catalog.py`

| Resource | Type | Details |
|----------|------|---------|
| UC Catalog | Unity Catalog | Created if it does not exist; configurable via `--catalog` flag |
| Schema `icd10_care_gap` | Unity Catalog Schema | Created inside the catalog |
| `patient_records` | Delta Table | Stores 25 synthetic SOAP-format clinical notes |
| `care_gap_rules` | Delta Table | Stores 20 evidence-based care gap rules (ADA, ACC/AHA, GOLD, NCCN, KDIGO) |
| `icd10_analysis_results` | Delta Table | Populated at runtime by the ICD-10 Analyzer tab |
| `care_gap_findings` | Delta Table | Populated at runtime by the Care Gap Advisor tab |
| `bootstrap_status` | Delta Table | Setup state tracker; read by the Home tab |
| `icd10_reference_pdfs` | UC Volume | Managed volume for ICD-10 reference PDF files |
| Care gap rules (seed data) | Delta rows | 20 rules inserted into `care_gap_rules` |

#### Tasks 2–4 (parallel, run after `create_catalog`)

| Task | Notebook | What it does |
|------|---------|-------------|
| `ingest_patient_data` | `setup/02_ingest_patient_json.py` | Reads `data/patient_records.json` via the Workspace REST API and writes 25 synthetic patient records to the `patient_records` Delta table |
| `load_icd10_pdfs` | `setup/03_load_icd10_pdfs_to_volume.py` | Lists and downloads PDFs from `data/icd10_pdfs/` via the Workspace Files API and writes them to the `icd10_reference_pdfs` UC Volume |

> All tasks are idempotent — safe to re-run. Tables use `IF NOT EXISTS`; the patient
> ingest checks row count before writing.

### After Job 1 completes

- Home tab steps 1–3 show green **Completed** badges.
- The `bootstrap_status` table is populated with timestamps for each completed step.
- The **Care Gap Advisor** tab becomes operational (it needs only the patient records and
  care gap rules tables, plus the Foundation Model API endpoint).

---

## Stage 5 — Job 2: AI Setup

**Trigger:** Click **Run AI Setup (Job 2)** on the Home tab after Job 1 is complete.

> Job 2 **must run after Job 1** — the UC volume must exist and contain PDFs before the
> Knowledge Assistant is created.

Job 2 runs two parallel tasks on serverless compute (no cluster required).

### Tasks

#### Task 1: `create_knowledge_assistant`

Notebook: `setup/04_create_knowledge_assistant.py`

| Resource | Type | Details |
|----------|------|---------|
| Knowledge Assistant agent | Databricks Agent | Named `ICD-10 Coding Assistant`; backed by Genie / RAG framework |
| Knowledge source attachment | KA config | UC volume `icd10_reference_pdfs` attached as a PDF knowledge source |
| KA serving endpoint | Model Serving | Auto-provisioned by Databricks; endpoint name stored in `bootstrap_status` |
| PDF indexing job | Background process | Asynchronous; indexes all PDFs in the volume (20–60 min) |

#### Task 2: `configure_ai_gateway`

Notebook: `setup/05_configure_ai_gateway.py`

| Resource | Type | Details |
|----------|------|---------|
| AI Gateway endpoint | Model Serving | Named `care-gap-advisor` (Anthropic key mode) or reuses `databricks-claude-sonnet-4-6` (FMAPI mode) |

**Model selection logic:**

| Condition | Endpoint used |
|-----------|--------------|
| Secret scope `care-gap-demo` with key `anthropic-api-key` exists | External Anthropic Claude via AI Gateway (`care-gap-advisor`) |
| Secret scope missing or key absent | `databricks-claude-sonnet-4-6` Foundation Model API (no external key needed) |

> The recommended setup is the FMAPI path — no secret management needed, Claude Sonnet
> is already available as a built-in Databricks Foundation Model endpoint.

### After Job 2 completes

- Home tab steps 4–5 show green **Completed** badges.
- Step 6 (PDF indexing) shows **In Progress** — PDF indexing continues asynchronously.
- The **ICD-10 Analyzer** tab becomes available but shows a banner while PDFs are indexing.
- Full ICD-10 code suggestions with PDF citations are available once step 6 completes
  (typically 20–60 minutes after Job 2 finishes).

---

## Stage 6 — Using the ICD-10 Analyzer Tab

**Prerequisite:** Steps 1–5 complete (step 6 / PDF indexing in progress is acceptable;
code suggestions will return results but without PDF citations until indexing finishes).

### Workflow

1. Select a patient from the **Patient** dropdown — 25 synthetic records are available.
2. The patient's clinical SOAP note loads into the text area automatically.
3. Click **Analyze ICD-10 Codes**.
4. The app calls the Knowledge Assistant serving endpoint (real-time inference) with the
   clinical note as the query.
5. The KA returns suggested ICD-10 codes with citations sourced from the indexed PDFs.

### What happens under the hood

- The app sends the SOAP note text to the KA endpoint via a REST call
  (`POST /serving-endpoints/<ka-endpoint>/invocations`).
- The KA performs RAG over the indexed ICD-10 reference PDFs and returns code
  suggestions ranked by relevance.
- Results are stored to `icd10_analysis_results` for audit and review.

### Notes

- A yellow banner is shown while PDF indexing (step 6) is still in progress.
- Once indexing completes (step 6 green), citations appear alongside each suggested code.

---

## Stage 7 — Using the Care Gap Advisor Tab

**Prerequisite:** Steps 1–2 complete (care gap rules table and Foundation Model API endpoint available).

### Workflow

1. Select a patient from the **Patient** dropdown.
2. Click **Identify Care Gaps**.
3. The app loads the patient's clinical SOAP note and all 20 active care gap rules from
   the `care_gap_rules` Delta table.
4. The note and rules are sent to the Foundation Model API (`databricks-claude-sonnet-4-6`
   or the configured AI Gateway endpoint).
5. The LLM compares the clinical note against all rules and returns applicable gaps with:
   - **Priority** (HIGH / MEDIUM / LOW)
   - **Finding** — specific clinical observation from the note
   - **Recommended action** — evidence-based next step
   - **Guideline reference** — source guideline (e.g., ADA 2024, ACC/AHA 2023)
6. Findings are stored to `care_gap_findings`.

### Care gap rules

Rules are stored in the `care_gap_rules` Delta table and evaluated dynamically at query
time — no code changes are needed to add, edit, or deactivate rules.

To add a rule:

```sql
INSERT INTO my_catalog.icd10_care_gap.care_gap_rules
VALUES ('CGR-021', 'T2DM', 'Statin Therapy', 'ADA 2024',
        'Statin prescribed for T2DM patients aged 40–75', 'HIGH');
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "You do not have permission to use the SQL Warehouse" | App SP missing `CAN_USE` on warehouse | Re-run `deploy.sh` — Step 2b grants the permission; or grant manually in the Warehouse permissions UI |
| Home tab shows all steps as "Not Started" | Job 1 hasn't run yet, or `bootstrap_status` table doesn't exist | Click **Run Data Setup (Job 1)** |
| ICD-10 Analyzer returns no citations | PDF indexing (step 6) still in progress | Wait 20–60 min; click **Refresh Now** to check step 6 status |
| Care Gap Advisor returns an error | AI Gateway endpoint not found | Run Job 2; verify `AI_GATEWAY_ROUTE` in `app.yaml` matches the deployed endpoint name |
| Job 2 fails on `create_knowledge_assistant` | UC volume doesn't exist or is empty | Confirm Job 1 completed successfully and PDFs are in `data/icd10_pdfs/` |
| Job 2 fails on `configure_ai_gateway` | Secret scope `care-gap-demo` missing | Normal — notebook falls back to `databricks-claude-sonnet-4-6` FMAPI automatically |

---

## Re-running Jobs

All setup notebooks are idempotent — safe to re-run at any time:

- **Job 1** can be re-run to add new patient records or upload additional ICD-10 PDFs.
- **Job 2** can be re-run to re-create the Knowledge Assistant or reconfigure the AI Gateway.
- Re-running a job that has already completed does not duplicate data or resources.

# Installation Guide

This guide walks through every stage of deploying and configuring the ICD-10 Gap Analysis Demo,
from running `deploy.sh` through having a fully operational app.

---

## Pre-Installation — Configuration Values to Update

Only **one file** needs editing before deployment: `databricks.yml`. Everything else —
`app.yaml`, SQL warehouse, KA endpoint — is resolved and generated automatically at deploy time.

---

### `databricks.yml`

This is the single source of truth for all configuration. Update the values below before
running `deploy.sh`:

#### Required

| Variable | Location | What to set |
|----------|----------|-------------|
| `workspace.host` | `targets.dev.workspace.host` | Your Databricks workspace URL |

```yaml
targets:
  dev:
    mode: development
    workspace:
      host: https://adb-1234567890.12.azuredatabricks.net   # ← your workspace URL
```

#### Optional overrides (have working defaults)

| Variable | Default | When to change |
|----------|---------|----------------|
| `catalog` | `my_catalog` | If your catalog has a different name |
| `schema` | `icd10_care_gap` | If you want a different schema name |
| `warehouse_id` | `""` | Set to an existing warehouse ID to skip auto-creation |
| `ka_display_name` | `ICD-10 Clinical Reference Assistant` | Change if you want a different KA name |
| `model_provider` | `databricks` | Set to `anthropic` to use an external Anthropic key |
| `ai_gateway_route` | `care-gap-advisor` | Only relevant if `model_provider=anthropic` |

> **`app.yaml` — do not edit.** It is overwritten by `deploy.sh` at every deploy. All
> environment variables are injected from resolved values at deploy time.

---

### Optional — Anthropic API Key (Anthropic mode only)

By default the app uses the `databricks-claude-sonnet-4-6` Foundation Model API — **no
API key is required**. If you want to route care gap analysis through an external
Anthropic key instead:

```bash
# Create the secret scope
databricks secrets create-scope care-gap-demo --profile DEFAULT

# Store the Anthropic API key
databricks secrets put-secret care-gap-demo anthropic-api-key --profile DEFAULT
# (enter your Anthropic API key when prompted)
```

Then pass `--model-provider anthropic` to `deploy.sh`.

---

### Pre-Installation Checklist

- [ ] `databricks.yml` — `workspace.host` updated to your workspace URL
- [ ] Databricks CLI authenticated: `databricks auth profiles` shows a valid profile
- [ ] Terraform installed: `terraform -version` returns a version
- [ ] *(Anthropic mode only)* Secret scope `care-gap-demo` created with key `anthropic-api-key`

---

## Stage 1 — Deployment (`deploy.sh`)

**Trigger:** Run `./deploy.sh` from the project root.

```bash
chmod +x deploy.sh
./deploy.sh --profile DEFAULT \
            --catalog my_catalog \
            --app-path /Workspace/Users/you@example.com/icd10-gapanalysis-demo/app
```

All flags are optional except `--app-path`. Defaults are read from `databricks.yml`.

| Flag | Default (from `databricks.yml`) | Description |
|------|---------------------------------|-------------|
| `--app-path` | *(required)* | Workspace path to the `app/` directory |
| `--profile` | `DEFAULT` | Databricks CLI profile |
| `--catalog` | `my_catalog` | Unity Catalog name |
| `--schema` | `icd10_care_gap` | Schema name |
| `--warehouse-id` | `""` (auto-created) | SQL Warehouse ID |
| `--ka-display-name` | `ICD-10 Clinical Reference Assistant` | KA display name |
| `--ai-gateway-route` | `care-gap-advisor` | AI Gateway route name |
| `--model-provider` | `databricks` | `databricks` or `anthropic` |

### What deploy.sh does — step by step

#### Step 1 — Read defaults from `databricks.yml`

All variable defaults are parsed from `databricks.yml` via a Python heredoc. Command-line
flags then override any of these values. No values are hardcoded in `deploy.sh`.

#### Step 2 — Resolve SQL warehouse and KA endpoint (`setup_resources.py`)

`setup_resources.py` is called with the resolved config. It outputs two shell variable
assignments consumed via `eval`:

**SQL Warehouse resolution:**

| Condition | Action |
|-----------|--------|
| `warehouse_id` is set and valid | Validates it exists; uses it |
| `warehouse_id` is empty or invalid | Creates a new serverless SQL Warehouse named `icd10-gap-demo-warehouse` |

**Knowledge Assistant endpoint resolution:**

| Condition | Action |
|-----------|--------|
| KA with matching `display_name` found | Reads `endpoint_name` from the API response — no creation needed |
| Multiple KAs with same display name | Uses the most recently created one (sorted by `create_time`) |
| No KA with that display name found | Creates a new KA via `POST /api/2.1/knowledge-assistants`, waits for endpoint to be `READY` |

> Volume attachment and PDF indexing are **not** done here — that is Job 2's responsibility.
> `setup_resources.py` only ensures the KA serving endpoint exists and is reachable.

After this step, `WAREHOUSE_ID` and `KA_ENDPOINT_NAME` are set in the shell environment.

#### Step 3 — Generate `app.yaml`

`deploy.sh` writes a fresh `app.yaml` with all resolved values and uploads it to the
workspace path. This file is the runtime configuration for the Databricks App.

```yaml
# Generated by deploy.sh — do not edit manually
command: ["python", "app.py"]

env:
  - name: UC_CATALOG
    value: "<resolved catalog>"
  - name: UC_SCHEMA
    value: "<resolved schema>"
  - name: AI_GATEWAY_ROUTE
    value: "<resolved ai_gateway_route>"
  - name: DATABRICKS_WAREHOUSE_ID
    value: "<resolved warehouse_id>"
  - name: KA_ENDPOINT_NAME
    value: "<resolved ka_endpoint_name>"
  - name: DATA_SETUP_JOB_NAME
    value: "<resolved data_setup_job_name>"
  - name: AI_SETUP_JOB_NAME
    value: "<resolved ai_setup_job_name>"
  - name: MODEL_PROVIDER
    value: "<resolved model_provider>"
  - name: SECRET_SCOPE
    value: "<resolved secret_scope>"

resources:
  - name: sql-warehouse
    sql_warehouse:
      id: "<resolved warehouse_id>"
      permission: "CAN_USE"
```

#### Step 4 — Deploy the Databricks App

```bash
databricks apps deploy icd10-gap-advisor \
  --source-code-path <app-path> \
  --mode SNAPSHOT
```

This creates (or updates) the `icd10-gap-advisor` Databricks App and provisions its
dedicated service principal.

#### Step 5 — Grant warehouse CAN_USE to app service principal

```bash
databricks apps get icd10-gap-advisor   # → reads service_principal_client_id
databricks permissions update warehouses <warehouse-id> \
  --json '{"access_control_list": [{"service_principal_name": "<sp-id>", "permission_level": "CAN_USE"}]}'
```

The app SP needs `CAN_USE` on the SQL Warehouse to run Delta queries.

#### Step 6 — Deploy the Asset Bundle (Jobs)

```bash
databricks bundle deploy \
  --var="catalog=..." \
  --var="schema=..." \
  --var="warehouse_id=..." \
  --var="ka_endpoint_name=..." \
  --var="ai_gateway_route=..." \
  --var="model_provider=..." \
  --var="secret_scope=..." \
  --var="data_setup_job_name=..." \
  --var="ai_setup_job_name=..." \
  --var="app_service_principal=<sp-id>"
```

Creates Job 1 (Data Setup) and Job 2 (AI Setup) as Databricks Workflow Jobs, with
`CAN_MANAGE_RUN` granted to the app SP so the app can trigger them.

### Resources created after Stage 1

| Resource | Type | Location |
|----------|------|----------|
| Databricks App | App | Workspace → Apps → `icd10-gap-advisor` |
| App Service Principal | IAM | Auto-created by the app |
| SQL Warehouse | SQL | Auto-created if not pre-existing |
| KA Serving Endpoint | Model Serving | Looked up or auto-created by `setup_resources.py` |
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

Navigate to **Workspace → Apps → icd10-gap-advisor** and click **Open**.

### What the app does on startup

1. Reads all config from environment variables set in `app.yaml` (`UC_CATALOG`, `UC_SCHEMA`,
   `KA_ENDPOINT_NAME`, `DATABRICKS_WAREHOUSE_ID`, etc.).
2. Queries the `bootstrap_status` Delta table to determine which setup steps are complete.
3. If `bootstrap_status` does not yet exist (before Job 1 has run), the Home tab shows all
   six steps in **Not Started** state.

### What you see

- **Home tab** — Six accordion steps, all showing grey "Not Started" badges.
- **ICD-10 Analyzer tab** — Disabled until Job 1 completes (steps 1–3).
- **Care Gap Advisor tab** — Disabled until Job 1 completes (steps 1–2).
- **Settings tab** — Read-only view of all configured values. All values come from `app.yaml`
  and are set at deploy time.

---

## Stage 3 — Home Tab Controls

The Home tab is the control panel for all setup operations. It is always accessible.

| Control | Description |
|---------|-------------|
| **Run Data Setup (Job 1)** | Triggers Job 1 — creates UC objects, ingests patient data, downloads PDFs |
| **Run AI Setup (Job 2)** | Triggers Job 2 — attaches the PDF volume to the KA and configures AI Gateway |
| **Refresh Now** | Re-queries `bootstrap_status` and updates all step badges without a page reload |

Each of the six steps is shown as a collapsible accordion row. Steps are colour-coded:
- **Grey** — Not started
- **Yellow** — In progress (job running)
- **Green** — Completed (written to `bootstrap_status`)
- **Red** — Failed

---

## Stage 4 — Job 1: Data Setup

**Trigger:** Click **Run Data Setup (Job 1)** in the Home tab.

```bash
# Or trigger manually:
databricks jobs run-now --job-id <job1-id> --profile DEFAULT
```

Job 1 runs one sequential task followed by three parallel tasks, all on serverless compute.

### Tasks

#### Task 1: `create_catalog` (runs first)

Notebook: `setup/01_create_catalog.py`

Creates all Unity Catalog resources and grants. Subsequent tasks depend on this completing.

| Resource | Details |
|----------|---------|
| UC Catalog | Created if it does not exist |
| Schema | `<catalog>.<schema>` |
| `patient_records` | Delta table — 25 synthetic SOAP-format clinical notes |
| `care_gap_rules` | Delta table — 20 evidence-based care gap rules |
| `icd10_analysis_results` | Delta table — runtime, populated by ICD-10 Analyzer |
| `care_gap_findings` | Delta table — runtime, populated by Care Gap Advisor |
| `bootstrap_status` | Delta table — setup state tracker, read by the Home tab |
| `icd10_reference_pdfs` | UC Volume — managed volume for ICD-10 reference PDFs |
| Grants | `CAN_USE` schema + tables granted to the app service principal |

#### Tasks 2–4 (parallel, run after `create_catalog`)

| Task | Notebook | What it does |
|------|----------|--------------|
| `setup_care_gap_rules` | `setup/02_setup_care_gap_rules.py` | Seeds `care_gap_rules` with 20 evidence-based HEDIS/ACC/ADA rules |
| `ingest_patient_data` | `setup/02_ingest_patient_json.py` | Writes 25 synthetic patient records to `patient_records` Delta table |
| `load_icd10_pdfs` | `setup/03_load_icd10_pdfs_to_volume.py` | Downloads ICD-10 reference PDFs from GitHub and streams them into the `icd10_reference_pdfs` UC Volume |

All tasks are idempotent — safe to re-run.

### After Job 1 completes

- Home tab steps 1–3 show green **Completed** badges.
- **Care Gap Advisor** becomes operational — it needs only the patient records, care gap
  rules, and the Foundation Model API endpoint (all available after Job 1).
- **ICD-10 Analyzer** tab becomes visible but requires Job 2 to complete before use.

---

## Stage 5 — Job 2: AI Setup

**Trigger:** Click **Run AI Setup (Job 2)** in the Home tab after Job 1 completes.

> Job 2 **must run after Job 1** — the `icd10_reference_pdfs` UC Volume must exist and
> contain PDFs before the knowledge source can be attached.

Job 2 runs two parallel tasks on serverless compute.

### Tasks

#### Task 1: `configure_knowledge_source`

Notebook: `setup/04_configure_knowledge_source.py`

The Knowledge Assistant already exists (created by `setup_resources.py` during deploy).
This task attaches the UC Volume to it as a knowledge source.

| Step | What happens |
|------|-------------|
| Locate KA | Calls `GET /api/2.1/knowledge-assistants`, finds the KA whose `endpoint_name` matches `KA_ENDPOINT_NAME` |
| Check sources | Calls `GET /api/2.1/<ka-name>/knowledge-sources` to see if the volume is already attached |
| Attach volume | If not attached: calls `POST /api/2.1/<ka-name>/knowledge-sources` with the UC Volume path |
| Already attached | If already attached: logs current state and ingestion details, skips re-attachment |
| Update status | Writes `ka_configured_with_icd10_files → COMPLETED` to `bootstrap_status` |

Once the volume is attached, the KA begins indexing PDFs asynchronously. Indexing typically
takes 20–60 minutes. The Home tab step 6 status is driven by `bootstrap_status` — it shows
**Completed** as soon as this task finishes (meaning the source is attached and sync
triggered), not when indexing finishes.

#### Task 2: `configure_ai_gateway`

Notebook: `setup/05_configure_ai_gateway.py`

Creates the AI Gateway serving endpoint for the Care Gap Advisor.

| Condition | Endpoint used |
|-----------|--------------|
| Secret scope `care-gap-demo` with key `anthropic-api-key` present | External Anthropic Claude via AI Gateway (`care-gap-advisor`) |
| Secret scope missing or key absent | `databricks-claude-sonnet-4-6` Foundation Model API (no external key needed) |

The recommended path is FMAPI — no secret management required.

### After Job 2 completes

- Home tab steps 4–6 show green **Completed** badges.
- **ICD-10 Analyzer** tab becomes fully operational.
- PDF indexing continues asynchronously — full citations appear as indexing progresses.

---

## Stage 6 — Using the ICD-10 Analyzer Tab

**Prerequisite:** Steps 1–6 complete (the KA volume source must be configured).

### Workflow

1. Select a patient from the **Patient** dropdown — 25 synthetic records available.
2. The patient's SOAP note loads automatically.
3. Click **Analyze ICD-10 Codes**.
4. The app calls the KA serving endpoint (`KA_ENDPOINT_NAME`) with the SOAP note as the query.
5. The KA performs RAG over the indexed ICD-10 reference PDFs and returns code suggestions
   ranked by relevance, each with a citation from the source PDF.
6. Results are stored to `icd10_analysis_results` for audit and review.

---

## Stage 7 — Using the Care Gap Advisor Tab

**Prerequisite:** Steps 1–2 complete (patient records and care gap rules tables must exist).

### Workflow

1. Select a patient from the **Patient** dropdown.
2. Click **Identify Care Gaps**.
3. The app loads the patient's SOAP note and all active care gap rules from `care_gap_rules`.
4. The note and rules are sent to the Foundation Model API.
5. The LLM returns applicable care gaps with priority, finding, recommended action, and
   guideline reference (e.g., ADA 2024, ACC/AHA 2023).
6. Findings are stored to `care_gap_findings`.

### Care gap rules

Rules live in the `care_gap_rules` Delta table and are evaluated dynamically at query time.
Add, edit, or deactivate rules with SQL — no code changes or redeployment needed:

```sql
INSERT INTO my_catalog.icd10_care_gap.care_gap_rules
VALUES ('CGR-021', 'T2DM', 'Statin Therapy', 'ADA 2024',
        'Statin prescribed for T2DM patients aged 40–75', 'HIGH');
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "You do not have permission to use the SQL Warehouse" | App SP missing `CAN_USE` | Re-run `deploy.sh` — Step 5 grants the permission |
| Home tab shows all steps as "Not Started" | Job 1 hasn't run | Click **Run Data Setup (Job 1)** |
| ICD-10 Analyzer tab is disabled | Job 2 hasn't run | Click **Run AI Setup (Job 2)** |
| ICD-10 Analyzer returns no results | PDF indexing still in progress | Wait 20–60 min; citations appear as indexing progresses |
| Job 2 `configure_knowledge_source` fails with "No KA found" | `KA_ENDPOINT_NAME` env var doesn't match any live KA | Re-run `deploy.sh` — `setup_resources.py` will re-resolve and regenerate `app.yaml` |
| Job 2 `configure_ai_gateway` fails | Secret scope `care-gap-demo` missing | Normal in `databricks` mode — this task falls back to FMAPI automatically |
| Step 6 shows "Not yet run" after Job 2 | `04_configure_knowledge_source` notebook failed | Check the Job 2 run log; re-run Job 2 |

---

## Re-running Jobs

All setup notebooks are idempotent — safe to re-run at any time:

- **Job 1** can be re-run to add new patient records or update PDF files in the UC Volume.
- **Job 2** can be re-run to re-attach the knowledge source or reconfigure the AI Gateway.
- Re-running a completed job does not duplicate data or resources.

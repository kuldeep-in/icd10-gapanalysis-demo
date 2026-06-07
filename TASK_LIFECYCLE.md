# Setup Page — Task Lifecycle

This document explains how each setup task transitions between states, what
triggers each transition, and how the Setup page determines what to display.

---

## How Status Is Determined

The Setup page does **not** poll in real time. On every **Refresh** click
(or on first load), a single SQL query runs:

```sql
SELECT step, status, updated_at, details
FROM bootstrap_status
```

Each setup notebook writes a row to this table when it completes. The Setup
page reads the table and maps each row to a visual step card.

**Exception — `ka_source_sync` (Step 8):** PDF indexing is asynchronous.
This step is checked via the live KA API on every Refresh until COMPLETED is
cached in `bootstrap_status`, after which the live call is skipped.

---

## Status Values

| Status | Meaning | Who writes it |
|--------|---------|---------------|
| `NOT_STARTED` | No row in `bootstrap_status` yet | — |
| `IN_PROGRESS` | Written explicitly when a long async task begins | `04_configure_knowledge_source.py` |
| `COMPLETED` | Notebook finished successfully | Each setup notebook |
| `FAILED` | Notebook errored and wrote this status | Not currently used — job failure is visible in Workflows UI |

---

## Full Task Lifecycle

```
deploy.sh completes
│
│  setup_schema.py has created schema, tables, volume, and UC grants.
│  bootstrap_status is empty → all 8 steps show NOT_STARTED on Setup page.
│
▼
User opens Setup page → clicks "Run Job 1 (Data Setup)"
│
│  ┌─────────────────────────────────────────────────────────────────┐
│  │  Job 1 — Data Setup                                             │
│  │                                                                 │
│  │  Task 1: create_catalog                                         │
│  │    Ensures Unity Catalog exists.                                │
│  │    Writes: create_catalog = COMPLETED                           │
│  │    → Step 1 ✅  Home page not yet populated                     │
│  │                                                                 │
│  │  Task 2: setup_care_gap_rules    ─── (parallel) ───             │
│  │    Seeds 20 care gap rules.                                     │
│  │    Writes: setup_care_gap_rules = COMPLETED                     │
│  │    → Step 2 ✅                                                  │
│  │                                                                 │
│  │  Task 3: ingest_patient_data     ─── (parallel) ───             │
│  │    Loads 25 SOAP patient records.                               │
│  │    Writes: ingest_patient_data = COMPLETED                      │
│  │    → Step 3 ✅  Home page now shows patients                    │
│  │                                                                 │
│  │  Task 4: create_vs_index  (depends on Task 2)                   │
│  │    Builds embedding_text, creates / syncs VS index.             │
│  │    Writes: care_gap_vs_index = COMPLETED                        │
│  │    → Step 4 ✅  Care Gap Advisor uses semantic retrieval        │
│  │                                                                 │
│  │  Task 5: configure_genie_space  (depends on Tasks 3 + 4)        │
│  │    Registers 4 tables to Genie Space.                           │
│  │    Writes: genie_configured = COMPLETED                         │
│  │    → Step 5 ✅  Genie chat panel becomes active                 │
│  └─────────────────────────────────────────────────────────────────┘
│
▼
User clicks "Run Job 2 (KA Setup)"
│
│  ┌─────────────────────────────────────────────────────────────────┐
│  │  Job 2 — KA Setup                                               │
│  │                                                                 │
│  │  Task 1: load_icd10_pdfs                                        │
│  │    Downloads ICD-10 PDFs from GitHub → UC Volume.               │
│  │    Writes: load_icd10_pdfs = COMPLETED                          │
│  │    → Step 6 ✅                                                  │
│  │                                                                 │
│  │  Task 2: configure_knowledge_source  (depends on Task 1)        │
│  │    Attaches UC Volume to KA, triggers async PDF indexing.       │
│  │    Deletes old ka_source_sync cache (forces fresh poll).        │
│  │    Writes: ka_source_configured = COMPLETED                     │
│  │    Writes: ka_source_sync       = IN_PROGRESS                   │
│  │    → Step 7 ✅                                                  │
│  │    → Step 8 ⏳ In Progress                                       │
│  └─────────────────────────────────────────────────────────────────┘
│
│  KA indexes PDFs asynchronously (30–60 min — no job running)
│
│  Each Refresh polls live KA API for source state:
│    UPDATING / PENDING  → Step 8 stays ⏳ In Progress
│    UPDATED             → Step 8 turns ✅ Completed
│                           Caches ka_source_sync = COMPLETED
│                           Future Refreshes skip the live API call
│
▼
All 8 steps ✅ → progress bar 100% → app fully operational
```

---

## The `ka_source_sync` Special Case (Step 8)

This is the only step that isn't written to `bootstrap_status` by a notebook
on completion — because PDF indexing happens asynchronously after the notebook
finishes.

```
bootstrap_status: ka_source_sync = IN_PROGRESS
        ↓
Refresh → GET /api/2.1/{ka_name}/knowledge-sources
        ↓
Source state = UPDATED?
   Yes → render COMPLETED
          write ka_source_sync = COMPLETED to bootstrap_status
          all future Refreshes use the cached row (no live API call)
   No  → render IN_PROGRESS — check again later

Path match fallback:
   If exact volume path not found in API response,
   check if ANY source has state = UPDATED (safe — only one source exists)
```

---

## Behaviour on Job Re-run

All notebooks are **idempotent** — safe to re-run at any time.

| Step | What happens on re-run |
|------|------------------------|
| `create_catalog` | Catalog exists → skips, overwrites `bootstrap_status` row |
| `setup_care_gap_rules` | Rules present → skips INSERT, overwrites row |
| `ingest_patient_data` | ≥ 25 records → skips ingestion, overwrites row |
| `create_vs_index` | Index exists + embeddings present → sync only (no rebuild) |
| `configure_genie_space` | Tables already registered → re-PATCH (idempotent), overwrites row |
| `load_icd10_pdfs` | `load_icd10_pdfs = COMPLETED` in db → exits immediately (skipped) |
| `configure_knowledge_source` | Deletes `ka_source_sync` cache → re-attaches volume → resets Step 8 to IN_PROGRESS |

---

## What Unlocks After Each Step

| Step completes | App capability unlocked |
|----------------|------------------------|
| Step 3 — patients ingested | Home page patient accordion populated |
| Step 4 — VS index ready | Care Gap Advisor uses semantic rule retrieval |
| Step 5 — Genie configured | Floating chat panel active |
| Step 7 — KA volume attached | ICD-10 Analyzer usable (PDFs still indexing) |
| Step 8 — PDF sync complete | ICD-10 Analyzer returns PDF-grounded citations |

> **Care Gap Advisor** requires Job 1 only (Steps 1–5).
> **ICD-10 Analyzer** requires both Job 1 and Job 2 (Steps 1–8).
> **Genie Chat** requires Job 1 only (Steps 1–5).

---

## Bootstrap Status Table Schema

```sql
CREATE TABLE bootstrap_status (
    step        STRING NOT NULL,   -- step_id matching BOOTSTRAP_STEPS in config.py
    status      STRING,            -- NOT_STARTED | IN_PROGRESS | COMPLETED | FAILED
    updated_at  TIMESTAMP,
    details     STRING             -- human-readable detail or JSON payload
) USING DELTA
```

Managed by: `setup_schema.py` (creates table) · setup notebooks (write rows)
Read by: `tab_setup.py` → `_load_bootstrap_statuses()`

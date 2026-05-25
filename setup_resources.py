#!/usr/bin/env python3
"""
Pre-deploy resource setup using only the Databricks CLI + Python stdlib.
No Python packages required — just python3 and the databricks CLI.

KA endpoint logic:
  - Searches for a KA whose display_name matches ka_display_name via
    GET /api/2.1/knowledge-assistants.
  - If one or more match, uses the most recently created one.
  - If none match, creates a new KA with the display_name as-is.
  - Volume / knowledge source configuration is NOT done here — that is Job 2.

Outputs shell variable assignments to stdout (consume via eval in deploy.sh).
Progress messages go to stderr so they don't pollute the eval output.

Usage (from deploy.sh):
    eval "$(python3 setup_resources.py --profile "$PROFILE" \
                --catalog "$CATALOG" --schema "$SCHEMA" \
                [--warehouse-id "$WAREHOUSE_ID"] [--ka-display-name "$KA_DISPLAY_NAME"])"
"""

import argparse
import json
import subprocess
import sys
import time

PROFILE = "DEFAULT"


def _info(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr)


def _cli(*args, parse_json=True):
    """Run a databricks CLI command. Returns parsed JSON or raw stdout string."""
    cmd = ["databricks"] + list(args) + [f"--profile={PROFILE}", "--output=json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    if not parse_json:
        return result.stdout
    return json.loads(result.stdout) if result.stdout.strip() else {}


def _api_get(path: str) -> dict:
    """GET /api/... via databricks api get."""
    cmd = ["databricks", "api", "get", path, f"--profile={PROFILE}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


def _api_post(path: str, body: dict) -> dict:
    """POST /api/... via databricks api post, body passed via stdin."""
    cmd = ["databricks", "api", "post", path, f"--profile={PROFILE}"]
    result = subprocess.run(cmd, input=json.dumps(body), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------
def resolve_warehouse(warehouse_id: str) -> str:
    if warehouse_id and warehouse_id not in ("<your-warehouse-id>", ""):
        try:
            data = _api_get(f"/api/2.0/sql/warehouses/{warehouse_id}")
            _info(f"✔ Warehouse validated: {data.get('name', '')} ({warehouse_id})")
            return warehouse_id
        except Exception:
            _info(f"✗ Warehouse '{warehouse_id}' not found — creating a new one")

    _info("Creating serverless SQL warehouse...")
    data = _api_post("/api/2.0/sql/warehouses", {
        "name":                      "icd10-gap-demo-warehouse",
        "cluster_size":              "Small",
        "warehouse_type":            "PRO",
        "enable_serverless_compute": True,
        "auto_stop_mins":            30,
    })
    wh_id = data.get("id", "")
    _info(f"✔ Warehouse created: {wh_id}")
    return wh_id


# ---------------------------------------------------------------------------
# Knowledge Assistant endpoint
# ---------------------------------------------------------------------------
def _create_ka(display_name: str) -> str:
    """Create a new KA with the given display name. Returns the serving endpoint name."""
    _info(f"Creating Knowledge Assistant: '{display_name}'")

    ka = _api_post("/api/2.1/knowledge-assistants", {
        "display_name": display_name,
        "description": (
            "Answers ICD-10 coding questions based on uploaded ICD-10 reference PDFs. "
            "Returns relevant codes with citations from source documents."
        ),
        "instructions": (
            "Return relevant ICD-10 codes with citations from the reference documents. "
            "For each code include: the code itself, the full description, and the specific "
            "excerpt from the source document that supports it. "
            "Rank results by relevance to the clinical text provided. "
            "If a code cannot be confidently matched to the uploaded documents, "
            "state that explicitly rather than guessing."
        ),
    })

    ka_name  = ka.get("name", "")
    endpoint = ka.get("endpoint_name", "")
    _info(f"KA created: {ka_name}")
    _info(f"Endpoint:   {endpoint}")

    if endpoint:
        _info(f"Waiting for endpoint '{endpoint}' to become ready...")
        for i in range(12):
            try:
                ep = _cli("serving-endpoints", "get", endpoint)
                state = (ep.get("state") or {}).get("ready", "")
                if state == "READY":
                    _info(f"✔ Endpoint ready after {i * 10}s")
                    break
                _info(f"[{i * 10}s] State: {state} — waiting...")
            except Exception:
                _info(f"[{i * 10}s] Endpoint not yet visible...")
            time.sleep(10)

    return endpoint


def resolve_ka_endpoint(ka_display_name: str) -> tuple[str, str]:
    """Returns (endpoint_name, ka_name) for the KA matching ka_display_name."""
    _info(f"Looking up KA by display name: '{ka_display_name}'")

    try:
        data = _api_get("/api/2.1/knowledge-assistants")
    except Exception as e:
        print(f"\nERROR: Failed to list Knowledge Assistants: {e}", file=sys.stderr)
        sys.exit(1)

    kas     = data.get("knowledge_assistants", [])
    matches = [ka for ka in kas if ka.get("display_name") == ka_display_name]

    if matches:
        matches.sort(key=lambda x: x.get("create_time", ""), reverse=True)
        chosen   = matches[0]
        endpoint = chosen.get("endpoint_name", "")
        ka_name  = chosen.get("name", "")
        if len(matches) > 1:
            _info(f"Found {len(matches)} KAs named '{ka_display_name}' — using latest "
                  f"(created {chosen.get('create_time', 'unknown')})")
        else:
            _info(f"✔ Found KA '{ka_display_name}' (created {chosen.get('create_time', 'unknown')})")
        _info(f"✔ Endpoint: {endpoint}")
        _info(f"✔ KA name:  {ka_name}")
        return endpoint, ka_name

    _info(f"No KA named '{ka_display_name}' found — creating new one")
    endpoint = _create_ka(ka_display_name)
    # Re-fetch to get the name field after creation
    data2   = _api_get("/api/2.1/knowledge-assistants")
    created = next(
        (k for k in data2.get("knowledge_assistants", [])
         if k.get("endpoint_name") == endpoint),
        {},
    )
    return endpoint, created.get("name", "")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    global PROFILE

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile",         default="DEFAULT", help="Databricks CLI profile")
    parser.add_argument("--catalog",         required=True,     help="Unity Catalog name")
    parser.add_argument("--schema",          required=True,     help="Schema name")
    parser.add_argument("--warehouse-id",    default="",        help="SQL warehouse ID (created if empty)")
    parser.add_argument("--ka-display-name", default="",        help="KA display name (created if not found)")
    args = parser.parse_args()

    PROFILE = args.profile

    print("Resolving infrastructure resources...", file=sys.stderr)

    print("\n[1/2] SQL Warehouse", file=sys.stderr)
    warehouse_id = resolve_warehouse(args.warehouse_id)

    print("\n[2/2] Knowledge Assistant Endpoint", file=sys.stderr)
    ka_endpoint, ka_name = resolve_ka_endpoint(args.ka_display_name)

    print("\n✔ All resources resolved\n", file=sys.stderr)

    # Output shell variable assignments — consumed by deploy.sh via eval
    print(f'WAREHOUSE_ID="{warehouse_id}"')
    print(f'KA_ENDPOINT_NAME="{ka_endpoint}"')
    print(f'KA_NAME="{ka_name}"')


if __name__ == "__main__":
    main()

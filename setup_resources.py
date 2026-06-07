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

VS endpoint logic:
  - Checks whether the VS endpoint named vs_endpoint_name exists via
    GET /api/2.0/vector-search/endpoints/{name}.
  - If it exists and is ONLINE, uses it as-is.
  - If it exists but is not yet ONLINE, waits up to 5 minutes.
  - If it does not exist, creates it and waits for ONLINE state.
  - VS index creation is NOT done here — that is Job 1 Task 4.

Outputs shell variable assignments to stdout (consume via eval in deploy.sh).
Progress messages go to stderr so they don't pollute the eval output.

Usage (from deploy.sh):
    eval "$(python3 setup_resources.py --profile "$PROFILE" \
                --catalog "$CATALOG" --schema "$SCHEMA" \
                [--warehouse-id "$WAREHOUSE_ID"] \
                [--ka-display-name "$KA_DISPLAY_NAME"] \
                [--vs-endpoint-name "$VS_ENDPOINT_NAME"])"
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


def _api_patch(path: str, body: dict) -> dict:
    """PATCH /api/... via databricks api patch, body passed via --json flag."""
    cmd = ["databricks", "api", "patch", path,
           f"--profile={PROFILE}", "--json", json.dumps(body)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


def _api_post(path: str, body: dict) -> dict:
    """POST /api/... via databricks api post, body passed via --json flag."""
    cmd = ["databricks", "api", "post", path,
           f"--profile={PROFILE}", "--json", json.dumps(body)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}



# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------
def resolve_warehouse(warehouse_name: str) -> str:
    if not warehouse_name:
        print("ERROR: warehouse_name is not set in databricks.yml. Cannot deploy.", file=sys.stderr)
        sys.exit(1)

    _info(f"Looking up warehouse by name: '{warehouse_name}'")
    warehouses = _api_get("/api/2.0/sql/warehouses").get("warehouses", [])
    match = next((w for w in warehouses if w.get("name") == warehouse_name), None)

    if match:
        _info(f"✔ Warehouse found: {match['name']} ({match['id']})")
        return match["id"]

    _info(f"Warehouse '{warehouse_name}' not found — creating")
    data = _api_post("/api/2.0/sql/warehouses", {
        "name":                      warehouse_name,
        "cluster_size":              "Small",
        "warehouse_type":            "PRO",
        "enable_serverless_compute": True,
        "auto_stop_mins":            30,
        "max_num_clusters":          1,
    })
    wh_id = data.get("id", "")
    _info(f"✔ Warehouse created: {wh_id}")
    return wh_id
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
            "state that explicitly rather than guessing. "
            "Always return the result as JSON array with a concrete example of the exact "
            "format expected (code, type, description, confidence)."
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
# Vector Search endpoint
# ---------------------------------------------------------------------------
def _wait_vs_online(endpoint_name: str, max_wait_s: int = 300) -> None:
    """Poll until the VS endpoint reaches ONLINE state."""
    _info(f"Waiting for VS endpoint '{endpoint_name}' to become ONLINE...")
    for i in range(max_wait_s // 10):
        time.sleep(10)
        try:
            data  = _api_get(f"/api/2.0/vector-search/endpoints/{endpoint_name}")
            state = data.get("endpoint_status", {}).get("state", "")
            if state == "ONLINE":
                _info(f"✔ VS endpoint ONLINE after {(i + 1) * 10}s")
                return
            _info(f"[{(i + 1) * 10}s] State: {state} — waiting...")
        except Exception:
            _info(f"[{(i + 1) * 10}s] Endpoint not yet visible — waiting...")
    _info("WARNING: VS endpoint did not reach ONLINE within timeout — continuing anyway")


def resolve_vs_endpoint(endpoint_name: str, catalog: str, schema: str) -> tuple[str, str]:
    """
    Returns (endpoint_name, index_name).
    Checks whether the named VS endpoint exists; creates it if not.
    index_name is always derived as <catalog>.<schema>.care_gap_rules_vs_index.
    """
    index_name = f"{catalog}.{schema}.care_gap_rules_vs_index"
    _info(f"Looking up VS endpoint: '{endpoint_name}'")

    try:
        data  = _api_get(f"/api/2.0/vector-search/endpoints/{endpoint_name}")
        state = data.get("endpoint_status", {}).get("state", "")
        _info(f"✔ VS endpoint found: {endpoint_name} (state: {state})")
        if state != "ONLINE":
            _wait_vs_online(endpoint_name)
        else:
            _info(f"✔ Already ONLINE")
        return endpoint_name, index_name

    except Exception as e:
        err = str(e)
        if not any(x in err for x in ("NOT_FOUND", "404", "does not exist", "not found", "RESOURCE_DOES_NOT_EXIST")):
            raise

    _info(f"VS endpoint '{endpoint_name}' not found — creating new one")
    _api_post("/api/2.0/vector-search/endpoints", {
        "name":          endpoint_name,
        "endpoint_type": "STANDARD",
    })
    _info(f"✔ VS endpoint created: {endpoint_name}")
    _wait_vs_online(endpoint_name)
    return endpoint_name, index_name


# ---------------------------------------------------------------------------
# Genie Space
# ---------------------------------------------------------------------------
def resolve_genie_space(space_name: str, warehouse_id: str) -> str:
    if not space_name:
        print("ERROR: genie_space_name not set in databricks.yml. Cannot deploy.", file=sys.stderr)
        sys.exit(1)

    # Check if a space with this name already exists
    _info(f"Looking up Genie Space by name: '{space_name}'")
    try:
        result = subprocess.run(
            ["databricks", "genie", "list-spaces",
             f"--profile={PROFILE}", "--output=json"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            import json as _json
            data   = _json.loads(result.stdout)
            spaces = data if isinstance(data, list) else data.get("spaces", [])
            match  = next((s for s in spaces if s.get("title") == space_name), None)
            if match:
                space_id = match.get("space_id") or match.get("id", "")
                _info(f"✔ Genie Space found: {space_id}")
                # Always update warehouse to current deployment's warehouse
                # (space may have been created with a previous/deleted warehouse)
                try:
                    _api_patch(f"/api/2.0/genie/spaces/{space_id}",
                               {"warehouse_id": warehouse_id})
                    _info(f"  Warehouse updated to: {warehouse_id}")
                except Exception as e:
                    _info(f"  Warning: could not update warehouse: {e}")
                return space_id
    except Exception as e:
        _info(f"  Could not list Genie Spaces: {e}")

    # Create with title and description in a single call
    _info(f"Creating Genie Space: '{space_name}'")
    data = _api_post("/api/2.0/genie/spaces", {
        "title":           space_name,
        "description":     (
            "AI analytics assistant for patient clinical data, ICD-10 code analysis, "
            "care gap findings, and evidence-based care gap rules."
        ),
        "warehouse_id":    warehouse_id,
        "serialized_space": json.dumps({"version": 1}),
    })
    space_id = data.get("space_id") or data.get("id", "")
    _info(f"✔ Genie Space created: {space_id}")
    _info("  Tables will be registered by Job 1 Task 5 (configure_genie_space)")
    return space_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    global PROFILE

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile",           default="DEFAULT",            help="Databricks CLI profile")
    parser.add_argument("--catalog",           required=True,                help="Unity Catalog name")
    parser.add_argument("--schema",            required=True,                help="Schema name")
    parser.add_argument("--warehouse-name",    default="",                   help="SQL warehouse name (created if not found; error if empty)")
    parser.add_argument("--ka-display-name",   default="",                   help="KA display name (created if not found)")
    parser.add_argument("--vs-endpoint-name",  default="rag_pdf_vs_endpoint", help="VS endpoint name (created if not found)")
    parser.add_argument("--genie-space-name",  default="",                   help="Genie Space display name (created if not found)")
    args = parser.parse_args()

    PROFILE = args.profile

    print("Resolving infrastructure resources...", file=sys.stderr)

    print("\n[1/4] SQL Warehouse", file=sys.stderr)
    warehouse_id = resolve_warehouse(args.warehouse_name)

    print("\n[2/4] Knowledge Assistant Endpoint", file=sys.stderr)
    ka_endpoint, ka_name = resolve_ka_endpoint(args.ka_display_name)

    print("\n[3/4] Vector Search Endpoint", file=sys.stderr)
    vs_endpoint, vs_index = resolve_vs_endpoint(args.vs_endpoint_name, args.catalog, args.schema)

    print("\n[4/4] Genie Space", file=sys.stderr)
    genie_space_id = resolve_genie_space(args.genie_space_name, warehouse_id)

    print("\n✔ All resources resolved\n", file=sys.stderr)

    # Output shell variable assignments — consumed by deploy.sh via eval
    print(f'WAREHOUSE_ID="{warehouse_id}"')
    print(f'KA_ENDPOINT_NAME="{ka_endpoint}"')
    print(f'KA_NAME="{ka_name}"')
    print(f'VS_ENDPOINT_NAME="{vs_endpoint}"')
    print(f'GENIE_SPACE_ID="{genie_space_id}"')


if __name__ == "__main__":
    main()

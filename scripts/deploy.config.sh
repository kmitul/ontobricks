#!/usr/bin/env bash
# ── OntoBricks deployment configuration ─────────────────────────────
#
# SINGLE SOURCE OF TRUTH for everything `make deploy` needs.
# Sourced by `scripts/deploy.sh`. Every variable is env-overridable
# (`FOO=bar make deploy`) so CI can drive deployments without
# committing per-environment changes.
#
# ┌─────────────────────────────────────────────────────────────────┐
# │  TO DEPLOY A NEW INSTANCE: change DEFAULT_APP_NAME (and        │
# │  DEFAULT_REGISTRY_SCHEMA / DEFAULT_LAKEBASE_REGISTRY_SCHEMA    │
# │  only if you need custom schema names).                         │
# │  Everything else derives automatically:                         │
# │    MCP app name       → "mcp-<DEFAULT_APP_NAME>"               │
# │    UC registry schema → DEFAULT_REGISTRY_SCHEMA (slug default) │
# │    Lakebase PG schema → DEFAULT_LAKEBASE_REGISTRY_SCHEMA       │
# │    Lakebase datname   → same as DEFAULT_LAKEBASE_REGISTRY_SCHEMA│
# │    Workspace folder   → .bundle/<DEFAULT_APP_NAME>/<target>    │
# │                                                                  │
# │  Per-workspace constants (section 0b) stay shared across all    │
# │  instances — set them once for your workspace.                  │
# └─────────────────────────────────────────────────────────────────┘
#
# Workflow:
#
#   1. Set DEFAULT_APP_NAME to a free name (`databricks apps list`).
#   2. Set DEFAULT_REGISTRY_SCHEMA if the schema name must differ
#      from the app-name slug (pre-existing or shared schema).
#   3. Adjust section 0b if deploying to a different workspace.
#   4. Run `make deploy` (or `scripts/deploy.sh` directly).
#   5. For a one-off override: `WAREHOUSE_ID=abc make deploy`.
#
# Sections:
#
#   0a. Instance identity  — THE ONE THING to change per deployment.
#   0b. Workspace constants — per-workspace, shared across instances.
#   0c. Derived defaults   — auto-computed from 0a; do not edit.
#   1.  Apps               — exports for the app names
#   2.  DAB target         — which `databricks.yml` target to deploy
#   3.  DAB vars           — values for `databricks.yml > variables:`
#   4.  Runtime fallbacks  — values rendered into `app.yaml` at deploy time

# ── 0a. Instance identity ────────────────────────────────────────────
# THE ONLY LINE YOU NEED TO CHANGE to create a new deployment.
# App names are workspace-global — pick one not already in `databricks apps list`.
DEFAULT_APP_NAME="ontobricks-060"

# Postgres schema inside Lakebase where OntoBricks registry tables live.
# Independent of the UC schema above — both default to the same slug but
# can diverge (e.g. a shared Lakebase instance with a fixed schema name).
DEFAULT_LAKEBASE_REGISTRY_SCHEMA="${DEFAULT_APP_NAME//-/_}"
# Example — shared/fixed Lakebase schema: DEFAULT_LAKEBASE_REGISTRY_SCHEMA="ontobricks_registry"

# ── 0b. Workspace constants ──────────────────────────────────────────
# Set once for your workspace. Shared across all instances deployed here.

# SQL Warehouse
DEFAULT_WAREHOUSE_ID="d2096aa075ad44a3"

# Unity Catalog — catalog that holds per-instance schemas
DEFAULT_REGISTRY_CATALOG="benoit_cayla"
# UC schema for the Volume registry (Unity Catalog — triples + domain files).
# Defaults to the app-name slug (hyphens → underscores).
# Set explicitly only for a pre-existing or shared UC schema.
DEFAULT_REGISTRY_SCHEMA="ontobricks_demo"
DEFAULT_REGISTRY_VOLUME="registry"

# Lakebase Autoscaling project + branch (shared across instances —
# each instance gets its own schema inside the same database).
DEFAULT_LAKEBASE_PROJECT="ontobricks-demo2"
DEFAULT_LAKEBASE_BRANCH="production"
# db-… resource id from `databricks postgres list-databases
#   "projects/<project>/branches/<branch>" -o json`
DEFAULT_LAKEBASE_DATABASE_RESOURCE_SEGMENT="db-v6vc-8ibz5oeigo"

# ── 0c. Derived defaults (auto-computed — do NOT edit) ────────────────
# MCP companion app name: "mcp-<app-name>"
DEFAULT_MCP_APP_NAME="mcp-${DEFAULT_APP_NAME}"

# Lakebase datname mirrors the Lakebase schema name
DEFAULT_LAKEBASE_REGISTRY_DATABASE="${DEFAULT_LAKEBASE_REGISTRY_SCHEMA}"

# DAB resource keys (static — identifiers in databricks.yml, not app names)
DEFAULT_APP_RESOURCE_KEY="ontobricks_dev_app"
DEFAULT_MCP_APP_RESOURCE_KEY="mcp_ontobricks_app"

# ── 0d. app.yaml runtime fallback literals ───────────────────────────
# Only literal values that have no section-3 counterpart live here.
DEFAULT_APP_TRIPLESTORE_TABLE_NAME="default_triplestore"
DEFAULT_APP_MLFLOW_TRACKING_URI="databricks"

# ── DAB target ───────────────────────────────────────────────────────
DEFAULT_DAB_TARGET="dev-lakebase"

# ── 1. Apps ─────────────────────────────────────────────────────────
# The FastAPI UI app and its MCP companion server.
export APP_NAME="${APP_NAME:-$DEFAULT_APP_NAME}"
export MCP_APP_NAME="${MCP_APP_NAME:-$DEFAULT_MCP_APP_NAME}"

# DAB resource keys (rarely change — identifiers in databricks.yml).
export APP_RESOURCE_KEY="${APP_RESOURCE_KEY:-$DEFAULT_APP_RESOURCE_KEY}"
export MCP_APP_RESOURCE_KEY="${MCP_APP_RESOURCE_KEY:-$DEFAULT_MCP_APP_RESOURCE_KEY}"

# ── 2. DAB target ───────────────────────────────────────────────────
# `dev`           : Volume-only registry backend.
# `dev-lakebase`  : Volume + Lakebase Autoscaling Postgres binding
#                   (default — required for the Postgres registry).
export DAB_TARGET="${DAB_TARGET:-$DEFAULT_DAB_TARGET}"

# ── 3. DAB variable overrides (databricks.yml > variables:) ─────────
# Passed to `databricks bundle deploy` as `--var=key=value`.
export WAREHOUSE_ID="${WAREHOUSE_ID:-$DEFAULT_WAREHOUSE_ID}"

# Unity Catalog Volume securable.
export REGISTRY_CATALOG="${REGISTRY_CATALOG:-$DEFAULT_REGISTRY_CATALOG}"
export REGISTRY_SCHEMA="${REGISTRY_SCHEMA:-$DEFAULT_REGISTRY_SCHEMA}"
export REGISTRY_VOLUME="${REGISTRY_VOLUME:-$DEFAULT_REGISTRY_VOLUME}"

# Lakebase Autoscaling project / branch / database.
# `LAKEBASE_DATABASE_RESOURCE_SEGMENT` is the `db-…` id from
# `databricks postgres list-databases` — NOT the datname.
# See `databricks.yml` lines 110-156 for the full caveat.
export LAKEBASE_PROJECT="${LAKEBASE_PROJECT:-$DEFAULT_LAKEBASE_PROJECT}"
export LAKEBASE_BRANCH="${LAKEBASE_BRANCH:-$DEFAULT_LAKEBASE_BRANCH}"
export LAKEBASE_DATABASE_RESOURCE_SEGMENT="${LAKEBASE_DATABASE_RESOURCE_SEGMENT:-$DEFAULT_LAKEBASE_DATABASE_RESOURCE_SEGMENT}"

# Registry Postgres datname (psql grants + app.yaml runtime). NOT the db-… segment.
export LAKEBASE_REGISTRY_DATABASE="${LAKEBASE_REGISTRY_DATABASE:-$DEFAULT_LAKEBASE_REGISTRY_DATABASE}"

# Lakebase Postgres schema OntoBricks writes the registry into.
export LAKEBASE_REGISTRY_SCHEMA="${LAKEBASE_REGISTRY_SCHEMA:-$DEFAULT_LAKEBASE_REGISTRY_SCHEMA}"

# ── 4. app.yaml runtime fallbacks ───────────────────────────────────
# Templated into `app.yaml` (and `src/mcp-server/app.yaml`) at deploy time.
# Only consulted when the matching DAB resource is unbound.

# MCP companion: URL of the main OntoBricks app.
# Format: https://<app-name>-<workspace-id>.<region>.databricksapps.com
# Retrieve after first deploy with: databricks apps get <APP_NAME> | jq -r .url
# Leave empty ("") to use localhost:8000 (local dev only).
export APP_ONTOBRICKS_URL="${APP_ONTOBRICKS_URL:-}"

# DBSQL warehouse fallback for MCP / session-less API calls.
export APP_SQL_WAREHOUSE_FALLBACK="${APP_SQL_WAREHOUSE_FALLBACK:-$WAREHOUSE_ID}"

# Default fully-qualified Delta triplestore table (catalog.schema.table).
export APP_TRIPLESTORE_TABLE="${APP_TRIPLESTORE_TABLE:-${REGISTRY_CATALOG}.${REGISTRY_SCHEMA}.${DEFAULT_APP_TRIPLESTORE_TABLE_NAME}}"

# Registry runtime fallbacks — used when the Volume resource is not bound
# (typically local dev / MCP).
export APP_REGISTRY_CATALOG="${APP_REGISTRY_CATALOG:-$REGISTRY_CATALOG}"
export APP_REGISTRY_SCHEMA="${APP_REGISTRY_SCHEMA:-$REGISTRY_SCHEMA}"
export APP_REGISTRY_VOLUME="${APP_REGISTRY_VOLUME:-$REGISTRY_VOLUME}"

# Lakebase Postgres schema (must match the schema GRANTed by
# `bootstrap-lakebase-perms.sh`).
export APP_LAKEBASE_SCHEMA="${APP_LAKEBASE_SCHEMA:-$LAKEBASE_REGISTRY_SCHEMA}"

# Lakebase Postgres database name (the actual datname, not the db-… segment).
export APP_LAKEBASE_DATABASE="${APP_LAKEBASE_DATABASE:-$LAKEBASE_REGISTRY_DATABASE}"

# Lakebase project (autoscaling instance name) — informational in deployed app.
export APP_LAKEBASE_PROJECT="${APP_LAKEBASE_PROJECT:-$LAKEBASE_PROJECT}"

# Lakebase branch (used by LakebaseAuth host-resolution fallback when PGHOST
# is not injected — e.g. local dev without binding).
export APP_LAKEBASE_BRANCH="${APP_LAKEBASE_BRANCH:-$LAKEBASE_BRANCH}"

# Lakebase managed-synced: UC catalog for the Lakeflow synced-table registration.
# Leave empty to auto-resolve from the registry Volume config (recommended).
# NOTE: intentionally NOT using ${APP_SYNC_UC_CATALOG:-} here so that a
# stale shell export from a previous deploy never bleeds through.
export APP_SYNC_UC_CATALOG=""

# MLflow tracking URI (`databricks` = workspace tracking server).
export APP_MLFLOW_TRACKING_URI="${APP_MLFLOW_TRACKING_URI:-$DEFAULT_APP_MLFLOW_TRACKING_URI}"

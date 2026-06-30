-- ============================================================================
-- OntoBricks Lakebase registry upgrade: add graph_analytics cache (0.6.x)
-- ----------------------------------------------------------------------------
-- The Knowledge Graph Analytics page now runs asynchronously and persists the
-- LAST computed metrics result per (domain_id, version) in a new
-- ``graph_analytics`` table. The Analytics page and the Domain Validation
-- "Graph Structure" cockpit card render from this row instead of recomputing
-- NetworkX metrics on every request.
--
-- This is a cache (one row per tuple, replaced on every successful recompute
-- via UPSERT), not an append-only trace like ``build_runs``.
--
-- ADDITIVE: creates one table + nothing destructive. The app self-heals new
-- installs (``_ensure_graph_analytics_table``) and the canonical
-- ``src/back/objects/registry/store/lakebase/schema.sql`` declares the table,
-- so this script is only needed to add the table to registries provisioned
-- before the change without re-running the Settings "Initialize" action.
-- Run it as the schema owner so the app SP inherits the right grants
-- (or re-run ``make bootstrap-lakebase`` afterwards).
--
-- Idempotent: safe to run multiple times (IF NOT EXISTS guards throughout).
-- ----------------------------------------------------------------------------
-- Usage (psql):
--   # default schema (ontobricks_registry):
--   psql "$PGURL" -f scripts/upgrade_lakebase_0.6_add_graph_analytics.sql
--
--   # custom registry schema (matches LAKEBASE_SCHEMA / REGISTRY_SCHEMA):
--   psql "$PGURL" -v reg_schema=my_registry_schema \
--        -f scripts/upgrade_lakebase_0.6_add_graph_analytics.sql
-- ============================================================================

\set ON_ERROR_STOP on

-- Resolve the target schema (override with  -v reg_schema=...  ; default below).
\if :{?reg_schema}
\else
  \set reg_schema ontobricks_registry
\endif

SET search_path TO :"reg_schema";

\echo 'Adding graph_analytics cache on OntoBricks registry schema:' :reg_schema

BEGIN;

CREATE TABLE IF NOT EXISTS graph_analytics (
    domain_id    uuid NOT NULL
                 REFERENCES domains(id) ON DELETE CASCADE,
    version      text NOT NULL,
    status       text NOT NULL DEFAULT 'completed',  -- completed|failed
    graph_name   text NOT NULL DEFAULT '',
    class_filter jsonb NOT NULL DEFAULT '[]'::jsonb,  -- entity types used ([]=all)
    stats        jsonb NOT NULL DEFAULT '{}'::jsonb,
    top_pagerank jsonb NOT NULL DEFAULT '[]'::jsonb,
    result       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- full compute payload
    error        text NOT NULL DEFAULT '',
    task_id      text NOT NULL DEFAULT '',
    duration_ms  bigint NOT NULL DEFAULT 0,
    computed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (domain_id, version)
);

-- Append-only run history (lightweight metadata per analysis launched).
CREATE TABLE IF NOT EXISTS graph_analytics_runs (
    id                  bigserial PRIMARY KEY,
    domain_id           uuid NOT NULL
                        REFERENCES domains(id) ON DELETE CASCADE,
    version             text NOT NULL,
    status              text NOT NULL DEFAULT 'completed',
    class_filter        jsonb NOT NULL DEFAULT '[]'::jsonb,
    node_count          bigint NOT NULL DEFAULT 0,
    edge_count          bigint NOT NULL DEFAULT 0,
    connected_components integer NOT NULL DEFAULT 0,
    avg_degree          double precision NOT NULL DEFAULT 0,
    density             double precision NOT NULL DEFAULT 0,
    duration_ms         bigint NOT NULL DEFAULT 0,
    task_id             text NOT NULL DEFAULT '',
    error               text NOT NULL DEFAULT '',
    computed_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_graph_analytics_runs_domain_version
    ON graph_analytics_runs(domain_id, version, computed_at DESC);

COMMIT;

-- Summary -------------------------------------------------------------------
\echo 'Done. graph_analytics columns:'
SELECT column_name
FROM information_schema.columns
WHERE table_schema = :'reg_schema'
  AND table_name = 'graph_analytics'
ORDER BY ordinal_position;

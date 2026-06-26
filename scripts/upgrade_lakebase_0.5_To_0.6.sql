-- ============================================================================
-- OntoBricks Lakebase registry upgrade: 0.5.x  ->  0.6.x
-- ----------------------------------------------------------------------------
-- Adds the collaborative *comments & tasks* (the "Discussions" feature):
--
--   * new table   domain_comments  — contextual threaded discussion anchored
--                 to a domain version (ontology class/property, mapping, graph
--                 node/edge, or the whole domain). A non-empty ``parent_id``
--                 makes the row a reply; ``resolved`` closes a thread without
--                 losing history.
--   * new table   domain_tasks     — personalised work items, usually born
--                 from a comment (``comment_id``), surfaced in the assignee's
--                 "My Tasks" worklist.
--   * indexes     idx_domain_comments_anchor, idx_domain_tasks_assignee,
--                 idx_domain_tasks_domain.
--
-- These tables carry the same CHECK constraints as the canonical
-- ``src/back/objects/registry/store/lakebase/schema.sql`` (anchor_type and
-- task status), so the registry stays fully constrained.
--
-- The app self-heals these tables lazily on first comment/task write
-- (``_ensure_collab_tables``), and ``make bootstrap-lakebase`` provisions them
-- as the schema owner. Run this script when you prefer an explicit, auditable
-- one-shot migration (e.g. a DBA applying it out-of-band). Nothing here is
-- destructive — no existing data is touched, no columns are dropped.
--
-- Idempotent: safe to run multiple times.
-- ----------------------------------------------------------------------------
-- Usage (psql):
--   # default schema (ontobricks_registry):
--   psql "$PGURL" -f scripts/upgrade_lakebase_0.5_To_0.6.sql
--
--   # custom registry schema (matches LAKEBASE_SCHEMA / REGISTRY_SCHEMA):
--   psql "$PGURL" -v reg_schema=my_registry_schema \
--        -f scripts/upgrade_lakebase_0.5_To_0.6.sql
-- ============================================================================

\set ON_ERROR_STOP on

-- Resolve the target schema (override with  -v reg_schema=...  ; default below).
\if :{?reg_schema}
\else
  \set reg_schema ontobricks_registry
\endif

SET search_path TO :"reg_schema";

\echo 'Upgrading OntoBricks registry schema:' :reg_schema

BEGIN;

-- 1. Collaborative comments --------------------------------------------------
CREATE TABLE IF NOT EXISTS domain_comments (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_id   uuid NOT NULL
                REFERENCES domains(id) ON DELETE CASCADE,
    version     text NOT NULL,
    anchor_type text NOT NULL DEFAULT 'domain'
                CHECK (anchor_type IN ('ontology_class', 'ontology_property',
                                       'mapping', 'graph_node', 'graph_edge',
                                       'domain')),
    anchor_ref  text NOT NULL DEFAULT '',
    parent_id   uuid REFERENCES domain_comments(id) ON DELETE CASCADE,
    author      text NOT NULL,
    body        text NOT NULL DEFAULT '',
    resolved    boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_domain_comments_anchor
    ON domain_comments(domain_id, version, anchor_type, anchor_ref);

-- 1b. Backfill the anchor_type CHECK on registries whose table was created by
--     the app's lazy self-heal path (which omits the constraint).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'domain_comments_anchor_type_check'
          AND conrelid = 'domain_comments'::regclass
    ) THEN
        ALTER TABLE domain_comments
            ADD CONSTRAINT domain_comments_anchor_type_check
            CHECK (anchor_type IN ('ontology_class', 'ontology_property',
                                   'mapping', 'graph_node', 'graph_edge',
                                   'domain'));
    END IF;
END$$;

-- 2. Collaborative tasks -----------------------------------------------------
CREATE TABLE IF NOT EXISTS domain_tasks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_id   uuid NOT NULL
                REFERENCES domains(id) ON DELETE CASCADE,
    version     text NOT NULL,
    assignee    text NOT NULL,
    created_by  text NOT NULL,
    title       text NOT NULL,
    description text NOT NULL DEFAULT '',
    status      text NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'in_progress', 'done', 'cancelled')),
    due_date    date,
    comment_id  uuid REFERENCES domain_comments(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_domain_tasks_assignee
    ON domain_tasks(lower(assignee), status);
CREATE INDEX IF NOT EXISTS idx_domain_tasks_domain
    ON domain_tasks(domain_id, version);

-- 2b. Backfill the status CHECK on lazily-created tables (see 1b). ------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'domain_tasks_status_check'
          AND conrelid = 'domain_tasks'::regclass
    ) THEN
        ALTER TABLE domain_tasks
            ADD CONSTRAINT domain_tasks_status_check
            CHECK (status IN ('open', 'in_progress', 'done', 'cancelled'));
    END IF;
END$$;

COMMIT;

-- Summary -------------------------------------------------------------------
\echo 'Done. Collaboration tables present:'
SELECT table_name
FROM information_schema.tables
WHERE table_schema = :'reg_schema'
  AND table_name IN ('domain_comments', 'domain_tasks')
ORDER BY table_name;

-- ============================================================================
-- Part 2 — Graph schema migration (0.5 → 0.6)
-- ----------------------------------------------------------------------------
-- Adds the ``datatype`` and ``lang`` columns to all ``*_sync`` and ``*__app``
-- graph triple-store tables that were created before those columns existed.
--
-- These tables live in the **graph schema** (default: ``ontobricks_graph``),
-- NOT in the registry schema above.
--
-- Also drops stale ``*_sync`` tables that may have been left behind by an
-- earlier run under a different role (Lakeflow or a previous session user).
-- The DROP is unconditional so you need superuser or ownership.  Run as the
-- Lakebase schema owner.
--
-- Idempotent: ``ADD COLUMN IF NOT EXISTS`` and ``DROP TABLE IF EXISTS`` are
-- safe to re-run.
--
-- Usage:
--   # default graph schema (ontobricks_graph):
--   psql "$PGURL" -f scripts/upgrade_lakebase_0.5_To_0.6.sql
--
--   # custom graph schema:
--   psql "$PGURL" -v graph_schema=my_graph_schema \
--        -f scripts/upgrade_lakebase_0.5_To_0.6.sql
-- ============================================================================

\if :{?graph_schema}
\else
  \set graph_schema ontobricks_graph
\endif

\echo ''
\echo 'Upgrading OntoBricks graph schema:' :graph_schema

SET search_path TO :"graph_schema";

BEGIN;

-- 3. Backfill datatype + lang on every *_sync table that is missing them -----
--    (tables created by the app before v0.6 only had subject/predicate/object)
DO $$
DECLARE
    tbl text;
BEGIN
    FOR tbl IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema()
          AND c.relkind = 'r'
          AND c.relname LIKE '%_sync'
    LOOP
        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS datatype TEXT', tbl);
        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS lang    TEXT', tbl);
        RAISE NOTICE 'Patched table: %', tbl;
    END LOOP;
END$$;

-- 4. Same for *__app companion tables ----------------------------------------
DO $$
DECLARE
    tbl text;
BEGIN
    FOR tbl IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema()
          AND c.relkind = 'r'
          AND c.relname LIKE '%__app'
    LOOP
        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS datatype TEXT', tbl);
        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS lang    TEXT', tbl);
        RAISE NOTICE 'Patched companion table: %', tbl;
    END LOOP;
END$$;

-- 5. Drop stale *_sync tables not owned by the current role ------------------
--    Use this block when the previous owner was a Lakeflow service principal
--    or a different session user. Requires superuser or explicit ownership.
--    Review the table list below before running!
--
--    Uncomment and adjust as needed:
-- DO $$
-- DECLARE tbl text;
-- BEGIN
--     FOR tbl IN
--         SELECT c.relname
--         FROM pg_class c
--         JOIN pg_namespace n ON n.oid = c.relnamespace
--         WHERE n.nspname = current_schema()
--           AND c.relkind = 'r'
--           AND c.relname LIKE '%_sync'
--           AND NOT pg_has_role(session_user, c.relowner, 'MEMBER')
--     LOOP
--         EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', tbl);
--         RAISE NOTICE 'Dropped stale table: %', tbl;
--     END LOOP;
-- END$$;

COMMIT;

\echo 'Done. Graph schema columns:'
SELECT
    c.relname                          AS table_name,
    array_agg(a.attname ORDER BY a.attnum) AS columns
FROM pg_class c
JOIN pg_namespace n  ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = :'graph_schema'
  AND c.relkind = 'r'
  AND (c.relname LIKE '%_sync' OR c.relname LIKE '%__app')
GROUP BY c.relname
ORDER BY c.relname;

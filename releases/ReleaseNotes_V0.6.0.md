# OntoBricks — Release Notes V0.6.0

**Release date:** 2026-06-27
**Type:** Major feature release
**Test status:** 2998 passed, 17 skipped (unit tier, `make test`).

---

## Summary

v0.6.0 is the largest feature release since v0.5.0. It ships three independent pillars
of new capability:

1. **Collaborative Comments & Tasks** — domain-scoped discussion threads, task management,
   with a shared team Discussion accessible from every surface (Ontology, Mapping, Graph
   Explorer, Validation, and the new Domain → Collaboration page).

2. **Graph Analytics** — server-side centrality metrics (PageRank, betweenness, degree,
   closeness, clustering), a Data Model Health card, and an AI interpretation agent that
   explains and recommends improvements grounded in live entity data.

3. **Mapping Designer depth** — per-attribute include/exclude, always-quoted column names,
   append-mode OWL/RDFS/SKOS/SHACL import with conflict detection, and a richer
   Ontology Designer (neighbourhood highlight, entity search, inheritance toggle, Business
   View auto-generation).

In addition the release includes a suite of UX improvements (sidebar user panel, App Logs
viewer, Registry access check, domain menu restructure) and a significant set of bug fixes.

No breaking changes. All v0.5.x features are fully intact.

---

## New Features

### 1. Collaborative Comments & Tasks

Domain versions can now carry a shared Discussion thread accessible from every surface
(Ontology Designer, Mapping Designer, Graph Explorer, Domain → Validation, and the new
Domain → Collaboration page). Comments are threaded; any comment can be turned into a
task assigned to a domain member.

- **Domain → Collaboration page** — My Tasks worklist (inline Start/Done transitions)
  + activity timeline with day grouping, anchor badges, and a live tag filter.
- **Discussion offcanvas** — rendered markdown (full `marked` support), per-thread status
  chips, and a standalone "New task" button in the panel header.
- **Per-surface anchoring** — discussion buttons on the Ontology Designer toolbar (whole
  diagram), on entity/relationship edit panels, on mapping modals, and on Graph Explorer
  node/edge detail panels.
- **Lakebase schema** — two new tables (`domain_comments`, `domain_tasks`) with the
  canonical upgrade script `scripts/upgrade_lakebase_0.5_To_0.6.sql`.

### 2. Graph Analytics

A new **Analytics** tab in the Knowledge Graph Explorer computes and renders:

- **Centrality metrics**: PageRank (with formula card), Betweenness, Degree, Closeness,
  Clustering — each with a bar chart, clickable bars (navigate to Graph Viewer), metric
  explanation popup, and a detail ranking table.
- **Entity type filter**: single-select dropdown; filters the returned node set while
  computing metrics on the full connected subgraph for accuracy.
- **Data Model Health card**: detects flat/time-series entity types via four heuristics
  (low degree, low clustering, temporal predicates, high predicate count) and surfaces
  them with reasoning badges.
- **AI Interpretation** (`agent_graph_interpreter`): tool-enabled agent (up to 8 tool
  iterations) that calls `get_entity_details` to ground its insights in live data; returns
  Key Findings, Notable Entities, and Recommendations as styled Bootstrap sub-cards.
- **Graph Structure cockpit card** on Domain → Validation: node/edge count, connected
  components, avg degree, density, and top-5 PageRank nodes.

### 3. Mapping Designer — Per-Attribute Include/Exclude

Attributes (data properties) can be individually excluded from a mapping without deleting
the entity's SQL configuration:

- Checkbox column in the Status tab; "Exclude all / Include all" toggle button.
- Excluded attributes are suppressed from: gap reporting, KPI gauges, Auto-Map page, the
  agent's attribute list, R2RML output, and the Mapping Information page.
- Exclusion state survives Unmap + re-map cycles, agent auto-map updates, and server
  restarts (persisted in the registry).

### 4. Append-mode OWL/RDFS Import with Conflict Detection

The OWL and RDFS import tabs now offer a **Replace / Append** mode selector. Append mode
uses a two-phase flow:

1. `POST /ontology/analyze-import` — read-only conflict analysis (new / duplicate /
   uri_conflict / name_conflict) displayed in a persistent step timeline panel.
2. `POST /ontology/merge-import` — per-entity resolution (skip / overwrite / rename)
   before merge.

Extended format support:
- **SKOS** — `skos:Concept` / `skos:prefLabel` / `skos:broader` vocabularies.
- **Alignment files** — SKOS alignment predicates + labeled-resource fallback.
- **SHACL shapes** — auto-detected and silently routed to Data Quality instead of the
  ontology class list.

### 5. Ontology Designer Improvements

- **Neighbourhood highlight**: clicking a node or relationship dims everything else and
  applies a blue glow ring to connected neighbours/endpoints.
- **Inheritance toggle**: a persistent toolbar button hides/shows all dashed inheritance
  arrows; state preserved in `sessionStorage`.
- **Entity search popup**: sorted dropdown + Focus button that pans/zooms the SVG to the
  selected entity and triggers the neighbourhood highlight.
- **Create Business View from right-click**: builds a 1-hop layout (centre entity + all
  direct neighbours) and saves it as a new Design View (`Auto_<EntityName>`); navigates
  directly to Business Views.

### 6. Application Logs Viewer (Settings → Admin)

A new **Logs** section under Settings → Admin streams a live tail of `ontobricks.log`
with level filtering (ALL / DEBUG / INFO / WARNING / ERROR), text search, configurable
line count (default 200, max 5 000), and 10-second auto-refresh.

### 7. Registry Access Check (Settings)

The "Registry Catalog & Volume" panel now includes a **Check Access** button that runs
two parallel probes:

- **Unity Catalog**: existence + accessible status for the UC schema and Volume via REST.
- **Lakebase**: single Postgres round-trip verifying `USAGE`/`CREATE` on schema, and
  `SELECT`/`INSERT`/`UPDATE`/`DELETE` on all 10 registry tables; failing checks include
  the exact `GRANT` SQL to fix them.

### 9. User / Role Info Panel in Sidebar

All pages using the shared sidebar nav show the connected user's email and role badges
(Admin in red, domain role in blue when it differs) pinned to the sidebar footer. The
redundant role pill in the top-right navbar is hidden.

---

## Bug Fixes

### Domain & Registry

- **last_build reset on domain switch** — `_record_build_run` now calls
  `stamp_last_build` on every successful UI/API build, writing directly to
  `domain_versions.last_build` in Lakebase so the "Last Built" indicator survives domain
  reloads.
- **Base URI persistence** — `read_version` now reads the canonical `domains.base_uri`
  column; `write_version` uses a `CASE WHEN` guard so an empty incoming value never
  overwrites a stored one; empty-string `base_uri` now triggers the `DEFAULT_BASE_URI`
  fallback consistently across the session, OWL generator, R2RML generator, and external
  API.

### Mapping & R2RML

- **Column names with spaces / special characters** — `R2RMLGenerator` now
  unconditionally double-quotes every `rr:column` and `rr:template` column reference per
  R2RML spec §7.4; `R2RMLParser` strips these quotes on read-back. The auto-mapping agent
  always backtick-quotes column names in SQL and never submits quoted names as mapping
  keys (backtick stripping in `tool_submit_entity_mapping`).
- **Auto-map overwrites attribute exclusions** — `tool_submit_entity_mapping` / `_relationship`
  preserve `excluded_attributes` from the existing mapping; `tool_get_ontology` hides
  excluded attributes from the agent entirely.

### Knowledge Graph

- **Lakeflow silently skipped on cold start** — `TripleStoreFactory._resolve_graph_engine`
  and `_resolve_graph_engine_config` now accept `force=True`; the build pipeline and the
  sync start endpoint always pass `force=True` so the `managed_synced` mode is read from
  Lakebase even immediately after a cold start.
- **Explorer blocked on PUBLISHED/IN-REVIEW domains** — `_STATUS_GATE_EDIT_PATHS` was
  narrowed from the broad `/dtwin/sync/` prefix to `/dtwin/sync/start` and
  `/dtwin/sync/load` only; read-only POSTs (`/dtwin/sync/filter` etc.) are now accessible
  regardless of lifecycle status.

### Ontology Designer

- **Relationships in opposite directions overlapping** — canonical-direction vector
  normalization (smaller entity ID → larger) ensures opposite-direction links offset to
  opposite sides; Bezier midpoint placement corrected to `t=0.5` (offset × 0.5, not × 1).
  Affects the Ontology Designer, Mapping Designer, and Business Views (OntoViz).
- **OWL export uses domain name as namespace prefix** — the default namespace is now bound
  to a clean lowercase prefix derived from the ontology name, instead of the empty string.

### UI & Deployment

- **Settings → Lakebase Connection tab dropdowns not pre-selected** — `SettingsService`
  now overlays env-var fallbacks (`LAKEBASE_PROJECT`, `LAKEBASE_BRANCH`,
  `PGDATABASE`/`LAKEBASE_DATABASE`) for empty fields before returning the config.
- **Lakebase Bulk loading tab — UC catalog not pre-selected** — `prefillLakebaseConnectionFromConfig`
  extended to also populate `lakebaseUcCatalog`; engine change handler triggers
  `loadUcCatalogsForGraphEngine` when saved mode is `managed_synced`.
- **App startup crash — `Any` not imported in `UnityCatalog.py`** — import fixed.
- **Runs page JS crash on dtwin page** — `runDetailsModal` and `domain-runs.js` added to
  `dtwin.html`; null guard added in `showRunDetailsObj`.
- **Knowledge Graph Explorer context-menu hover invisible** — hover rule corrected to
  blue-tint (`#e8f4fd` / `#1a73e8`) in `query-sigmagraph.css`.
- **deploy.sh — `lakebase_database_resource_segment` empty in DAB command** — the resolved
  `db-…` segment is now patched back into `_dab_var_overrides` before the bundle deploy.
- **Collaborative comments — MCP 502/503 retry** — `_get` and `_post` in the MCP server
  retry up to 3 times (5 s → 10 s → 20 s back-off) on 502/503 cold-start responses.

---

## Upgrade Notes

### New deploys (v0.6.0 from scratch)

No special action required beyond the standard `make bootstrap-lakebase` which creates all
required tables including `domain_comments` and `domain_tasks`.

### Upgrading from v0.5.x

Run the new idempotent migration script as schema owner to add the collaboration tables:

```bash
psql -v reg_schema=ontobricks_registry -f scripts/upgrade_lakebase_0.5_To_0.6.sql
```

The script adds `domain_comments` + `domain_tasks` (3 indexes + 2 CHECK constraints),
is fully non-destructive, and safe to re-run. The app's lazy `_ensure_collab_tables`
self-heal will also create the tables on first Discussion load if this script is not run.

No other schema changes. No data migrations required.

---

## Changes Summary

| Area | Files changed | Change type |
|------|---------------|-------------|
| Collaboration | `registry/CommentService.py`, `registry/store/lakebase/store.py`, `api/routers/internal/comments.py`, `scripts/upgrade_lakebase_0.5_To_0.6.sql` | New feature |
| Graph Analytics | `back/core/graph_analysis/GraphMetrics.py`, `api/routers/internal/dtwin.py`, `partials/dtwin/_query_analytics.html` | New feature |
| AI Graph Interpreter | `agents/agent_graph_interpreter/`, `back/objects/digitaltwin/DigitalTwin.py` | New feature |
| Mapping attr exclusion | `front/static/mapping/js/mapping-design.js`, `back/objects/mapping/Mapping.py`, `agents/tools/mapping.py` | New feature |
| OWL append import | `back/core/w3c/owl/OntologyConflictDetector.py`, `back/objects/ontology/Ontology.py`, `api/routers/internal/ontology.py` | New feature |
| SKOS/SHACL/Alignment import | `back/core/w3c/rdfs/RDFSParser.py` | New feature |
| Ontology Designer | `front/static/ontology/js/ontology-map.js`, `ontology-map.css`, `_ontology_map.html` | New feature |
| App Logs viewer | `api/routers/internal/settings.py`, `front/static/config/js/settings-logs.js` | New feature |
| Registry access check | `back/core/databricks/UnityCatalog.py`, `back/objects/domain/SettingsService.py` | New feature |
| Sidebar user panel | `front/templates/partials/layout/_sidebar_nav.html`, `css/sidebar-layout.css` | Enhancement |
| R2RML column quoting | `back/core/w3c/r2rml/R2RMLGenerator.py`, `R2RMLParser.py` | Fix |
| Base URI persistence | `registry/store/lakebase/store.py`, `session/DomainSession.py` | Fix |
| last_build persistence | `registry/store/base.py`, `digitaltwin/_build_pipeline.py` | Fix |
| Lakeflow cold-start | `back/core/triplestore/TripleStoreFactory.py`, `digitaltwin/_build_pipeline.py` | Fix |
| Status-gate Explorer fix | `src/shared/fastapi/main.py` | Fix |
| Designer overlap fix | `ontology-map.js`, `mapping-design.js`, `ontoviz.js` | Fix |
| Makefile test isolation | `Makefile` | Infrastructure |
| Version | `pyproject.toml` | Bumped to `0.6.0` |

---

## What is NOT changed

- External API contract — all `/api/v1/` endpoints are backward-compatible.
- MCP tool contracts — no MCP tool signatures changed.
- Existing v0.5.x registry tables — no columns dropped or modified.
- R2RML semantic content — column quoting only affects serialization; all triples and
  mappings are logically identical.
- Any Databricks workspace configuration (warehouses, catalogs, volumes, Lakebase
  settings).

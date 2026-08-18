-- 20260818120000_agents
--
-- Agents as a first-class resource, plus the three things that hang off them:
-- sessions (a body of work), connectors (where tools come from), and promoted
-- tools (accepted workflows that became callable).
--
-- Shape notes:
--   agents.settings is deliberately a JSONB bag whose keys are exactly the
--   AIAgent node's NodeDefinition.parameters keys (maxIterations, temperature,
--   enableSubAgents, ...). That is what lets AgentRuntime pass it straight into
--   the existing 2,745-line loop without a translation layer that would drift.
--
--   The session owns the workspace and the memory key; the run owns the trace.
--   That split is why turn 9 still sees what turn 1 wrote.

-- migrate:up

SET search_path TO "workflow-app";

-- ---------------------------------------------------------------------------
-- The agent: a DEFINITION, not an actor. Holds no run state, so N people can
-- run the same one concurrently without touching each other.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents (
    id             TEXT PRIMARY KEY,
    team_id        TEXT NOT NULL DEFAULT 'default' REFERENCES teams(id) ON DELETE CASCADE,
    folder_id      TEXT REFERENCES folders(id) ON DELETE SET NULL,
    name           TEXT NOT NULL,
    description    TEXT,
    -- derived from bound tools, not declared: asks | builds | watches
    role           TEXT NOT NULL DEFAULT 'asks',
    model          TEXT NOT NULL DEFAULT 'claude-sonnet-4-20250514',
    system_prompt  TEXT NOT NULL DEFAULT '',
    task_template  TEXT,
    settings       JSONB NOT NULL DEFAULT '{}',
    memory         JSONB,
    output_schema  JSONB,
    -- monotonic, bumped on any config-changing update; sessions pin it
    version        INTEGER NOT NULL DEFAULT 1,
    active         BOOLEAN NOT NULL DEFAULT TRUE,
    archived_at    TIMESTAMP,
    created_by     TEXT,
    updated_by     TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT now(),
    updated_at     TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agents_team ON agents (team_id);
CREATE INDEX IF NOT EXISTS ix_agents_name ON agents (name);

-- ---------------------------------------------------------------------------
-- Tool bindings. source discriminates where the tool comes from.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_tool_bindings (
    id             SERIAL PRIMARY KEY,
    agent_id       TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    source         TEXT NOT NULL DEFAULT 'builtin'
                   CHECK (source IN ('builtin','mcp','openapi','node','promoted')),
    connector_id   TEXT,
    tool_key       TEXT NOT NULL,
    alias          TEXT,
    config         JSONB NOT NULL DEFAULT '{}',
    requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    position       INTEGER NOT NULL DEFAULT 0,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_atb_agent ON agent_tool_bindings (agent_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_atb_unique
    ON agent_tool_bindings (agent_id, source, COALESCE(connector_id, ''), tool_key);

-- ---------------------------------------------------------------------------
-- A session is a continuous body of work with one agent. Team-visible,
-- single-writer. Pins the agent config so behaviour cannot shift mid-thread.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_sessions (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    team_id         TEXT NOT NULL DEFAULT 'default' REFERENCES teams(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','idle','archived')),
    agent_version   INTEGER NOT NULL DEFAULT 1,
    agent_config    JSONB NOT NULL DEFAULT '{}',
    app_id          TEXT REFERENCES apps(id) ON DELETE SET NULL,
    workflow_id     TEXT REFERENCES workflows(id) ON DELETE SET NULL,
    -- never the literal 'default': that is the cross-tenant leak
    memory_key      TEXT NOT NULL,
    created_by      TEXT,
    holder_id       TEXT,
    holder_since    TIMESTAMP,
    run_count       INTEGER NOT NULL DEFAULT 0,
    last_run_at     TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sessions_team ON agent_sessions (team_id, last_run_at DESC);
CREATE INDEX IF NOT EXISTS ix_sessions_agent ON agent_sessions (agent_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_memkey ON agent_sessions (memory_key);

-- ---------------------------------------------------------------------------
-- A run is ONE TURN within a session. Immutable once terminal.
-- Its own tables, not `executions`: ExecutionRepository._cleanup() keeps only
-- the newest 100 rows globally, which would evaporate agent traces.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_runs (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    agent_id            TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    team_id             TEXT NOT NULL DEFAULT 'default',
    turn                INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','running','waiting','success','failed','cancelled')),
    trigger             TEXT NOT NULL DEFAULT 'studio',
    task                TEXT NOT NULL,
    input               JSONB NOT NULL DEFAULT '{}',
    agent_snapshot      JSONB NOT NULL DEFAULT '{}',
    response            TEXT,
    structured_output   JSONB,
    error               TEXT,
    iterations          INTEGER NOT NULL DEFAULT 0,
    tool_call_count     INTEGER NOT NULL DEFAULT 0,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    llm_time_ms         DOUBLE PRECISION NOT NULL DEFAULT 0,
    event_count         INTEGER NOT NULL DEFAULT 0,
    parent_execution_id TEXT,
    created_by          TEXT,
    started_at          TIMESTAMP NOT NULL DEFAULT now(),
    ended_at            TIMESTAMP,
    cancelled_at        TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_runs_session ON agent_runs (session_id, turn);
CREATE INDEX IF NOT EXISTS ix_runs_agent ON agent_runs (agent_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_runs_status ON agent_runs (status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_turn ON agent_runs (session_id, turn);

-- ---------------------------------------------------------------------------
-- The durable trace: one row per agent:* event.
-- Row-per-event, not a JSONB blob, so a killed pod still leaves a partial
-- trace and ?after_seq= replay works for free.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_run_events (
    id         BIGSERIAL PRIMARY KEY,
    run_id     TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    type       TEXT NOT NULL,
    node_name  TEXT,
    payload    JSONB NOT NULL DEFAULT '{}',
    truncated  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_seq ON agent_run_events (run_id, seq);
CREATE INDEX IF NOT EXISTS ix_events_run ON agent_run_events (run_id, id);

-- ---------------------------------------------------------------------------
-- Approvals: a tool call held pending a human decision.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_approvals (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    tool_name    TEXT NOT NULL,
    arguments    JSONB NOT NULL DEFAULT '{}',
    reason       TEXT,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','approved','denied','cancelled')),
    scope        TEXT,
    decided_by   TEXT,
    decision_note TEXT,
    created_at   TIMESTAMP NOT NULL DEFAULT now(),
    decided_at   TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_approvals_run ON agent_approvals (run_id, status);

-- ---------------------------------------------------------------------------
-- Connectors: where tools come from beyond the builtin set.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tool_connectors (
    id            TEXT PRIMARY KEY,
    team_id       TEXT NOT NULL DEFAULT 'default',
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('mcp','openapi')),
    base_url      TEXT NOT NULL,
    spec_url      TEXT,
    tool_prefix   TEXT NOT NULL DEFAULT '',
    config        JSONB NOT NULL DEFAULT '{}',
    selection     JSONB NOT NULL DEFAULT '{}',
    headers       JSONB NOT NULL DEFAULT '{}',
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    status        TEXT NOT NULL DEFAULT 'pending',
    last_error    TEXT,
    source_hash   TEXT,
    instructions  TEXT,
    last_discovered_at TIMESTAMP,
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    updated_at    TIMESTAMP NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_connectors_name ON tool_connectors (team_id, name);

CREATE TABLE IF NOT EXISTS connector_tools (
    id            BIGSERIAL PRIMARY KEY,
    connector_id  TEXT NOT NULL REFERENCES tool_connectors(id) ON DELETE CASCADE,
    remote_id     TEXT NOT NULL,
    tool_name     TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    input_schema  JSONB NOT NULL DEFAULT '{}',
    optional_args JSONB NOT NULL DEFAULT '[]',
    invoke        JSONB NOT NULL DEFAULT '{}',
    selected      BOOLEAN NOT NULL DEFAULT FALSE,
    read_only     BOOLEAN NOT NULL DEFAULT TRUE,
    unsupported_reason TEXT,
    schema_hash   TEXT NOT NULL DEFAULT '',
    est_tokens    INTEGER NOT NULL DEFAULT 0,
    first_seen_at TIMESTAMP NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMP NOT NULL DEFAULT now(),
    removed_at    TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ctools_remote ON connector_tools (connector_id, remote_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ctools_name ON connector_tools (connector_id, tool_name);

-- ---------------------------------------------------------------------------
-- The flywheel: an accepted workflow registered as a callable tool.
-- Pinned to ONE version — publishing v6 does not reach a bound agent until
-- someone adopts it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS promoted_tools (
    id            TEXT PRIMARY KEY,
    team_id       TEXT NOT NULL DEFAULT 'default',
    workflow_id   TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    version_id    INTEGER REFERENCES workflow_versions(id) ON DELETE SET NULL,
    version_number INTEGER,
    tool_name     TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    input_schema  JSONB NOT NULL DEFAULT '{}',
    est_tokens    INTEGER NOT NULL DEFAULT 0,
    call_count    INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count    INTEGER NOT NULL DEFAULT 0,
    recent_results JSONB NOT NULL DEFAULT '[]',
    demoted_at    TIMESTAMP,
    demoted_reason TEXT,
    accepted_by   TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    updated_at    TIMESTAMP NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_promoted_name ON promoted_tools (team_id, tool_name);
CREATE INDEX IF NOT EXISTS ix_promoted_workflow ON promoted_tools (workflow_id);

-- migrate:down

SET search_path TO "workflow-app";
DROP TABLE IF EXISTS promoted_tools;
DROP TABLE IF EXISTS connector_tools;
DROP TABLE IF EXISTS tool_connectors;
DROP TABLE IF EXISTS agent_approvals;
DROP TABLE IF EXISTS agent_run_events;
DROP TABLE IF EXISTS agent_runs;
DROP TABLE IF EXISTS agent_sessions;
DROP TABLE IF EXISTS agent_tool_bindings;
DROP TABLE IF EXISTS agents;

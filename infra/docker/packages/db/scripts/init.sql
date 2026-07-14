-- init.sql
-- Mounted to /docker-entrypoint-initdb.d/init.sql
-- Executed automatically on first container startup when data directory is empty

-- 1. Enable pgvector extension in the agent database.
--    PostgreSQL extensions are scoped per database, not globally.
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create dedicated databases for Langfuse and Temporal.
--    Langfuse and Temporal manage their own schemas via migrations/bootstrap.
\c postgres
CREATE DATABASE langfuse;
CREATE DATABASE temporal;
CREATE DATABASE temporal_visibility;

\c agent

-- Multi-tenant isolation root table
CREATE TABLE IF NOT EXISTS tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(255) NOT NULL,
    plan        VARCHAR(50)  NOT NULL DEFAULT 'starter',   -- starter, growth, enterprise
    settings    JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);


-- Customers (end-users being managed by the tenant)
CREATE TABLE IF NOT EXISTS customers (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_id    VARCHAR(255),                             -- CRM ID (Salesforce, HubSpot, etc.)
    name           VARCHAR(255),
    email          VARCHAR(255),
    health_score   DECIMAL(5,2)  CHECK (health_score >= 0 AND health_score <= 100),  -- 0-100
    mrr            DECIMAL(10,2) DEFAULT 0,                 -- Monthly Recurring Revenue
    renewal_date   DATE,
    nps            INTEGER       CHECK (nps >= -100 AND nps <= 100),  -- latest Net Promoter Score
    usage_trend    JSONB         NOT NULL DEFAULT '{}',     -- rolling usage metrics for query_health
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Interactions (emails, calls — inbound and outbound). The type column is
-- generic, but email is the only active outbound channel (Slack was removed).
CREATE TABLE IF NOT EXISTS interactions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id  UUID         NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    type         VARCHAR(50)  NOT NULL,                     -- email, call, note
    direction    VARCHAR(20)  NOT NULL,                     -- inbound, outbound
    content      TEXT,
    ai_generated BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Agent sessions (customer journey state machine)
CREATE TABLE IF NOT EXISTS agent_sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id UUID         NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    status      VARCHAR(50)  NOT NULL DEFAULT 'monitoring',  -- monitoring, reaching_out, waiting_reply, escalated
    context     JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Audit log (every action logged with actor, before/after state, IP)
CREATE TABLE IF NOT EXISTS audit_logs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    actor        VARCHAR(255),                             -- user_id, agent, system
    action       VARCHAR(100) NOT NULL,                   -- email_sent, crm_updated, status_changed, ...
    resource     VARCHAR(100),                            -- customer, interaction, session, ...
    resource_id  UUID,
    before_state JSONB,
    after_state  JSONB,
    ip_address   VARCHAR(45),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-tenant token usage tracking (for billing and rate limiting)
CREATE TABLE IF NOT EXISTS tenant_usage (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    date         DATE         NOT NULL,
    model        VARCHAR(100) NOT NULL,
    prompt_tokens    INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    cost_usd     DECIMAL(10,4) NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, date, model)
);

-- State machine history (audit trail for agent_sessions transitions)
CREATE TABLE IF NOT EXISTS customer_states (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id UUID         NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    from_status VARCHAR(50),
    to_status   VARCHAR(50) NOT NULL,
    reason      TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Conversation session tracking
CREATE TABLE IF NOT EXISTS sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     VARCHAR(255),
    metadata    JSONB        DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- LLM call audit trail for observability and cost tracking
CREATE TABLE IF NOT EXISTS llm_audit_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    session_id    UUID         REFERENCES sessions(id) ON DELETE SET NULL,
    model         VARCHAR(100) NOT NULL,
    prompt_tokens INT          NOT NULL DEFAULT 0,
    completion_tokens INT      NOT NULL DEFAULT 0,
    latency_ms    INT,
    status        VARCHAR(20)  NOT NULL DEFAULT 'success',
    request_body  JSONB,
    response_body JSONB,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- RAG knowledge base chunks with vector embeddings
-- 1536 dims match the configured embedding model (qwen/qwen3-embedding-8b at
-- 1536 via MRL dimension selection, and the deterministic local fallback vector).
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_doc  VARCHAR(500),
    content     TEXT         NOT NULL,
    embedding   VECTOR(1536),
    metadata    JSONB        DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Distilled per-customer profile, extracted from conversation memory and shared
-- with the signal system (read by query_health for proactive health analysis).
CREATE TABLE IF NOT EXISTS customer_profiles (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                 UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id               UUID         NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    preferences               JSONB        NOT NULL DEFAULT '[]',
    sentiment_signals         JSONB        NOT NULL DEFAULT '[]',
    risk_signals              JSONB        NOT NULL DEFAULT '[]',
    communication_preferences JSONB        NOT NULL DEFAULT '[]',
    entities                  JSONB        NOT NULL DEFAULT '{}',
    last_intent               VARCHAR(50),
    last_sentiment            VARCHAR(50),
    updated_at                TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, customer_id)
);

-- Durable record of every signal enqueued/processed (dashboard + audit).
CREATE TABLE IF NOT EXISTS signals (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id  UUID         REFERENCES customers(id) ON DELETE CASCADE,
    signal_key   VARCHAR(128),                             -- dedupe/idempotency key
    type         VARCHAR(50)  NOT NULL,                    -- renewal_risk, low_health, negative_sentiment, ...
    severity     VARCHAR(20)  NOT NULL DEFAULT 'normal',   -- low, normal, high, critical
    source       VARCHAR(30)  NOT NULL DEFAULT 'manual',   -- detector, chat_bridge, manual
    status       VARCHAR(20)  NOT NULL DEFAULT 'queued',   -- queued, processing, done, failed
    payload      JSONB        NOT NULL DEFAULT '{}',
    result       JSONB,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

-- Compliance-approved external action grants. The orchestrator inserts one row
-- per approved write; the MCP tool-gateway verifies against it before executing.
CREATE TABLE IF NOT EXISTS action_approvals (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_id   VARCHAR(128) NOT NULL,                    -- deterministic id supplied by orchestrator
    tenant_id     UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    action_name   VARCHAR(100) NOT NULL,                    -- send_email, escalate_to_human, ...
    trace_id      VARCHAR(128),
    payload_hash  VARCHAR(64),                              -- sha256 of the approved arguments
    approved_by   VARCHAR(255) NOT NULL DEFAULT 'compliance_critic',
    expires_at    TIMESTAMPTZ,
    consumed_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, approval_id, action_name)
);

-- Tenant-scoped idempotency ledger preventing duplicate external side effects.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    idempotency_key  VARCHAR(128) NOT NULL,
    result           JSONB        NOT NULL,                 -- serialized ActionResult
    expires_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);

-- Performance indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_sessions_tenant_id ON sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_llm_audit_logs_tenant_id ON llm_audit_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_llm_audit_logs_created_at ON llm_audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tenant_id ON knowledge_chunks(tenant_id);

-- HNSW index for efficient approximate nearest neighbor search
-- Only create after table has data in production; safe to define here for dev/init
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);

-- Indexes for customers table
CREATE INDEX IF NOT EXISTS idx_customers_tenant_id ON customers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_customers_external_id ON customers(external_id);
CREATE INDEX IF NOT EXISTS idx_customers_health_score ON customers(health_score);
CREATE INDEX IF NOT EXISTS idx_customers_renewal_date ON customers(renewal_date);

-- Indexes for interactions table
CREATE INDEX IF NOT EXISTS idx_interactions_tenant_id ON interactions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_interactions_customer_id ON interactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_interactions_tenant_customer ON interactions(tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_interactions_created_at ON interactions(created_at DESC);

-- Indexes for agent_sessions table
CREATE INDEX IF NOT EXISTS idx_agent_sessions_tenant_id ON agent_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_customer_id ON agent_sessions(customer_id);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_tenant_customer ON agent_sessions(tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_status ON agent_sessions(status);

-- Indexes for audit_logs table
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_id ON audit_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_created ON audit_logs(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_resource ON audit_logs(resource, resource_id);

-- Indexes for tenant_usage table
CREATE INDEX IF NOT EXISTS idx_tenant_usage_tenant_date ON tenant_usage(tenant_id, date);
CREATE INDEX IF NOT EXISTS idx_tenant_usage_model ON tenant_usage(model);

-- Indexes for customer_states table
CREATE INDEX IF NOT EXISTS idx_customer_states_tenant_customer ON customer_states(tenant_id, customer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_customer_states_to_status ON customer_states(to_status);

-- Indexes for action_approvals and idempotency_keys
CREATE INDEX IF NOT EXISTS idx_action_approvals_lookup
    ON action_approvals(tenant_id, approval_id, action_name);
CREATE INDEX IF NOT EXISTS idx_idempotency_keys_lookup
    ON idempotency_keys(tenant_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_idempotency_keys_expires_at
    ON idempotency_keys(expires_at);

-- Indexes for customer_profiles and signals
CREATE INDEX IF NOT EXISTS idx_customer_profiles_lookup
    ON customer_profiles(tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_signals_tenant_status
    ON signals(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_signals_tenant_customer
    ON signals(tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_signals_created_at
    ON signals(created_at DESC);

-- Row-Level Security (RLS) — enforces multi-tenant isolation at the database level
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE interactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;

-- RLS policy helper: sets the current tenant context for the session
-- Application code calls  SET LOCAL app.current_tenant_id = '<tenant_uuid>'  before queries
CREATE POLICY tenant_isolation_customers ON customers
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_interactions ON interactions
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_agent_sessions ON agent_sessions
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_audit_logs ON audit_logs
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_tenant_usage ON tenant_usage
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_customer_states ON customer_states
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_sessions ON sessions
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_llm_audit_logs ON llm_audit_logs
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_knowledge_chunks ON knowledge_chunks
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_action_approvals ON action_approvals
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_idempotency_keys ON idempotency_keys
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_customer_profiles ON customer_profiles
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY tenant_isolation_signals ON signals
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);

-- ── Auth: platform users and tenant memberships ─────────────────────────────
-- Users are global (not tenant-scoped): a single Customer Success Manager may
-- belong to several tenants. Authorization is derived from tenant_memberships,
-- never from a client-supplied X-Tenant-Id header. Kept in sync with Alembic
-- migration 0002_auth_users_memberships.
CREATE TABLE IF NOT EXISTS users (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email             VARCHAR(320) NOT NULL UNIQUE,
    full_name         VARCHAR(255),
    password_hash     VARCHAR(255) NOT NULL,
    is_platform_admin BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_memberships (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id                  UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role                     VARCHAR(50)  NOT NULL DEFAULT 'csm',  -- platform_admin, tenant_admin, csm, viewer
    receives_qbr             BOOLEAN      NOT NULL DEFAULT TRUE,
    notification_preferences JSONB        NOT NULL DEFAULT '{}',
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_memberships_user ON tenant_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant ON tenant_memberships(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_qbr
    ON tenant_memberships(tenant_id) WHERE receives_qbr = TRUE;

-- ── NPS: surveys and responses ──────────────────────────────────────────────
-- A survey invitation (nps_surveys) is separate from an individual response
-- (nps_responses). A response score is a single 0-10 answer; the aggregate NPS
-- (-100..100) is computed deterministically from responses and is never stored
-- as a customer field. Kept in sync with Alembic migration 0003_nps.
CREATE TABLE IF NOT EXISTS nps_surveys (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id  UUID         NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    status       VARCHAR(20)  NOT NULL DEFAULT 'created',  -- created, sent, responded, expired
    sent_at      TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ,
    responded_at TIMESTAMPTZ,
    workflow_id  VARCHAR(255),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nps_responses (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    survey_id   UUID         NOT NULL REFERENCES nps_surveys(id) ON DELETE CASCADE,
    customer_id UUID         NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    score       INTEGER      NOT NULL CHECK (score >= 0 AND score <= 10),
    comment     TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_nps_surveys_tenant ON nps_surveys(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_nps_surveys_customer ON nps_surveys(tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_nps_responses_tenant ON nps_responses(tenant_id);
CREATE INDEX IF NOT EXISTS idx_nps_responses_survey ON nps_responses(survey_id);

ALTER TABLE nps_surveys ENABLE ROW LEVEL SECURITY;
ALTER TABLE nps_responses ENABLE ROW LEVEL SECURITY;
CREATE POLICY nps_surveys_isolation ON nps_surveys
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
CREATE POLICY nps_responses_isolation ON nps_responses
    FOR ALL USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID);

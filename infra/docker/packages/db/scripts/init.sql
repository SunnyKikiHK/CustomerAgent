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
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Interactions (emails, Slack messages, calls — inbound and outbound)
CREATE TABLE IF NOT EXISTS interactions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id  UUID         NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    type         VARCHAR(50)  NOT NULL,                     -- email, slack, call
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
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_doc  VARCHAR(500),
    content     TEXT         NOT NULL,
    embedding   VECTOR(1024),
    metadata    JSONB        DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
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

// Thin API client. All requests go through the Vite "/api" proxy to the
// FastAPI gateway (see vite.config.js).

const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

export function fetchDemoTenantStatus(tenantId) {
  return request(`/demo/tenants/${encodeURIComponent(tenantId)}/status`);
}

export function ensureDemoTenant(tenantId) {
  return request(`/demo/tenants/${encodeURIComponent(tenantId)}`, {
    method: "POST",
  });
}

export function fetchDemoCustomerStatus(tenantId, customerId) {
  return request(
    `/demo/tenants/${encodeURIComponent(tenantId)}/customers/${encodeURIComponent(customerId)}/status`,
  );
}

export function ensureDemoCustomer(tenantId, customerId) {
  return request(
    `/demo/tenants/${encodeURIComponent(tenantId)}/customers/${encodeURIComponent(customerId)}`,
    { method: "POST" },
  );
}

export function fetchRecentMessages({ tenantId, customerId, limit = 5 }) {
  const params = new URLSearchParams({
    tenant_id: tenantId,
    customer_id: customerId,
    limit: String(Math.min(limit, 5)),
  });
  return request(`/chat/messages?${params.toString()}`, {
    headers: { "X-Tenant-Id": tenantId },
  });
}

export async function streamChatTurn(
  { tenantId, customerId, sessionId, content },
  onEvent,
) {
  const res = await fetch(`${BASE}/chat/turn`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Tenant-Id": tenantId,
    },
    body: JSON.stringify({
      tenant_id: tenantId,
      customer_id: customerId,
      session_id: sessionId,
      content,
      stream: true,
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  if (!res.body) throw new Error("Streaming response body is unavailable");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    // SSE frames are separated by a blank line.
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      if (!block.trim()) continue;
      const lines = block.replace(/\r/g, "").split("\n");
      const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
      const dataLine = lines.find((line) => line.startsWith("data:"))?.slice(5).trim();
      if (!event || dataLine == null) continue;
      try {
        onEvent(event, JSON.parse(dataLine));
      } catch {
        // Ignore malformed frames rather than killing the whole stream.
      }
    }
    if (done) break;
  }
}

export function sendChatTurn({ tenantId, customerId, sessionId, content }) {
  return request("/chat/turn", {
    method: "POST",
    headers: { "X-Tenant-Id": tenantId },
    body: JSON.stringify({
      tenant_id: tenantId,
      customer_id: customerId,
      session_id: sessionId,
      content,
      stream: false,
    }),
  });
}

export function fetchCustomers(tenantId) {
  // Customer list is auth-gated (membership-derived tenant access).
  return request(`/customers?tenant_id=${encodeURIComponent(tenantId)}`, {
    headers: authHeaders(tenantId),
  });
}

export function fetchSignals(tenantId) {
  return request(`/signals?tenant_id=${encodeURIComponent(tenantId)}`, {
    headers: authHeaders(tenantId),
  });
}

export function runScan(tenantId) {
  return request("/signals/scan", {
    method: "POST",
    headers: authHeaders(tenantId),
    body: JSON.stringify({ tenant_id: tenantId }),
  });
}

export function fetchSkills(tenantId) {
  return request(`/skills?tenant_id=${encodeURIComponent(tenantId)}`);
}

// --- Auth + customer simulator ---------------------------------------------
// The simulator write path requires a JWT. We cache a dev token per session and
// attach it as a Bearer header on writes; reads still go through X-Tenant-Id.

let _token = null;

export async function login(email, password) {
  const data = await request("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  _token = data.access_token;
  return data;
}

function authHeaders(tenantId) {
  const headers = { "X-Tenant-Id": tenantId };
  if (_token) headers["Authorization"] = `Bearer ${_token}`;
  return headers;
}

export function createCustomer(tenantId, payload) {
  return request("/customers", {
    method: "POST",
    headers: authHeaders(tenantId),
    body: JSON.stringify(payload),
  });
}

export function updateCustomer(tenantId, customerId, payload) {
  return request(`/customers/${encodeURIComponent(customerId)}`, {
    method: "PATCH",
    headers: authHeaders(tenantId),
    body: JSON.stringify(payload),
  });
}

export function recordUsageEvent(tenantId, customerId, usageTrend) {
  return request(`/customers/${encodeURIComponent(customerId)}/usage-events`, {
    method: "POST",
    headers: authHeaders(tenantId),
    body: JSON.stringify({ usage_trend: usageTrend }),
  });
}

// --- NPS ------------------------------------------------------------------
// The response endpoint is customer-facing (no JWT): the surveyed customer
// submits a 0-10 score for a survey id. Tenant is resolved server-side.

export function submitNpsResponse(tenantId, surveyId, score, comment) {
  return request(`/nps/surveys/${encodeURIComponent(surveyId)}/response`, {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenantId, score, comment: comment || null }),
  });
}

// --- QBR -------------------------------------------------------------------
// Generation and reads require a CSM JWT (write role for generate). Reads use
// the same Bearer token cached by login().

export function generateQbr(tenantId) {
  return request("/qbr/generate", {
    method: "POST",
    headers: authHeaders(tenantId),
  });
}

export function fetchQbrReports(tenantId) {
  return request("/qbr/reports", { headers: authHeaders(tenantId) });
}

export function fetchQbrReport(tenantId, reportId) {
  return request(`/qbr/reports/${encodeURIComponent(reportId)}`, {
    headers: authHeaders(tenantId),
  });
}

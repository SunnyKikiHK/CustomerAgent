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
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const lines = block.split("\n");
      const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
      const data = lines.find((line) => line.startsWith("data:"))?.slice(5).trim();
      if (event && data) onEvent(event, JSON.parse(data));
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
  return request(`/customers?tenant_id=${encodeURIComponent(tenantId)}`, {
    headers: { "X-Tenant-Id": tenantId },
  });
}

export function fetchSignals(tenantId) {
  return request(`/signals?tenant_id=${encodeURIComponent(tenantId)}`, {
    headers: { "X-Tenant-Id": tenantId },
  });
}

export function runScan(tenantId) {
  return request("/signals/scan", {
    method: "POST",
    headers: { "X-Tenant-Id": tenantId },
    body: JSON.stringify({ tenant_id: tenantId }),
  });
}

export function fetchSkills(tenantId) {
  return request(`/skills?tenant_id=${encodeURIComponent(tenantId)}`);
}

import React, { useState, useEffect } from "react";
import {
  fetchCustomers,
  createCustomer,
  updateCustomer,
  runScan,
  login,
} from "../api.js";

// Demo credentials seeded by scripts/seed_users.py. In a real deployment these
// would come from a login screen; the simulator auto-logs-in for convenience.
const DEMO_EMAIL = "csm@demo.test";
const DEMO_PASSWORD = "demo-password";

function emptyForm() {
  return {
    name: "",
    email: "",
    health_score: 70,
    mrr: 0,
    renewal_date: "",
    nps: 8,
    previous_active_users: 100,
    current_active_users: 100,
    support_ticket_count: 0,
  };
}

export default function Simulator({ tenantId }) {
  const [customers, setCustomers] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState(emptyForm());
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      const data = await fetchCustomers(tenantId);
      setCustomers(data.customers || []);
    } catch (err) {
      setStatus(`Failed to load customers: ${err.message}`);
    }
  }

  useEffect(() => {
    // Best-effort dev login so writes carry a bearer token.
    login(DEMO_EMAIL, DEMO_PASSWORD).catch(() => {});
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId]);

  function selectCustomer(id) {
    setSelectedId(id);
    if (!id) {
      setForm(emptyForm());
      return;
    }
    const customer = customers.find((c) => c.id === id);
    if (customer) {
      const trend = customer.usage_trend || {};
      setForm({
        name: customer.name || "",
        email: customer.email || "",
        health_score: customer.health_score ?? 70,
        mrr: customer.mrr ?? 0,
        renewal_date: customer.renewal_date || "",
        nps: 8,
        previous_active_users: trend.previous_active_users ?? 100,
        current_active_users: trend.current_active_users ?? 100,
        support_ticket_count: trend.support_ticket_count ?? 0,
      });
    }
  }

  function set(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSave(runDetectors) {
    setBusy(true);
    setStatus("");
    try {
      const usageTrend = {
        previous_active_users: Number(form.previous_active_users),
        current_active_users: Number(form.current_active_users),
        support_ticket_count: Number(form.support_ticket_count),
      };
      const payload = {
        name: form.name,
        email: form.email,
        health_score: Number(form.health_score),
        mrr: Number(form.mrr),
        renewal_date: form.renewal_date || null,
        usage_trend: usageTrend,
      };
      if (selectedId) {
        await updateCustomer(tenantId, selectedId, payload);
      } else {
        const created = await createCustomer(tenantId, payload);
        setSelectedId(created.id);
      }
      setStatus("Saved.");
      await refresh();
      if (runDetectors) {
        const scan = await runScan(tenantId);
        setStatus(`Saved. Scan enqueued ${scan.enqueued} signal(s).`);
      }
    } catch (err) {
      setStatus(`Save failed: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="simulator">
      <div className="sim-controls">
        <label>Customer</label>
        <select value={selectedId} onChange={(e) => selectCustomer(e.target.value)}>
          <option value="">+ New customer</option>
          {customers.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name || c.id} ({c.health_score ?? "?"})
            </option>
          ))}
        </select>
      </div>

      <div className="sim-grid">
        <label>
          Name
          <input value={form.name} onChange={(e) => set("name", e.target.value)} />
        </label>
        <label>
          Email
          <input value={form.email} onChange={(e) => set("email", e.target.value)} />
        </label>
        <label>
          Health score: {form.health_score}
          <input
            type="range"
            min="0"
            max="100"
            value={form.health_score}
            onChange={(e) => set("health_score", e.target.value)}
          />
        </label>
        <label>
          MRR
          <input
            type="number"
            min="0"
            value={form.mrr}
            onChange={(e) => set("mrr", e.target.value)}
          />
        </label>
        <label>
          Renewal date
          <input
            type="date"
            value={form.renewal_date}
            onChange={(e) => set("renewal_date", e.target.value)}
          />
        </label>
        <label>
          Previous active users
          <input
            type="number"
            min="0"
            value={form.previous_active_users}
            onChange={(e) => set("previous_active_users", e.target.value)}
          />
        </label>
        <label>
          Current active users
          <input
            type="number"
            min="0"
            value={form.current_active_users}
            onChange={(e) => set("current_active_users", e.target.value)}
          />
        </label>
        <label>
          Support tickets
          <input
            type="number"
            min="0"
            value={form.support_ticket_count}
            onChange={(e) => set("support_ticket_count", e.target.value)}
          />
        </label>
      </div>

      <div className="sim-actions">
        <button disabled={busy} onClick={() => handleSave(false)}>
          Save
        </button>
        <button disabled={busy} onClick={() => handleSave(true)}>
          Save and run detectors
        </button>
      </div>

      {status && <p className="sim-status">{status}</p>}
    </div>
  );
}

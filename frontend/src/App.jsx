import React, { useEffect, useState } from "react";
import { ensureDemoTenant, fetchDemoTenantStatus } from "./api.js";
import ChatView from "./views/ChatView.jsx";
import Dashboard from "./views/Dashboard.jsx";
import Simulator from "./views/Simulator.jsx";
import NpsResponse from "./views/NpsResponse.jsx";
import Qbr from "./views/Qbr.jsx";

// Demo tenant/customer seeded by scripts/seed_playbooks.py.
const DEFAULT_TENANT = "11111111-1111-1111-1111-111111111111";
const DEFAULT_CUSTOMER = "22222222-2222-2222-2222-222222222222";

export default function App() {
  const [tab, setTab] = useState("chat");
  const [tenantId, setTenantId] = useState(DEFAULT_TENANT);
  const [tenantExists, setTenantExists] = useState(null);
  const [tenantBusy, setTenantBusy] = useState(false);
  const [tenantStatus, setTenantStatus] = useState("");

  useEffect(() => {
    const normalized = tenantId.trim();
    setTenantStatus("");
    setTenantExists(null);
    if (!normalized) return undefined;

    let active = true;
    const timer = window.setTimeout(async () => {
      try {
        const result = await fetchDemoTenantStatus(normalized);
        if (active) setTenantExists(Boolean(result.exists));
      } catch (error) {
        if (active) setTenantStatus(String(error.message || error));
      }
    }, 300);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [tenantId]);

  async function createTenant() {
    const normalized = tenantId.trim();
    if (!normalized || tenantBusy || tenantExists) return;
    setTenantBusy(true);
    setTenantStatus("");
    try {
      const latest = await fetchDemoTenantStatus(normalized);
      if (latest.exists) {
        setTenantExists(true);
        setTenantStatus("Tenant already exists; no create request sent.");
        return;
      }
      const result = await ensureDemoTenant(normalized);
      setTenantExists(true);
      setTenantStatus(result.created ? "Tenant created." : "Tenant already exists.");
    } catch (error) {
      setTenantStatus(`Tenant creation failed: ${error.message || error}`);
    } finally {
      setTenantBusy(false);
    }
  }

  return (
    <div className="app">
      <header className="header">
        <h1>Customer Success Agent</h1>
        <div className="tenant">
          <label>Tenant ID</label>
          <input
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value.trim())}
            spellCheck={false}
          />
          <button
            type="button"
            onClick={createTenant}
            disabled={!tenantId || tenantBusy || tenantExists === true}
          >
            {tenantBusy ? "Creating…" : tenantExists ? "Tenant exists" : "Create tenant"}
          </button>
          {tenantStatus && <span className="inline-status">{tenantStatus}</span>}
        </div>
      </header>

      <nav className="tabs">
        <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
          Chat
        </button>
        <button className={tab === "dashboard" ? "active" : ""} onClick={() => setTab("dashboard")}>
          Signal Dashboard
        </button>
        <button className={tab === "simulator" ? "active" : ""} onClick={() => setTab("simulator")}>
          Customer Simulator
        </button>
        <button className={tab === "nps" ? "active" : ""} onClick={() => setTab("nps")}>
          NPS Response
        </button>
        <button className={tab === "qbr" ? "active" : ""} onClick={() => setTab("qbr")}>
          QBR
        </button>
      </nav>

      <main className="main">
        <div hidden={tab !== "chat"}>
          <ChatView tenantId={tenantId} defaultCustomerId={DEFAULT_CUSTOMER} />
        </div>
        {tab === "dashboard" && <Dashboard tenantId={tenantId} />}
        {tab === "simulator" && <Simulator tenantId={tenantId} />}
        {tab === "nps" && <NpsResponse tenantId={tenantId} />}
        {tab === "qbr" && <Qbr tenantId={tenantId} />}
      </main>
    </div>
  );
}

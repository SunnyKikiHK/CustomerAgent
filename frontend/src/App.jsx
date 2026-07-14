import React, { useState } from "react";
import ChatView from "./views/ChatView.jsx";
import Dashboard from "./views/Dashboard.jsx";

// Demo tenant/customer seeded by scripts/seed_playbooks.py.
const DEFAULT_TENANT = "11111111-1111-1111-1111-111111111111";
const DEFAULT_CUSTOMER = "22222222-2222-2222-2222-222222222222";

export default function App() {
  const [tab, setTab] = useState("chat");
  const [tenantId, setTenantId] = useState(DEFAULT_TENANT);

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
        </div>
      </header>

      <nav className="tabs">
        <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
          Chat
        </button>
        <button className={tab === "dashboard" ? "active" : ""} onClick={() => setTab("dashboard")}>
          Signal Dashboard
        </button>
      </nav>

      <main className="main">
        <div hidden={tab !== "chat"}>
          <ChatView tenantId={tenantId} defaultCustomerId={DEFAULT_CUSTOMER} />
        </div>
        {tab === "dashboard" && <Dashboard tenantId={tenantId} />}
      </main>
    </div>
  );
}

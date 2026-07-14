import React, { useState, useEffect, useCallback } from "react";
import { fetchCustomers, fetchSignals, runScan } from "../api.js";

function healthClass(score) {
  if (score == null) return "";
  if (score < 50) return "bad";
  if (score < 70) return "warn";
  return "good";
}

export default function Dashboard({ tenantId }) {
  const [customers, setCustomers] = useState([]);
  const [signals, setSignals] = useState([]);
  const [error, setError] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState(null);

  const load = useCallback(async () => {
    setError("");
    try {
      const [c, s] = await Promise.all([fetchCustomers(tenantId), fetchSignals(tenantId)]);
      setCustomers(c.customers || []);
      setSignals(s.signals || []);
    } catch (err) {
      setError(String(err.message || err));
    }
  }, [tenantId]);

  useEffect(() => {
    load();
  }, [load]);

  async function onScan() {
    setScanning(true);
    setScanResult(null);
    setError("");
    try {
      const res = await runScan(tenantId);
      setScanResult(res);
      await load();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setScanning(false);
    }
  }

  return (
    <div className="dashboard">
      <div className="toolbar">
        <button onClick={load}>Refresh</button>
        <button className="primary" onClick={onScan} disabled={scanning}>
          {scanning ? "Scanning…" : "Run detector scan"}
        </button>
        {scanResult && (
          <span className="muted">
            detected {scanResult.detected}, enqueued {scanResult.enqueued}
          </span>
        )}
      </div>

      {error && <div className="error">{error}</div>}

      <section>
        <h2>Customers</h2>
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Health</th>
              <th>MRR</th>
              <th>Renewal</th>
              <th>NPS</th>
            </tr>
          </thead>
          <tbody>
            {customers.map((c) => (
              <tr key={c.id}>
                <td>{c.name || c.id}</td>
                <td><span className={`pill ${healthClass(c.health_score)}`}>{c.health_score ?? "—"}</span></td>
                <td>{c.mrr != null ? `$${c.mrr}` : "—"}</td>
                <td>{c.renewal_date || "—"}</td>
                <td>{c.nps ?? "—"}</td>
              </tr>
            ))}
            {customers.length === 0 && (
              <tr><td colSpan={5} className="muted center">No customers. Seed the demo tenant first.</td></tr>
            )}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Signals</h2>
        <table>
          <thead>
            <tr>
              <th>Type</th>
              <th>Severity</th>
              <th>Source</th>
              <th>Status</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {signals.map((s) => (
              <tr key={s.id}>
                <td>{s.type}</td>
                <td>{s.severity}</td>
                <td>{s.source}</td>
                <td><span className={`pill status-${s.status}`}>{s.status}</span></td>
                <td>{s.created_at ? new Date(s.created_at).toLocaleString() : "—"}</td>
              </tr>
            ))}
            {signals.length === 0 && (
              <tr><td colSpan={5} className="muted center">No signals yet. Run a detector scan.</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}

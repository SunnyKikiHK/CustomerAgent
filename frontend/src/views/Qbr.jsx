import React, { useState, useEffect } from "react";
import { generateQbr, fetchQbrReports, fetchQbrReport, login } from "../api.js";

const DEMO_EMAIL = "csm@demo.test";
const DEMO_PASSWORD = "demo-password";

// Tenant QBR view: trigger generation, list reports, and preview one report's
// deterministic metrics snapshot plus its generated narrative.
export default function Qbr({ tenantId }) {
  const [reports, setReports] = useState([]);
  const [selected, setSelected] = useState(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      const data = await fetchQbrReports(tenantId);
      setReports(data.reports || []);
    } catch (err) {
      setStatus(`Failed to load reports: ${err.message}`);
    }
  }

  useEffect(() => {
    login(DEMO_EMAIL, DEMO_PASSWORD).catch(() => {});
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId]);

  async function generate() {
    setBusy(true);
    setStatus("Generating QBR...");
    try {
      const result = await generateQbr(tenantId);
      setStatus(`Generation started (${result.mode}).`);
      await refresh();
    } catch (err) {
      setStatus(`Generate failed: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function open(reportId) {
    try {
      const report = await fetchQbrReport(tenantId, reportId);
      setSelected(report);
    } catch (err) {
      setStatus(`Failed to open report: ${err.message}`);
    }
  }

  return (
    <div className="qbr">
      <div className="sim-actions">
        <button disabled={busy} onClick={generate}>
          Generate QBR
        </button>
      </div>
      {status && <p className="sim-status">{status}</p>}

      <div className="qbr-layout">
        <div className="qbr-list">
          <h3>Reports</h3>
          {reports.map((r) => (
            <button key={r.id} className="sim-list-item" onClick={() => open(r.id)}>
              {r.period_start} to {r.period_end} — {r.status}
            </button>
          ))}
          {reports.length === 0 && <p className="sim-status">No reports yet.</p>}
        </div>

        <div className="qbr-detail">
          {selected ? (
            <>
              <h3>
                QBR {selected.period_start} to {selected.period_end} ({selected.status})
              </h3>
              <h4>Metrics snapshot</h4>
              <pre className="qbr-snapshot">
                {JSON.stringify(selected.metrics_snapshot, null, 2)}
              </pre>
              <h4>Narrative</h4>
              <pre className="qbr-narrative">
                {selected.report_markdown || "(not generated)"}
              </pre>
            </>
          ) : (
            <p className="sim-status">Select a report to preview.</p>
          )}
        </div>
      </div>
    </div>
  );
}

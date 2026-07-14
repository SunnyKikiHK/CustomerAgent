import React, { useState } from "react";
import { submitNpsResponse } from "../api.js";

// Minimal customer-facing NPS response form. In a real deployment the survey id
// comes from the emailed link (/nps/:surveyId); here it is entered directly so
// the flow can be exercised from the demo UI. Score is strictly 0-10.
export default function NpsResponse({ tenantId }) {
  const [surveyId, setSurveyId] = useState("");
  const [score, setScore] = useState(9);
  const [comment, setComment] = useState("");
  const [status, setStatus] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setStatus("");
    setResult(null);
    try {
      const data = await submitNpsResponse(tenantId, surveyId.trim(), Number(score), comment);
      setResult(data);
      setStatus(`Recorded: ${data.classification} (score ${data.score}).`);
    } catch (err) {
      setStatus(`Failed: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="nps-response">
      <h2>NPS Survey Response</h2>
      <p className="sim-status">
        How likely are you to recommend us to a colleague? (0 = not at all, 10 = extremely)
      </p>
      <div className="sim-controls">
        <label>
          Survey ID
          <input
            value={surveyId}
            onChange={(e) => setSurveyId(e.target.value)}
            placeholder="survey uuid from the invitation link"
            spellCheck={false}
          />
        </label>
        <label>
          Score: {score}
          <input
            type="range"
            min={0}
            max={10}
            value={score}
            onChange={(e) => setScore(e.target.value)}
          />
        </label>
        <label>
          Comment (optional)
          <input value={comment} onChange={(e) => setComment(e.target.value)} />
        </label>
      </div>
      <div className="sim-actions">
        <button disabled={busy || !surveyId.trim()} onClick={submit}>
          Submit response
        </button>
      </div>
      {status && <p className="sim-status">{status}</p>}
      {result && result.classification === "detractor" && (
        <p className="pill bad">Detractor — a CSM will follow up.</p>
      )}
    </div>
  );
}

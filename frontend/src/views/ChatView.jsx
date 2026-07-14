import React, { useState, useRef, useEffect } from "react";
import { streamChatTurn } from "../api.js";

function makeSessionId() {
  return "sess-" + Math.random().toString(36).slice(2, 10);
}

export default function ChatView({ tenantId, defaultCustomerId }) {
  const [customerId, setCustomerId] = useState(defaultCustomerId);
  const [sessionId] = useState(makeSessionId);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("");
  const [error, setError] = useState("");
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function submit(e) {
    e.preventDefault();
    const content = input.trim();
    if (!content || busy) return;
    setError("");
    setInput("");
    setMessages((m) => [...m, { role: "user", content }]);
    setBusy(true);
    setPhase("Planning response");
    setMessages((m) => [...m, { role: "assistant", content: "", pending: true }]);
    try {
      await streamChatTurn(
        { tenantId, customerId, sessionId, content },
        (event, data) => {
          if (event === "status") {
            setPhase(data.message || data.phase || "Working");
          } else if (event === "token") {
            setMessages((current) => current.map((message, index) =>
              index === current.length - 1
                ? { ...message, content: message.content + (data.text || "") }
                : message
            ));
          } else if (event === "done") {
            setMessages((current) => current.map((message, index) =>
              index === current.length - 1
                ? { ...message, content: data.text || message.content, pending: false, approved: true }
                : message
            ));
          } else if (event === "error") {
            setMessages((current) => current.map((message, index) =>
              index === current.length - 1
                ? {
                    ...message,
                    content: data.message || "The response was blocked for safety.",
                    pending: false,
                    approved: false,
                    action: data.action,
                  }
                : message
            ));
          }
        },
      );
    } catch (err) {
      setMessages((current) => current.filter((message) => !message.pending));
      setError(String(err.message || err));
    } finally {
      setBusy(false);
      setPhase("");
    }
  }

  return (
    <div className="chat">
      <div className="chat-config">
        <label>Customer ID</label>
        <input value={customerId} onChange={(e) => setCustomerId(e.target.value.trim())} spellCheck={false} />
        <span className="muted">session {sessionId}</span>
      </div>

      <div className="messages">
        {messages.length === 0 && (
          <p className="muted center">
            Try: "I need a refund for my invoice" or "the app keeps crashing"
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="bubble">
              {m.content}
              {m.role === "assistant" && m.approved === false && (
                <span className="badge blocked">
                  {m.action === "compliance_retry_exhausted"
                    ? "safe fallback"
                    : "not approved"}
                </span>
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="agent-status" role="status">
            <span className="status-dot" />
            {phase || "Working"}
          </div>
        )}
        <div ref={endRef} />
      </div>

      {error && <div className="error">{error}</div>}

      <form className="composer" onSubmit={submit}>
        <input
          placeholder="Type a customer message…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>Send</button>
      </form>
    </div>
  );
}

import React, { useState, useRef, useEffect } from "react";
import {
  ensureDemoCustomer,
  fetchDemoCustomerStatus,
  fetchRecentMessages,
  streamChatTurn,
} from "../api.js";

function makeSessionId() {
  return "sess-" + Math.random().toString(36).slice(2, 10);
}

export default function ChatView({ tenantId, defaultCustomerId }) {
  const [customerId, setCustomerId] = useState(defaultCustomerId);
  const [sessionId] = useState(makeSessionId);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [phase, setPhase] = useState("");
  const [error, setError] = useState("");
  const [customerExists, setCustomerExists] = useState(null);
  const [customerBusy, setCustomerBusy] = useState(false);
  const [customerStatus, setCustomerStatus] = useState("");
  const endRef = useRef(null);
  // Track whether the stream produced a terminal event (done/error).
  const finishedRef = useRef(false);

  useEffect(() => {
    const normalizedTenantId = tenantId.trim();
    const normalizedCustomerId = customerId.trim();
    if (!normalizedTenantId || !normalizedCustomerId) return undefined;

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoadingHistory(true);
      setError("");
      try {
        const result = await fetchRecentMessages({
          tenantId: normalizedTenantId,
          customerId: normalizedCustomerId,
          limit: 5,
        });
        if (!controller.signal.aborted) {
          setMessages((result.messages || []).slice(-5));
        }
      } catch (err) {
        if (!controller.signal.aborted) setError(String(err.message || err));
      } finally {
        if (!controller.signal.aborted) setLoadingHistory(false);
      }
    }, 300);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [tenantId, customerId]);

  useEffect(() => {
    const normalizedTenantId = tenantId.trim();
    const normalizedCustomerId = customerId.trim();
    setCustomerExists(null);
    setCustomerStatus("");
    if (!normalizedTenantId || !normalizedCustomerId) return undefined;

    let active = true;
    const timer = window.setTimeout(async () => {
      try {
        const result = await fetchDemoCustomerStatus(
          normalizedTenantId,
          normalizedCustomerId,
        );
        if (active) setCustomerExists(Boolean(result.exists));
      } catch (err) {
        if (active) setCustomerStatus(String(err.message || err));
      }
    }, 300);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [tenantId, customerId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, phase]);

  async function createCustomer() {
    const normalizedTenantId = tenantId.trim();
    const normalizedCustomerId = customerId.trim();
    if (
      !normalizedTenantId ||
      !normalizedCustomerId ||
      customerBusy ||
      customerExists
    ) return;

    setCustomerBusy(true);
    setCustomerStatus("");
    try {
      const latest = await fetchDemoCustomerStatus(
        normalizedTenantId,
        normalizedCustomerId,
      );
      if (latest.exists) {
        setCustomerExists(true);
        setCustomerStatus("Customer already exists; no create request sent.");
        return;
      }
      const result = await ensureDemoCustomer(
        normalizedTenantId,
        normalizedCustomerId,
      );
      setCustomerExists(true);
      setCustomerStatus(
        result.created ? "Customer created." : "Customer already exists.",
      );
    } catch (err) {
      setCustomerStatus(`Customer creation failed: ${err.message || err}`);
    } finally {
      setCustomerBusy(false);
    }
  }

  function patchLastAssistant(patch) {
    setMessages((current) =>
      current.map((message, index) =>
        index === current.length - 1 ? { ...message, ...patch } : message
      )
    );
  }

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
    finishedRef.current = false;
    try {
      await streamChatTurn(
        { tenantId, customerId, sessionId, content },
        (event, data) => {
          if (event === "status") {
            setPhase(data.message || data.phase || "Working");
          } else if (event === "token") {
            setMessages((current) =>
              current.map((message, index) =>
                index === current.length - 1
                  ? { ...message, content: message.content + (data.text || "") }
                  : message
              )
            );
          } else if (event === "done") {
            finishedRef.current = true;
            patchLastAssistant({
              content: data.text || "",
              pending: false,
              approved: true,
            });
          } else if (event === "error") {
            finishedRef.current = true;
            patchLastAssistant({
              content: data.message || "The response was blocked for safety.",
              pending: false,
              approved: false,
              action: data.action,
            });
            if (data.action === "stream_failed") {
              setError(data.message || "Chat failed. Please try again.");
            }
          }
        },
      );
      // Stream closed without done/error (proxy drop, server crash mid-turn).
      if (!finishedRef.current) {
        patchLastAssistant({
          content:
            "The connection closed before a reply arrived. The LLM provider may be unreachable from the API container.",
          pending: false,
          approved: false,
          action: "stream_incomplete",
        });
        setError("Chat stream ended without a final answer.");
      }
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
        <button
          type="button"
          onClick={createCustomer}
          disabled={
            !tenantId ||
            !customerId ||
            customerBusy ||
            customerExists === true
          }
        >
          {customerBusy
            ? "Creating…"
            : customerExists
              ? "Customer exists"
              : "Create customer"}
        </button>
        <span className="muted">session {sessionId}</span>
        {customerStatus && <span className="inline-status">{customerStatus}</span>}
      </div>

      <div className="messages">
        {loadingHistory && messages.length === 0 && (
          <p className="muted center">Loading recent messages…</p>
        )}
        {!loadingHistory && messages.length === 0 && (
          <p className="muted center">
            Try: &quot;I need a refund for my invoice&quot; or &quot;the app keeps crashing&quot;
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
                    : m.action === "stream_failed" || m.action === "stream_incomplete"
                      ? "failed"
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

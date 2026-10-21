"use strict";
(() => {
  const key = "smai.session.v1";
  let lastRequest = null;
  const origin = window.location.origin;
  const send = (type, data) => window.parent.postMessage({isStreamlitMessage: true, type, ...data}, origin);
  const safeTransport = () => window.location.protocol === "https:" ||
    ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);

  window.addEventListener("message", event => {
    if (event.source !== window.parent || event.origin !== origin || event.data?.type !== "streamlit:render") return;
    const command = event.data.args?.command;
    if (!command || typeof command.request_id !== "string" || command.request_id === lastRequest) return;
    lastRequest = command.request_id;
    let reply = {request_id: command.request_id, status: "ok", record: null};
    try {
      if (command.op === "clear") {
        window.localStorage.removeItem(key);
      } else if (!safeTransport()) {
        throw new Error("HTTPS_REQUIRED");
      } else if (command.op === "read") {
        const raw = window.localStorage.getItem(key);
        if (raw) {
          if (raw.length > 2048) throw new Error("INVALID_RECORD");
          const record = JSON.parse(raw);
          if (!record || !Number.isFinite(record.expires_at) || record.expires_at <= Date.now() / 1000) {
            window.localStorage.removeItem(key);
          } else reply.record = record;
        }
      } else if (command.op === "write") {
        if (!command.record || !Number.isFinite(command.record.expires_at) || command.record.expires_at <= Date.now() / 1000) {
          throw new Error("INVALID_RECORD");
        }
        const encoded = JSON.stringify(command.record);
        if (encoded.length > 2048) throw new Error("INVALID_RECORD");
        window.localStorage.setItem(key, encoded);
      } else throw new Error("INVALID_COMMAND");
    } catch (_) {
      reply.status = "unavailable";
      try { window.localStorage.removeItem(key); } catch (_) { /* Storage may be denied. */ }
    }
    // No token in URL, log, analytics, cookie, or third-party request.
    send("streamlit:setComponentValue", {value: reply, dataType: "json"});
    send("streamlit:setFrameHeight", {height: 0});
  });
  send("streamlit:componentReady", {apiVersion: 1});
  send("streamlit:setFrameHeight", {height: 0});
})();

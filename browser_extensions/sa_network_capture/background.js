(function () {
  function cleanText(value) {
    return String(value || "").trim();
  }

  async function getEndpoint(message) {
    var explicit = cleanText(message && message.endpoint);
    if (explicit) return explicit.replace(/\/$/, "");
    var stored = await chrome.storage.sync.get({ endpoint: "" });
    return cleanText(stored && stored.endpoint).replace(/\/$/, "");
  }

  async function postSnapshot(message) {
    var endpoint = await getEndpoint(message);
    if (!endpoint) {
      return { ok: false, error: "BRC endpoint not configured" };
    }
    var response = await fetch(endpoint + "/api/webhooks/sa_symbol_capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.payload || {})
    });
    var body = await response.text();
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: "HTTP " + response.status + " " + body.slice(0, 200),
        body: body.slice(0, 500)
      };
    }
    return { ok: true, status: response.status, body: body.slice(0, 500) };
  }

  async function sendDebugPing(message) {
    var endpoint = await getEndpoint(message);
    if (!endpoint) {
      return { ok: false, error: "BRC endpoint not configured" };
    }
    var payload = message.payload || {};
    var extra = payload.extra || {};
    var url = endpoint + "/api/webhooks/sa_debug_ping"
      + "?stage=" + encodeURIComponent(cleanText(payload.stage))
      + "&v=" + encodeURIComponent(cleanText(payload.version))
      + "&href=" + encodeURIComponent(cleanText(payload.href))
      + "&host=" + encodeURIComponent(cleanText(payload.host))
      + "&page_type=" + encodeURIComponent(cleanText(payload.page_type || "symbol"));
    Object.keys(extra).forEach(function (key) {
      if (extra[key] == null || extra[key] === "") return;
      url += "&" + encodeURIComponent(key) + "=" + encodeURIComponent(String(extra[key]));
    });
    var response = await fetch(url, { method: "GET" });
    var body = await response.text();
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: "HTTP " + response.status + " " + body.slice(0, 200),
        body: body.slice(0, 500)
      };
    }
    return { ok: true, status: response.status };
  }

  chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
    (async function () {
      try {
        if (!message || !message.type) {
          sendResponse({ ok: false, error: "missing message type" });
          return;
        }
        if (message.type === "post_snapshot") {
          sendResponse(await postSnapshot(message));
          return;
        }
        if (message.type === "debug_ping") {
          sendResponse(await sendDebugPing(message));
          return;
        }
        sendResponse({ ok: false, error: "unsupported message type" });
      } catch (err) {
        sendResponse({ ok: false, error: err && err.message ? err.message : String(err || "unknown error") });
      }
    })();
    return true;
  });
})();

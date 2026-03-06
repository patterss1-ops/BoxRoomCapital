(function () {
  var lastEndpoint = "";

  function injectHook() {
    var parent = document.documentElement || document.head;
    if (!parent) {
      setTimeout(injectHook, 25);
      return;
    }
    var script = document.createElement("script");
    script.src = chrome.runtime.getURL("page_hook.js");
    script.dataset.extensionVersion = chrome.runtime.getManifest().version;
    parent.appendChild(script);
    script.onload = function () {
      dispatchEndpoint(lastEndpoint);
      script.remove();
    };
  }

  function dispatchEndpoint(endpoint) {
    lastEndpoint = String(endpoint || "").trim();
    var parent = document.documentElement || document;
    if (!parent) return;
    parent.dispatchEvent(new CustomEvent("brc-sa-endpoint", {
      detail: { endpoint: lastEndpoint }
    }));
  }

  function dispatchResponse(detail) {
    var parent = document.documentElement || document;
    if (!parent) return;
    parent.dispatchEvent(new CustomEvent("brc-sa-response", { detail: detail || {} }));
  }

  function handlePageRequest(event) {
    var detail = event && event.detail;
    if (!detail || !detail.requestId || !detail.kind) return;
    chrome.runtime.sendMessage(
      {
        type: detail.kind,
        requestId: detail.requestId,
        endpoint: lastEndpoint,
        payload: detail.payload || {}
      },
      function (response) {
        if (chrome.runtime.lastError) {
          dispatchResponse({
            requestId: detail.requestId,
            ok: false,
            error: chrome.runtime.lastError.message || "runtime sendMessage failed"
          });
          return;
        }
        dispatchResponse({
          requestId: detail.requestId,
          ok: !!(response && response.ok),
          error: response && response.error ? response.error : "",
          status: response && response.status ? response.status : 0,
          body: response && response.body ? response.body : ""
        });
      }
    );
  }

  injectHook();
  (document.documentElement || document).addEventListener("brc-sa-request", handlePageRequest);

  chrome.storage.sync.get({ endpoint: "" }, function (data) {
    dispatchEndpoint(data.endpoint || "");
  });

  chrome.storage.onChanged.addListener(function (changes, area) {
    if (area !== "sync" || !changes.endpoint) return;
    dispatchEndpoint(changes.endpoint.newValue || "");
  });
})();

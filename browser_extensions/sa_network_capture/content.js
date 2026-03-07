(function () {
  var lastEndpoint = "";
  var handledRequestIds = Object.create(null);

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
    try {
      window.postMessage(
        {
          __brc_sa_endpoint: true,
          endpoint: lastEndpoint
        },
        window.location.origin
      );
    } catch (err) {
      window.postMessage(
        {
          __brc_sa_endpoint: true,
          endpoint: lastEndpoint
        },
        "*"
      );
    }
  }

  function dispatchResponse(detail) {
    var parent = document.documentElement || document;
    if (!parent) return;
    parent.dispatchEvent(new CustomEvent("brc-sa-response", { detail: detail || {} }));
    try {
      window.postMessage(
        {
          __brc_sa_response: true,
          detail: detail || {}
        },
        window.location.origin
      );
    } catch (err) {
      window.postMessage(
        {
          __brc_sa_response: true,
          detail: detail || {}
        },
        "*"
      );
    }
  }

  function forwardRequest(detail) {
    if (!detail || !detail.requestId || !detail.kind) return;
    if (handledRequestIds[detail.requestId]) return;
    handledRequestIds[detail.requestId] = Date.now();
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
        setTimeout(function () {
          delete handledRequestIds[detail.requestId];
        }, 30000);
      }
    );
  }

  function handlePageRequest(event) {
    forwardRequest(event && event.detail);
  }

  function handlePageMessage(event) {
    if (event.source !== window) return;
    var data = event.data;
    if (!data || !data.__brc_sa_request) return;
    forwardRequest(data.detail || {});
  }

  injectHook();
  (document.documentElement || document).addEventListener("brc-sa-request", handlePageRequest);
  window.addEventListener("message", handlePageMessage, false);

  chrome.storage.sync.get({ endpoint: "" }, function (data) {
    dispatchEndpoint(data.endpoint || "");
  });

  chrome.storage.onChanged.addListener(function (changes, area) {
    if (area !== "sync" || !changes.endpoint) return;
    dispatchEndpoint(changes.endpoint.newValue || "");
  });
})();

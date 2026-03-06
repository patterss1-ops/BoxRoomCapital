/**
 * Seeking Alpha bookmarklet for BoxRoomCapital.
 *
 * Captures two data classes from the user's authenticated browser session:
 * 1. article/text intelligence for LLM analysis
 * 2. stock-page quant ratings and factor grades for the L8 SA signal path
 */
(function () {
  var ENDPOINT = window.__BRC_ENDPOINT || "%%ENDPOINT%%";
  var PAGE_TEXT = (document.body && document.body.innerText) || "";

  function textOf(selectors) {
    for (var i = 0; i < selectors.length; i++) {
      var el = document.querySelector(selectors[i]);
      if (el && el.textContent && el.textContent.trim()) {
        return el.textContent.trim();
      }
    }
    return "";
  }

  function extractTickerList() {
    var seen = {};
    var out = [];
    var els = document.querySelectorAll(
      'a[href*="/symbol/"], span[data-test-id="ticker-symbol"], .ticker-hover'
    );
    els.forEach(function (el) {
      var value = (el.textContent || "").trim().replace(/[^A-Z.=\-]/g, "");
      if (value && value.length <= 12 && !seen[value]) {
        seen[value] = true;
        out.push(value);
      }
    });
    var urlMatch = window.location.pathname.match(/\/symbol\/([A-Z.=\-]+)/i);
    if (urlMatch) {
      var symbol = urlMatch[1].toUpperCase();
      if (!seen[symbol]) {
        out.unshift(symbol);
      }
    }
    return out;
  }

  function extractNamedRating(label) {
    var re = new RegExp(
      label + "[^A-Za-z]{0,12}(Strong Buy|Buy|Hold|Sell|Strong Sell|Very Bullish|Bullish|Neutral|Bearish|Very Bearish)",
      "i"
    );
    var match = PAGE_TEXT.match(re);
    return match ? match[1] : "";
  }

  function extractQuantScore() {
    var direct = textOf([
      '[data-test-id="quant-score"]',
      '[data-test-id*="quant-score"]',
      '[data-test-id*="quantScore"]',
      '.quant-score',
      '.quantScore'
    ]);
    var text = direct || PAGE_TEXT;
    var match = text.match(/Quant (?:Rating|Score)[^0-9]{0,12}(\d+(?:\.\d+)?)/i);
    if (!match) return null;
    var value = parseFloat(match[1]);
    return isNaN(value) ? null : value;
  }

  function extractGrades() {
    var out = {};
    var candidates = document.querySelectorAll(
      '[data-test-id*="grade"], .factor-grade, [class*="GradeCircle"]'
    );
    candidates.forEach(function (el) {
      var label = ((el.getAttribute("data-test-id") || el.className || "") + " " + (el.getAttribute("aria-label") || "")).toLowerCase();
      var value = (el.textContent || "").trim().toUpperCase();
      if (!/^[A-F][+-]?$/.test(value)) return;
      if (label.indexOf("value") >= 0) out.value = value;
      else if (label.indexOf("growth") >= 0) out.growth = value;
      else if (label.indexOf("profit") >= 0) out.profitability = value;
      else if (label.indexOf("momentum") >= 0) out.momentum = value;
      else if (label.indexOf("revision") >= 0) out.revisions = value;
    });

    if (Object.keys(out).length > 0) {
      return out;
    }

    ["Value", "Growth", "Profitability", "Momentum", "Revisions"].forEach(function (label) {
      var re = new RegExp(label + "[^A-F]{0,12}([A-F][+-]?)", "i");
      var match = PAGE_TEXT.match(re);
      if (match) {
        out[label.toLowerCase()] = match[1].toUpperCase();
      }
    });
    return out;
  }

  function extractArticleContent() {
    var articleEl =
      document.querySelector('[data-test-id="article-content"]') ||
      document.querySelector("article") ||
      document.querySelector(".paywall-full-content") ||
      document.querySelector("#content-area") ||
      document.querySelector(".main-content");
    if (!articleEl) {
      return PAGE_TEXT.substring(0, 15000).trim();
    }
    var clone = articleEl.cloneNode(true);
    clone.querySelectorAll("script, style, iframe, .ad-container, [data-ad]").forEach(function (el) {
      el.remove();
    });
    return clone.textContent.replace(/\s+/g, " ").trim().substring(0, 15000);
  }

  function postJson(path, payload) {
    return fetch(ENDPOINT + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (resp) {
      return resp.json().then(function (body) {
        return { ok: resp.ok, status: resp.status, body: body };
      });
    });
  }

  var tickers = extractTickerList();
  var rating =
    textOf([
      '[data-test-id="quant-rating"]',
      '[data-test-id*="quant-rating"]',
      '.quant-rating',
      '[class*="RatingTag"]'
    ]) || extractNamedRating("Quant (?:Rating|Recommendation)");
  var title =
    textOf(['h1[data-test-id="post-title"]', "h1"]) || document.title || "";
  var data = {
    source: "seeking_alpha",
    url: window.location.href,
    title: title,
    author: textOf([
      '[data-test-id="post-author"] a',
      '[data-test-id="author-name"]',
      '.author-name'
    ]),
    ticker: tickers[0] || "",
    tickers: tickers,
    page_type: window.location.pathname.indexOf("/symbol/") >= 0 ? "symbol" : "article",
    rating: rating,
    quant_score: extractQuantScore(),
    author_rating: extractNamedRating("Authors?['’]?(?: Rating)?"),
    wall_st_rating: extractNamedRating("Wall Street(?: Rating)?"),
    grades: extractGrades(),
    captured_at: new Date().toISOString(),
    content: extractArticleContent()
  };

  var status = document.createElement("div");
  status.style.cssText =
    "position:fixed;top:10px;right:10px;z-index:99999;padding:12px 20px;" +
    "background:#1a1a2e;color:#00ff88;font-family:monospace;font-size:13px;" +
    "border:1px solid #00ff88;border-radius:6px;box-shadow:0 4px 20px rgba(0,255,136,0.3);max-width:420px";
  status.textContent = "Capturing Seeking Alpha data...";
  document.body.appendChild(status);

  var requests = [];
  var hasQuant = !!(data.ticker && (data.rating || data.quant_score !== null || Object.keys(data.grades).length));
  var hasIntel = !!(data.content && data.content.length >= 200);

  if (hasQuant) {
    requests.push(postJson("/api/webhooks/sa_quant_capture", data));
  }
  if (hasIntel) {
    requests.push(postJson("/api/webhooks/sa_intel", data));
  }

  if (!requests.length) {
    status.style.borderColor = "#ff4444";
    status.style.color = "#ff4444";
    status.textContent = "No article or quant data detected on this page.";
    setTimeout(function () { status.remove(); }, 5000);
    return;
  }

  Promise.allSettled(requests)
    .then(function (results) {
      var messages = [];
      var failures = [];
      results.forEach(function (result) {
        if (result.status !== "fulfilled") {
          failures.push(result.reason && result.reason.message ? result.reason.message : "request failed");
          return;
        }
        var payload = result.value;
        if (!payload.ok || !payload.body || payload.body.ok === false) {
          failures.push((payload.body && (payload.body.error || payload.body.detail)) || ("HTTP " + payload.status));
          return;
        }
        if (payload.body.layer_score) {
          messages.push("SA quant stored for " + payload.body.ticker + " (score " + payload.body.layer_score.score + ")");
        } else if (payload.body.job_id) {
          messages.push("LLM intel queued " + String(payload.body.job_id).substring(0, 8));
        } else if (payload.body.message) {
          messages.push(payload.body.message);
        }
      });

      if (failures.length) {
        status.style.borderColor = "#ff4444";
        status.style.color = "#ff4444";
        status.textContent = failures.join(" | ");
      } else {
        status.textContent = messages.join(" | ");
      }
      setTimeout(function () { status.remove(); }, 7000);
    })
    .catch(function (err) {
      status.style.borderColor = "#ff4444";
      status.style.color = "#ff4444";
      status.textContent = "Failed: " + err.message;
      setTimeout(function () { status.remove(); }, 5000);
    });
})();

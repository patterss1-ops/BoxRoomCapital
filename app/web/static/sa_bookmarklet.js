/**
 * Seeking Alpha Bookmarklet for BoxRoomCapital
 *
 * SETUP:
 * 1. Set your BoxRoomCapital URL below (ENDPOINT)
 * 2. Create a new bookmark in your browser
 * 3. Set the URL to: javascript:(function(){...minified version...})()
 *    Or use the install page at /intel/bookmarklet
 *
 * USAGE:
 * 1. Navigate to any Seeking Alpha article or stock page
 * 2. Click the bookmarklet
 * 3. It scrapes the page content and sends to BoxRoomCapital for LLM analysis
 */
(function () {
  // ── Configuration ──────────────────────────────────────
  // Change this to your BoxRoomCapital server URL
  var ENDPOINT = window.__BRC_ENDPOINT || "%%ENDPOINT%%";

  // ── Extract page data ──────────────────────────────────
  var data = { url: window.location.href, source: "seeking_alpha" };

  // Title
  data.title =
    (document.querySelector('h1[data-test-id="post-title"]') ||
      document.querySelector("h1") ||
      {}).textContent || document.title;

  // Author
  var authorEl =
    document.querySelector('[data-test-id="post-author"] a') ||
    document.querySelector('[data-test-id="author-name"]') ||
    document.querySelector(".author-name");
  data.author = authorEl ? authorEl.textContent.trim() : "";

  // Tickers - SA marks them with specific elements
  var tickerEls = document.querySelectorAll(
    'a[href*="/symbol/"], span[data-test-id="ticker-symbol"], .ticker-hover'
  );
  var tickers = [];
  tickerEls.forEach(function (el) {
    var text = el.textContent.trim().replace(/[^A-Z]/g, "");
    if (text && text.length <= 5 && tickers.indexOf(text) === -1) {
      tickers.push(text);
    }
  });
  // Also extract from URL if it's a stock page
  var urlMatch = window.location.pathname.match(/\/symbol\/([A-Z]+)/i);
  if (urlMatch) {
    var t = urlMatch[1].toUpperCase();
    if (tickers.indexOf(t) === -1) tickers.unshift(t);
  }
  data.tickers = tickers;

  // Quant grades (if on a stock page with grades visible)
  var grades = {};
  var gradeEls = document.querySelectorAll(
    '[data-test-id*="grade"], .factor-grade, [class*="GradeCircle"]'
  );
  gradeEls.forEach(function (el) {
    var label = (el.getAttribute("data-test-id") || el.className || "").toLowerCase();
    var value = el.textContent.trim();
    if (value && value.length <= 3) {
      if (label.includes("value")) grades.value = value;
      else if (label.includes("growth")) grades.growth = value;
      else if (label.includes("profitability")) grades.profitability = value;
      else if (label.includes("momentum")) grades.momentum = value;
      else if (label.includes("revision")) grades.revisions = value;
    }
  });
  if (Object.keys(grades).length > 0) data.grades = grades;

  // Rating (quant rating, author rating, etc.)
  var ratingEl =
    document.querySelector('[data-test-id="quant-rating"]') ||
    document.querySelector(".quant-rating") ||
    document.querySelector('[class*="RatingTag"]');
  if (ratingEl) data.rating = ratingEl.textContent.trim();

  // Article content
  var articleEl =
    document.querySelector('[data-test-id="article-content"]') ||
    document.querySelector("article") ||
    document.querySelector(".paywall-full-content") ||
    document.querySelector("#content-area") ||
    document.querySelector(".main-content");

  if (articleEl) {
    // Get text content, preserving some structure
    var clone = articleEl.cloneNode(true);
    // Remove scripts, styles, ads
    clone.querySelectorAll("script, style, iframe, .ad-container, [data-ad]").forEach(function (el) {
      el.remove();
    });
    data.content = clone.textContent
      .replace(/\s+/g, " ")
      .trim()
      .substring(0, 15000);
  } else {
    // Fallback: grab main body text
    data.content = document.body.innerText.substring(0, 15000);
  }

  // ── Send to BoxRoomCapital ─────────────────────────────
  var status = document.createElement("div");
  status.style.cssText =
    "position:fixed;top:10px;right:10px;z-index:99999;padding:12px 20px;" +
    "background:#1a1a2e;color:#00ff88;font-family:monospace;font-size:13px;" +
    "border:1px solid #00ff88;border-radius:6px;box-shadow:0 4px 20px rgba(0,255,136,0.3)";
  status.textContent = "Sending to BoxRoomCapital...";
  document.body.appendChild(status);

  fetch(ENDPOINT + "/api/webhooks/sa_intel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (resp) {
      if (resp.ok) {
        status.style.borderColor = "#00ff88";
        status.textContent =
          "Sent to LLM council! Job: " +
          (resp.job_id || "").substring(0, 8) +
          " | Tickers: " +
          (data.tickers.join(", ") || "none detected");
      } else {
        status.style.borderColor = "#ff4444";
        status.style.color = "#ff4444";
        status.textContent = "Error: " + (resp.error || resp.detail || "unknown");
      }
      setTimeout(function () {
        status.remove();
      }, 5000);
    })
    .catch(function (err) {
      status.style.borderColor = "#ff4444";
      status.style.color = "#ff4444";
      status.textContent = "Failed: " + err.message;
      setTimeout(function () {
        status.remove();
      }, 5000);
    });
})();

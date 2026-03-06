/**
 * Seeking Alpha bookmarklet for BoxRoomCapital.
 *
 * Symbol pages on SA are multi-tab and often lazy-loaded. This bookmarklet now:
 * 1. scrapes the current DOM
 * 2. walks visible tab/button controls on symbol pages
 * 3. fetches same-origin symbol subroutes exposed by the page
 * 4. merges the richest snapshot before posting to BoxRoomCapital
 */
(function () {
  var ENDPOINT = window.__BRC_ENDPOINT || "%%ENDPOINT%%";
  var TAB_WAIT_MS = 1200;
  var MAX_BUTTON_TABS = 10;
  var MAX_ROUTE_TABS = 10;
  var TAB_KEYWORDS = [
    "quant",
    "rating",
    "ratings",
    "factor",
    "grade",
    "grades",
    "valuation",
    "value",
    "growth",
    "profitability",
    "momentum",
    "revision",
    "revisions",
    "dividend",
    "analyst",
    "authors",
    "wall street"
  ];

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function cleanText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function bodyText(root) {
    if (!root || !root.body) return "";
    return cleanText(root.body.innerText || root.body.textContent || "");
  }

  function textOf(selectors, root) {
    var scope = root || document;
    for (var i = 0; i < selectors.length; i++) {
      var el = scope.querySelector(selectors[i]);
      if (el && cleanText(el.textContent)) {
        return cleanText(el.textContent);
      }
    }
    return "";
  }

  function isVisible(el) {
    if (!el || typeof el.getBoundingClientRect !== "function") return false;
    if (el.hidden) return false;
    var style = window.getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    var rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function normalizeHref(href) {
    try {
      var url = new URL(href, window.location.href);
      url.hash = "";
      return url.toString();
    } catch (err) {
      return "";
    }
  }

  function detectPageType(url) {
    if (/\/symbol\//i.test(url)) return "symbol";
    if (/\/article\//i.test(url)) return "article";
    return "article";
  }

  function uniqueList(values) {
    var out = [];
    var seen = {};
    values.forEach(function (value) {
      var clean = cleanText(value).toUpperCase();
      if (!clean || seen[clean]) return;
      seen[clean] = true;
      out.push(clean);
    });
    return out;
  }

  function extractTickerList(root, url) {
    var scope = root || document;
    var seen = {};
    var out = [];
    var els = scope.querySelectorAll(
      'a[href*="/symbol/"], span[data-test-id="ticker-symbol"], .ticker-hover, [data-test-id*="ticker"]'
    );
    els.forEach(function (el) {
      var value = cleanText(el.textContent).replace(/[^A-Z.=\-]/g, "");
      if (value && value.length <= 12 && !seen[value]) {
        seen[value] = true;
        out.push(value);
      }
    });
    var urlMatch = String(url || "").match(/\/symbol\/([A-Z.=\-]+)/i);
    if (urlMatch) {
      var symbol = urlMatch[1].toUpperCase();
      if (!seen[symbol]) {
        out.unshift(symbol);
      } else {
        out = out.filter(function (item) { return item !== symbol; });
        out.unshift(symbol);
      }
    }
    return out;
  }

  function extractNamedRating(label, pageText) {
    var re = new RegExp(
      label + "[^A-Za-z]{0,16}(Strong Buy|Buy|Hold|Sell|Strong Sell|Very Bullish|Bullish|Neutral|Bearish|Very Bearish)",
      "i"
    );
    var match = String(pageText || "").match(re);
    return match ? cleanText(match[1]) : "";
  }

  function extractQuantScore(root, pageText) {
    var direct = textOf([
      '[data-test-id="quant-score"]',
      '[data-test-id*="quant-score"]',
      '[data-test-id*="quantScore"]',
      '.quant-score',
      '.quantScore',
      '[class*="quantScore"]'
    ], root);
    var text = direct || pageText;
    var match = String(text || "").match(/Quant (?:Rating|Score)[^0-9]{0,16}(\d+(?:\.\d+)?)/i);
    if (!match) return null;
    var value = parseFloat(match[1]);
    return isNaN(value) ? null : value;
  }

  function extractGrades(root, pageText) {
    var scope = root || document;
    var out = {};
    var candidates = scope.querySelectorAll(
      '[data-test-id*="grade"], .factor-grade, [class*="GradeCircle"], [aria-label*="grade" i]'
    );
    candidates.forEach(function (el) {
      var label = cleanText(
        (el.getAttribute("data-test-id") || "") + " " +
        (el.className || "") + " " +
        (el.getAttribute("aria-label") || "")
      ).toLowerCase();
      var value = cleanText(el.textContent).toUpperCase();
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
      var re = new RegExp(label + "[^A-F]{0,16}([A-F][+-]?)", "i");
      var match = String(pageText || "").match(re);
      if (match) {
        out[label.toLowerCase()] = match[1].toUpperCase();
      }
    });
    return out;
  }

  function extractArticleContent(root, pageText) {
    var scope = root || document;
    var articleEl =
      scope.querySelector('[data-test-id="article-content"]') ||
      scope.querySelector("article") ||
      scope.querySelector(".paywall-full-content") ||
      scope.querySelector("#content-area") ||
      scope.querySelector(".main-content");
    if (!articleEl) {
      return String(pageText || "").substring(0, 15000).trim();
    }
    var clone = articleEl.cloneNode(true);
    clone.querySelectorAll("script, style, iframe, .ad-container, [data-ad]").forEach(function (el) {
      el.remove();
    });
    return cleanText(clone.textContent).substring(0, 15000);
  }

  function snapshotFromDocument(root, url) {
    var pageText = bodyText(root);
    var tickers = extractTickerList(root, url);
    var pageType = detectPageType(url || window.location.href);
    return {
      source: "seeking_alpha",
      url: url || window.location.href,
      title: textOf(['h1[data-test-id="post-title"]', 'h1[data-test-id="security-header-title"]', "h1"], root) || document.title || "",
      author: textOf([
        '[data-test-id="post-author"] a',
        '[data-test-id="author-name"]',
        '.author-name'
      ], root),
      ticker: tickers[0] || "",
      tickers: tickers,
      page_type: pageType,
      rating:
        textOf([
          '[data-test-id="quant-rating"]',
          '[data-test-id*="quant-rating"]',
          '.quant-rating',
          '[class*="RatingTag"]'
        ], root) || extractNamedRating("Quant (?:Rating|Recommendation)", pageText),
      quant_score: extractQuantScore(root, pageText),
      author_rating: extractNamedRating("Authors?['’]?(?: Rating)?", pageText),
      wall_st_rating: extractNamedRating("Wall Street(?: Rating)?", pageText),
      grades: extractGrades(root, pageText),
      captured_at: new Date().toISOString(),
      content: extractArticleContent(root, pageText)
    };
  }

  function mergeData(base, extra) {
    var merged = Object.assign({}, base || {});
    var next = extra || {};

    merged.source = merged.source || next.source || "seeking_alpha";
    merged.page_type = merged.page_type || next.page_type || "article";
    merged.url = merged.url || next.url || window.location.href;
    merged.title = merged.title || next.title || document.title || "";
    merged.author = merged.author || next.author || "";

    var tickers = uniqueList([].concat(merged.tickers || [], next.tickers || []));
    merged.tickers = tickers;
    if (!merged.ticker && tickers.length) {
      merged.ticker = tickers[0];
    }
    if (next.ticker && !merged.ticker) {
      merged.ticker = next.ticker;
    }

    if (!merged.rating && next.rating) merged.rating = next.rating;
    if (merged.quant_score == null && next.quant_score != null) merged.quant_score = next.quant_score;
    if (!merged.author_rating && next.author_rating) merged.author_rating = next.author_rating;
    if (!merged.wall_st_rating && next.wall_st_rating) merged.wall_st_rating = next.wall_st_rating;

    merged.grades = Object.assign({}, merged.grades || {}, next.grades || {});

    var mergedContent = String(merged.content || "");
    var nextContent = String(next.content || "");
    if (nextContent.length > mergedContent.length) {
      merged.content = nextContent;
    } else {
      merged.content = mergedContent;
    }

    merged.captured_at = new Date().toISOString();
    return merged;
  }

  function keywordScore(label, href) {
    var haystack = (cleanText(label) + " " + cleanText(href)).toLowerCase();
    var score = 0;
    TAB_KEYWORDS.forEach(function (keyword, index) {
      if (haystack.indexOf(keyword) >= 0) {
        score += 100 - index;
      }
    });
    return score;
  }

  function currentSymbolPath() {
    var match = window.location.pathname.match(/\/symbol\/[^/?#]+/i);
    return match ? match[0] : "";
  }

  function collectRouteTabs() {
    var symbolPath = currentSymbolPath();
    if (!symbolPath) return [];
    var origin = window.location.origin;
    var currentUrl = normalizeHref(window.location.href);
    var seen = {};
    var items = [];

    document.querySelectorAll('a[href]').forEach(function (el) {
      if (!isVisible(el)) return;
      var href = normalizeHref(el.getAttribute('href'));
      if (!href || href === currentUrl) return;
      if (href.indexOf(origin + symbolPath) !== 0) return;
      var label = cleanText(el.textContent || el.getAttribute('aria-label') || el.title || href);
      var score = keywordScore(label, href);
      if (!score && href.split('/').length <= symbolPath.split('/').length + 1) {
        return;
      }
      if (seen[href]) return;
      seen[href] = true;
      items.push({ href: href, label: label || href, score: score });
    });

    items.sort(function (a, b) {
      return b.score - a.score || a.label.localeCompare(b.label);
    });
    return items.slice(0, MAX_ROUTE_TABS);
  }

  function collectButtonTabs() {
    var items = [];
    var seen = {};
    var selector = [
      '[role="tab"]',
      '[role="tablist"] button',
      'button[aria-controls]',
      'button[data-test-id*="tab"]',
      'button[class*="tab"]'
    ].join(',');

    document.querySelectorAll(selector).forEach(function (el) {
      if (!isVisible(el)) return;
      var label = cleanText(el.textContent || el.getAttribute('aria-label') || el.title || '');
      if (!label || label.length > 50) return;
      var key = label.toLowerCase();
      if (seen[key]) return;
      seen[key] = true;
      items.push({ element: el, label: label, score: keywordScore(label, '') });
    });

    items.sort(function (a, b) {
      return b.score - a.score || a.label.localeCompare(b.label);
    });
    return items.slice(0, MAX_BUTTON_TABS);
  }

  async function clickButtonTab(item, status) {
    var before = bodyText(document);
    try {
      status.textContent = "Scanning tab: " + item.label;
      item.element.scrollIntoView({ behavior: "instant", block: "center", inline: "center" });
    } catch (err) {
      // ignore scroll errors
    }
    try {
      item.element.click();
    } catch (err) {
      item.element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    }
    await sleep(TAB_WAIT_MS);
    var after = bodyText(document);
    if (after === before) {
      await sleep(400);
    }
    return snapshotFromDocument(document, window.location.href);
  }

  async function fetchRouteTab(item, status) {
    status.textContent = "Fetching route: " + item.label;
    var response = await fetch(item.href, {
      method: 'GET',
      credentials: 'include',
      headers: { 'Accept': 'text/html' }
    });
    if (!response.ok) {
      throw new Error('Route fetch failed ' + response.status + ' for ' + item.label);
    }
    var html = await response.text();
    var parser = new DOMParser();
    var doc = parser.parseFromString(html, 'text/html');
    return snapshotFromDocument(doc, item.href);
  }

  async function enrichSymbolSnapshot(data, status) {
    var merged = mergeData({}, data);
    var routeTabs = collectRouteTabs();
    var buttonTabs = collectButtonTabs();

    for (var i = 0; i < buttonTabs.length; i++) {
      try {
        merged = mergeData(merged, await clickButtonTab(buttonTabs[i], status));
      } catch (err) {
        console.warn('BRC bookmarklet button-tab scan failed', buttonTabs[i].label, err);
      }
    }

    routeTabs = collectRouteTabs();
    for (var j = 0; j < routeTabs.length; j++) {
      try {
        merged = mergeData(merged, await fetchRouteTab(routeTabs[j], status));
      } catch (err) {
        console.warn('BRC bookmarklet route scan failed', routeTabs[j].label, err);
      }
    }

    merged.tickers = uniqueList(merged.tickers || []);
    if (!merged.ticker && merged.tickers.length) {
      merged.ticker = merged.tickers[0];
    }
    return merged;
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

  function showStatusBox() {
    var status = document.createElement("div");
    status.style.cssText =
      "position:fixed;top:10px;right:10px;z-index:99999;padding:12px 20px;" +
      "background:#1a1a2e;color:#00ff88;font-family:monospace;font-size:13px;" +
      "border:1px solid #00ff88;border-radius:6px;box-shadow:0 4px 20px rgba(0,255,136,0.3);max-width:440px";
    status.textContent = "Capturing Seeking Alpha data...";
    document.body.appendChild(status);
    return status;
  }

  async function main() {
    var status = showStatusBox();
    try {
      var data = snapshotFromDocument(document, window.location.href);
      if (data.page_type === 'symbol') {
        data = await enrichSymbolSnapshot(data, status);
      }

      var requests = [];
      var hasQuant = !!(data.ticker && (data.rating || data.quant_score != null || Object.keys(data.grades || {}).length));
      var hasIntel = data.page_type === 'article' && !!(String(data.content || '').length >= 200);

      if (hasQuant) {
        requests.push(postJson('/api/webhooks/sa_quant_capture', data));
      }
      if (hasIntel) {
        requests.push(postJson('/api/webhooks/sa_intel', data));
      }

      if (!requests.length) {
        status.style.borderColor = '#ff4444';
        status.style.color = '#ff4444';
        status.textContent = 'No article or quant data detected on this page.';
        setTimeout(function () { status.remove(); }, 5000);
        return;
      }

      status.textContent = 'Sending captured Seeking Alpha data...';
      var results = await Promise.allSettled(requests);
      var messages = [];
      var failures = [];

      results.forEach(function (result) {
        if (result.status !== 'fulfilled') {
          failures.push(result.reason && result.reason.message ? result.reason.message : 'request failed');
          return;
        }
        var payload = result.value;
        if (!payload.ok || !payload.body || payload.body.ok === false) {
          failures.push((payload.body && (payload.body.error || payload.body.detail)) || ('HTTP ' + payload.status));
          return;
        }
        if (payload.body.layer_score) {
          messages.push('SA quant stored for ' + payload.body.ticker + ' (score ' + payload.body.layer_score.score + ')');
        } else if (payload.body.job_id) {
          messages.push('LLM intel queued ' + String(payload.body.job_id).substring(0, 8));
        } else if (payload.body.message) {
          messages.push(payload.body.message);
        }
      });

      if (failures.length) {
        status.style.borderColor = '#ff4444';
        status.style.color = '#ff4444';
        status.textContent = failures.join(' | ');
      } else {
        status.textContent = messages.join(' | ');
      }
      setTimeout(function () { status.remove(); }, 8000);
    } catch (err) {
      status.style.borderColor = '#ff4444';
      status.style.color = '#ff4444';
      status.textContent = 'Failed: ' + err.message;
      setTimeout(function () { status.remove(); }, 8000);
    }
  }

  main();
})();

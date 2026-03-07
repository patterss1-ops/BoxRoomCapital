(function () {
  var EXT_VERSION = (document.currentScript && document.currentScript.dataset.extensionVersion) || "0.4.5";
  var CAPTURE_SOURCE = "sa-network-extension";
  var TAB_WAIT_MS = 1800;
  var FLUSH_IDLE_MS = 1500;
  var API_FETCH_TIMEOUT_MS = 12000;
  var EXTENSION_TRANSPORT_TIMEOUT_MS = 45000;
  var MAX_RAW_RESPONSES = 48;
  var INTEL_CAPTURE_DELAY_MS = 1400;
  var INTEL_RETRY_DELAY_MS = 3200;
  var HUB_INTEL_WATCH_TIMEOUT_MS = 180000;
  var MAX_INTEL_CONTENT_CHARS = 30000;
  var endpoint = "";
  var endpointAnnounced = false;
  var captureState = null;
  var intelCaptureTimer = 0;
  var intelCaptureStarted = false;
  var intelCaptureInFlight = false;
  var intelCaptureFingerprint = "";
  var intelCaptureSeen = {};
  var intelObserver = null;
  var requestSeq = 0;
  var nativeFetch = window.fetch;
  var GRADE_MAP = {
    1: "F",
    2: "D-",
    3: "D",
    4: "D+",
    5: "C-",
    6: "C",
    7: "C+",
    8: "B-",
    9: "B",
    10: "B+",
    11: "A-",
    12: "A",
    13: "A+"
  };
  var TAB_LABEL_PATTERNS = [
    { section: "quant_ratings", pattern: /quant/i, score: 200 },
    { section: "author_ratings", pattern: /author|sa analysts?/i, score: 180 },
    { section: "sell_side_ratings", pattern: /sell[- ]side|wall st|wall street|analysts?/i, score: 170 },
    { section: "valuation", pattern: /valuation|value/i, score: 140 },
    { section: "financials", pattern: /financial|income|balance sheet|cash flow/i, score: 130 },
    { section: "earnings", pattern: /earnings|revision|transcript/i, score: 120 },
    { section: "dividends", pattern: /dividend|yield/i, score: 110 },
    { section: "peers", pattern: /peer|comparison/i, score: 100 },
    { section: "symbol", pattern: /growth|profitability|momentum/i, score: 90 }
  ];
  var VALUATION_FIELDS = [
    "dividend_yield",
    "ev_12m_sales_ratio",
    "ev_ebit",
    "ev_ebit_fy1",
    "ev_ebitda",
    "ev_ebitda_fy1",
    "ev_sales_fy1",
    "pb_fy1_ratio",
    "pb_ratio",
    "pe_gaap_fy1",
    "pe_nongaap",
    "pe_nongaap_fy1",
    "pe_ratio",
    "peg_gaap",
    "peg_nongaap_fy1",
    "price_cf_ratio",
    "price_cf_ratio_fy1",
    "ps_ratio",
    "ps_ratio_fy1"
  ];
  var CAPITAL_STRUCTURE_FIELDS = [
    "impliedmarketcap",
    "marketcap",
    "other_cap_struct",
    "tev",
    "total_cash",
    "total_debt"
  ];
  var VALUATION_AVERAGE_FIELDS = [
    "dividend_yield_avg_5y",
    "ev_12m_sales_ratio_avg_5y",
    "ev_ebit_avg_5y",
    "ev_ebit_fy1_avg_5y",
    "ev_ebitda_avg_5y",
    "ev_ebitda_fy1_avg_5y",
    "ev_sales_fy1_avg_5y",
    "pb_fy1_ratio_avg_5y",
    "pb_ratio_avg_5y",
    "pe_gaap_fy1_avg_5y",
    "pe_nongaap_avg_5y",
    "pe_nongaap_fy1_avg_5y",
    "pe_ratio_avg_5y",
    "peg_gaap_avg_5y",
    "peg_nongaap_fy1_avg_5y",
    "price_cf_ratio_avg_5y",
    "price_cf_ratio_fy1_avg_5y",
    "ps_ratio_avg_5y",
    "ps_ratio_fy1_avg_5y"
  ];
  var ESTIMATE_DATA_ITEMS = [
    "eps_normalized_actual",
    "eps_normalized_consensus_low",
    "eps_normalized_consensus_mean",
    "eps_normalized_consensus_high",
    "eps_normalized_num_of_estimates"
  ];

  function cleanText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function debugVersion() {
    return "sa-network-extension-" + EXT_VERSION;
  }

  function emitToExtension(kind, payload) {
    return new Promise(function (resolve, reject) {
      var requestId = "brc-sa-" + (++requestSeq) + "-" + Date.now();
      var settled = false;
      var timeoutId = 0;

      function onResponse(event) {
        var detail = event && event.detail;
        if (!detail || detail.requestId !== requestId) return;
        if (settled) return;
        settled = true;
        clearTimeout(timeoutId);
        document.documentElement.removeEventListener("brc-sa-response", onResponse);
        if (detail.ok) {
          resolve(detail);
        } else {
          reject(new Error(detail.error || "extension transport failed"));
        }
      }

      function onWindowMessage(event) {
        if (event.source !== window) return;
        var data = event && event.data;
        if (!data || !data.__brc_sa_response || !data.detail || data.detail.requestId !== requestId) return;
        if (settled) return;
        settled = true;
        clearTimeout(timeoutId);
        document.documentElement.removeEventListener("brc-sa-response", onResponse);
        window.removeEventListener("message", onWindowMessage);
        if (data.detail.ok) {
          resolve(data.detail);
        } else {
          reject(new Error(data.detail.error || "extension transport failed"));
        }
      }

      document.documentElement.addEventListener("brc-sa-response", onResponse);
      window.addEventListener("message", onWindowMessage, false);
      var detail = {
        requestId: requestId,
        kind: kind,
        payload: payload || {}
      };
      document.documentElement.dispatchEvent(new CustomEvent("brc-sa-request", { detail: detail }));
      try {
        window.postMessage({ __brc_sa_request: true, detail: detail }, window.location.origin);
      } catch (err) {
        window.postMessage({ __brc_sa_request: true, detail: detail }, "*");
      }

      timeoutId = setTimeout(function () {
        if (settled) return;
        settled = true;
        document.documentElement.removeEventListener("brc-sa-response", onResponse);
        window.removeEventListener("message", onWindowMessage);
        reject(new Error("extension transport timeout"));
      }, EXTENSION_TRANSPORT_TIMEOUT_MS);
    });
  }

  function isTopFrame() {
    try {
      return window.top === window;
    } catch (err) {
      return false;
    }
  }

  function normalizeHref(href, baseHref) {
    try {
      var url = new URL(href, baseHref || window.location.href);
      url.hash = "";
      return url.toString();
    } catch (err) {
      return "";
    }
  }

  function canonicalSymbolUrl(symbol) {
    return window.location.origin + "/symbol/" + encodeURIComponent(String(symbol || "").toUpperCase());
  }

  function symbolFromPath(pathname) {
    var match = String(pathname || "").match(/\/symbol\/([A-Z0-9.=\-]+)/i);
    return match ? match[1].toUpperCase() : "";
  }

  function symbolFromHref(href) {
    try {
      return symbolFromPath(new URL(href, window.location.href).pathname);
    } catch (err) {
      return "";
    }
  }

  function symbolFromLocation() {
    return symbolFromPath(window.location.pathname || "");
  }

  function lowerSlug(symbol) {
    return String(symbol || "").trim().toLowerCase();
  }

  function toNumber(value) {
    if (value == null || value === "") return null;
    var numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function ratingFromScore(score) {
    var numeric = toNumber(score);
    if (numeric == null) return "";
    if (numeric >= 4.5) return "strong buy";
    if (numeric >= 3.5) return "buy";
    if (numeric >= 2.5) return "hold";
    if (numeric >= 1.5) return "sell";
    return "strong sell";
  }

  function gradeFromNumeric(value) {
    var numeric = toNumber(value);
    if (numeric == null) return "";
    var rounded = Math.round(numeric);
    return GRADE_MAP[rounded] || "";
  }

  function latestHistoryEntry(payload) {
    var data = payload && payload.data;
    if (!Array.isArray(data)) return null;
    for (var i = 0; i < data.length; i++) {
      var item = data[i];
      var attrs = item && item.attributes;
      var ratings = attrs && attrs.ratings;
      if (!ratings || typeof ratings !== "object") continue;
      if (ratings.quantRating == null && ratings.sellSideRating == null && ratings.authorsRating == null) {
        continue;
      }
      return item;
    }
    return null;
  }

  function historyTickerId(payload) {
    var entry = latestHistoryEntry(payload);
    var attrs = entry && entry.attributes;
    return attrs && attrs.tickerId != null ? attrs.tickerId : null;
  }

  function clonePayload(value) {
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (err) {
      return null;
    }
  }

  function textOf(selectors, root) {
    var scope = root || document;
    for (var i = 0; i < selectors.length; i++) {
      var el = scope.querySelector(selectors[i]);
      if (!el) continue;
      var text = cleanText(el.textContent || el.getAttribute("content") || "");
      if (text) return text;
    }
    return "";
  }

  function hrefOf(selectors, root) {
    var scope = root || document;
    for (var i = 0; i < selectors.length; i++) {
      var el = scope.querySelector(selectors[i]);
      if (!el) continue;
      var href = cleanText(el.getAttribute("href") || el.getAttribute("content") || "");
      if (href) return normalizeHref(href, window.location.href);
    }
    return "";
  }

  function metaContent(attribute, name) {
    var selector = "meta[" + attribute + "=\"" + name.replace(/"/g, '\\"') + "\"]";
    var el = document.querySelector(selector);
    return cleanText(el && el.getAttribute("content"));
  }

  function normalizePathname(href) {
    try {
      var url = new URL(href, window.location.href);
      var path = cleanText(url.pathname).toLowerCase();
      if (!path) return "/";
      path = path.replace(/\/{2,}/g, "/");
      if (path.length > 1) path = path.replace(/\/+$/, "");
      return path || "/";
    } catch (err) {
      return "";
    }
  }

  function resolvePageUrl() {
    return cleanText(
      hrefOf(['link[rel="canonical"]'], document) ||
      metaContent("property", "og:url") ||
      metaContent("name", "twitter:url") ||
      normalizeHref(window.location.href)
    );
  }

  function classifySaPath(path) {
    if (/^\/symbol\/[^/]+/.test(path)) return "symbol";
    if (/^\/article\/[^/]+/.test(path)) return "article";
    if (/^\/news\/[^/]+/.test(path)) return "news";
    return "";
  }

  function isHubPagePath(path) {
    return /^\/market-news(?:\/|$)/.test(path) || path === "/news" || path === "/latest-news";
  }

  function isArticleRootElement(el) {
    return !!(
      el &&
      typeof el.matches === "function" &&
      (
        el.matches('[data-test-id="article-content"]') ||
        el.matches('[itemprop="articleBody"]') ||
        el.matches("article") ||
        el.matches(".paywall-full-content") ||
        el.matches("#content-area") ||
        el.matches(".main-content")
      )
    );
  }

  function findArticleRoot(root, allowLayoutFallback) {
    var scope = root || document;
    if (scope !== document && isArticleRootElement(scope)) return scope;
    var articleRoot = (
      scope.querySelector('[data-test-id="article-content"]') ||
      scope.querySelector('[itemprop="articleBody"]') ||
      scope.querySelector("article") ||
      scope.querySelector(".paywall-full-content")
    );
    if (articleRoot) return articleRoot;
    if (allowLayoutFallback) {
      return scope.querySelector("#content-area") || scope.querySelector(".main-content");
    }
    return null;
  }

  function isLikelyTickerSymbol(value) {
    var clean = cleanText(value).replace(/[^A-Z0-9.=\-]/g, "");
    if (!clean) return false;
    if (!/^[A-Z][A-Z0-9.=\-]{0,9}$/.test(clean)) return false;
    if (clean.length > 6 && clean.indexOf(".") === -1 && clean.indexOf("=") === -1 && clean.indexOf("-") === -1) {
      return false;
    }
    return !/^(NEWS|ETF|CFA|USD|EUR)$/i.test(clean);
  }

  function pushTicker(out, seen, symbol, maxCount) {
    var clean = cleanText(symbol).replace(/[^A-Z0-9.=\-]/g, "");
    if (!isLikelyTickerSymbol(clean) || seen[clean]) return;
    seen[clean] = true;
    out.push(clean);
    return out.length >= (maxCount || 8);
  }

  function collectTickersFromKeywords(out, seen, maxCount) {
    var keywordSources = [
      metaContent("name", "keywords"),
      metaContent("property", "article:tag"),
      metaContent("name", "news_keywords")
    ];
    keywordSources.forEach(function (raw) {
      cleanText(raw).split(",").forEach(function (part) {
        var matches = cleanText(part).match(/\b[A-Z][A-Z0-9.=\-]{0,9}\b/g) || [];
        matches.forEach(function (candidate) {
          pushTicker(out, seen, candidate, maxCount);
        });
      });
    });
  }

  function extractContentText(articleEl) {
    if (!articleEl) return "";
    var clone = articleEl.cloneNode(true);
    clone.querySelectorAll("script, style, iframe, img, video, figure, .ad-container, [data-ad]").forEach(function (el) {
      el.remove();
    });
    return cleanText(clone.textContent).slice(0, MAX_INTEL_CONTENT_CHARS);
  }

  function resolveExpandedNewsStoryUrl(root) {
    var scope = root || document;
    var direct = hrefOf([
      '[data-test-id="post-title"] a[href*="/news/"]',
      'h1 a[href*="/news/"]',
      'h2 a[href*="/news/"]',
      'a[href*="/news/"][aria-current="page"]'
    ], scope);
    if (classifySaPath(normalizePathname(direct)) === "news") return direct;

    var links = scope.querySelectorAll('a[href*="/news/"]');
    for (var i = 0; i < links.length; i++) {
      var href = normalizeHref(links[i].getAttribute("href"), window.location.href);
      if (classifySaPath(normalizePathname(href)) !== "news") continue;
      var label = cleanText(links[i].textContent);
      if (label.length >= 20 || i === 0) return href;
    }
    return "";
  }

  function extractExpandedNewsTitle(root) {
    var scope = root || document;
    var title = cleanText(
      textOf([
        '[data-test-id="post-title"]',
        'h1[data-test-id="post-title"]',
        "h1",
        "h2"
      ], scope)
    );
    if (title && title.toLowerCase() !== "news") return title;
    var link = scope.querySelector('a[href*="/news/"]');
    return cleanText(link && link.textContent);
  }

  function findExpandedNewsRoot() {
    var best = null;
    var bestScore = 0;
    var selectors = '[data-test-id="article-content"], [itemprop="articleBody"], .paywall-full-content, article';
    document.querySelectorAll(selectors).forEach(function (el) {
      if (!isVisibleElement(el)) return;
      var storyUrl = resolveExpandedNewsStoryUrl(el);
      if (classifySaPath(normalizePathname(storyUrl)) !== "news") return;
      var textLength = cleanText(el.textContent).length;
      if (textLength < 280) return;
      var score = textLength;
      if (el.matches('[data-test-id="article-content"], [itemprop="articleBody"], .paywall-full-content')) {
        score += 2000;
      }
      if (score > bestScore) {
        best = el;
        bestScore = score;
      }
    });
    return best;
  }

  function bodyText(root) {
    var scope = root || document;
    var body = scope.body || scope.documentElement || scope;
    return cleanText((body && (body.innerText || body.textContent)) || "");
  }

  function detectPageType() {
    var path = normalizePathname(resolvePageUrl() || window.location.href);
    var pageType = classifySaPath(path);
    if (pageType) return pageType;
    if (isHubPagePath(path) && findExpandedNewsRoot()) return "news";
    return "";
  }

  function shouldWatchIntelTransitions() {
    if (isIntelPageType(detectPageType())) return true;
    return isHubPagePath(normalizePathname(window.location.href));
  }

  function isIntelPageType(pageType) {
    return pageType === "article" || pageType === "news";
  }

  function extractPageTickers(root, url, pageType) {
    var scope = root || document;
    var seen = {};
    var out = [];
    var maxTickers = 8;
    if (pageType === "symbol") {
      var symbol = symbolFromHref(url || window.location.href);
      return symbol ? [symbol] : [];
    }
    if (scope === document) {
      collectTickersFromKeywords(out, seen, maxTickers);
    }

    var articleRoot = findArticleRoot(scope, true) || scope.querySelector("main") || scope;
    articleRoot.querySelectorAll(
      'a[href*="/symbol/"], span[data-test-id="ticker-symbol"], .ticker-hover, [data-test-id*="ticker"]'
    ).forEach(function (el) {
      if (out.length >= maxTickers) return;
      var hrefSymbol = symbolFromHref(el.href || "");
      var labelSymbol = cleanText(el.textContent).replace(/[^A-Z0-9.=\-]/g, "");
      pushTicker(out, seen, hrefSymbol || labelSymbol, maxTickers);
    });
    return out;
  }

  function parseJsonLdNodes() {
    var items = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach(function (el) {
      var text = cleanText(el.textContent || "");
      if (!text) return;
      try {
        var parsed = JSON.parse(text);
        if (Array.isArray(parsed)) {
          parsed.forEach(function (item) {
            if (item && typeof item === "object") items.push(item);
          });
        } else if (parsed && typeof parsed === "object") {
          items.push(parsed);
        }
      } catch (err) {
        // ignore invalid JSON-LD
      }
    });
    return items;
  }

  function firstJsonLdValue(keys) {
    var nodes = parseJsonLdNodes();
    for (var i = 0; i < nodes.length; i++) {
      for (var j = 0; j < keys.length; j++) {
        var value = nodes[i] && nodes[i][keys[j]];
        if (Array.isArray(value)) value = value[0];
        if (value && typeof value === "object" && value.name) value = value.name;
        value = cleanText(value);
        if (value) return value;
      }
    }
    return "";
  }

  function extractArticleContent(root) {
    var scope = root || document;
    var articleEl = findArticleRoot(scope, true);
    if (!articleEl && scope !== document && scope.nodeType === 1) {
      articleEl = scope;
    }
    if (!articleEl) {
      return bodyText(scope).slice(0, MAX_INTEL_CONTENT_CHARS);
    }
    return extractContentText(articleEl);
  }

  function buildIntelPagePayload() {
    var pageUrl = resolvePageUrl();
    var currentPath = normalizePathname(window.location.href);
    var pathType = classifySaPath(normalizePathname(pageUrl || window.location.href));
    var expandedNewsRoot = null;
    var pageType = detectPageType();
    if (!pathType && isHubPagePath(currentPath)) {
      expandedNewsRoot = findExpandedNewsRoot();
      if (expandedNewsRoot) {
        pageType = "news";
        pageUrl = resolveExpandedNewsStoryUrl(expandedNewsRoot) || pageUrl;
      }
    }
    if (!isIntelPageType(pageType)) return null;

    var canonicalUrl = pageUrl;
    if (!expandedNewsRoot && !isIntelPageType(classifySaPath(normalizePathname(canonicalUrl || window.location.href)))) {
      return null;
    }
    var title = cleanText(
      textOf(['h1[data-test-id="post-title"]', '[data-test-id="post-title"]', "h1"], expandedNewsRoot || document) ||
      metaContent("property", "og:title") ||
      firstJsonLdValue(["headline", "name"]) ||
      document.title
    );
    if ((!title || title.toLowerCase() === "news") && expandedNewsRoot) {
      title = extractExpandedNewsTitle(expandedNewsRoot);
    }
    var author = cleanText(
      textOf([
        '[data-test-id="post-author"] a',
        '[data-test-id="author-name"]',
        '.author-name',
        '[rel="author"]',
        '[itemprop="author"]'
      ], expandedNewsRoot || document) ||
      metaContent("name", "author") ||
      firstJsonLdValue(["author", "creator"])
    );
    var content = extractArticleContent(expandedNewsRoot || document);
    var description = cleanText(
      metaContent("name", "description") ||
      metaContent("property", "og:description") ||
      firstJsonLdValue(["description"])
    );
    var tickers = extractPageTickers(expandedNewsRoot || document, canonicalUrl || window.location.href, pageType);
    var publishedAt = cleanText(
      textOf(["time[datetime]"], expandedNewsRoot || document) ||
      metaContent("property", "article:published_time") ||
      metaContent("name", "article:published_time") ||
      firstJsonLdValue(["datePublished"])
    );
    var modifiedAt = cleanText(
      metaContent("property", "article:modified_time") ||
      metaContent("name", "article:modified_time") ||
      firstJsonLdValue(["dateModified"])
    );
    var sectionName = cleanText(
      metaContent("property", "article:section") ||
      metaContent("name", "article:section")
    );
    var keywords = cleanText(metaContent("name", "keywords"))
      .split(",")
      .map(function (part) { return cleanText(part); })
      .filter(Boolean)
      .slice(0, 20);
    var contentForIntel = content || description;
    if (!title || !contentForIntel) return null;

    return {
      page_type: pageType,
      url: canonicalUrl || normalizeHref(window.location.href),
      canonical_url: canonicalUrl || normalizeHref(window.location.href),
      title: title,
      author: author,
      ticker: tickers[0] || "",
      tickers: tickers,
      content: contentForIntel,
      summary: description,
      description: description,
      published_at: publishedAt,
      modified_at: modifiedAt,
      captured_at: new Date().toISOString(),
      bookmarklet_version: debugVersion(),
      source: CAPTURE_SOURCE,
      source_ref: canonicalUrl || normalizeHref(window.location.href),
      raw_fields: {
        source: CAPTURE_SOURCE,
        extension_version: EXT_VERSION,
        canonical_url: canonicalUrl || normalizeHref(window.location.href),
        page_url: normalizeHref(window.location.href),
        published_at: publishedAt,
        modified_at: modifiedAt,
        section: sectionName,
        keywords: keywords,
        content_length: contentForIntel.length
      }
    };
  }

  function isVisibleElement(el) {
    if (!el || typeof el.getBoundingClientRect !== "function") return false;
    if (el.hidden) return false;
    var style = window.getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    var rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function isInterestingPayload(payload, responseUrl, frameUrl) {
    if (!payload || typeof payload !== "object") return false;
    var responseHref = cleanText(responseUrl).toLowerCase();
    if (!/^https:\/\/seekingalpha\.com\/api\/v3\//.test(responseHref)) return false;
    if (latestHistoryEntry(payload)) return true;
    if (/\/symbols\/[^/]+\/relative_rankings\b/.test(responseHref)) return true;
    if (/\/symbols\/[^/]+\/sector_metrics\b/.test(responseHref)) return true;
    if (/\/ticker_metric_grades\b/.test(responseHref)) return true;
    if (/\/symbol_data\/estimates\b/.test(responseHref)) return true;
    if (/\/historical_prices\b/.test(responseHref)) return true;
    if (/\/metrics\b/.test(responseHref)) return true;
    if (/\/symbols\/[^/]+\/shares\b/.test(responseHref)) return true;
    return false;
  }

  function queryFields(responseUrl) {
    try {
      var url = new URL(responseUrl, window.location.href);
      var fields = [];
      var commaSeparated = url.searchParams.get("filter[fields]");
      if (commaSeparated) {
        commaSeparated.split(",").forEach(function (field) {
          var clean = cleanText(field);
          if (clean) fields.push(clean);
        });
      }
      url.searchParams.getAll("filter[fields][]").forEach(function (field) {
        var clean = cleanText(field);
        if (clean) fields.push(clean);
      });
      return fields;
    } catch (err) {
      return [];
    }
  }

  function classifySection(responseUrl, frameUrl, payload) {
    var url = cleanText(responseUrl || frameUrl || "").toLowerCase();
    if (latestHistoryEntry(payload)) return "ratings_history";
    if (url.indexOf("/relative_rankings") !== -1) return "relative_rankings";
    if (url.indexOf("/sector_metrics") !== -1) return "sector_metrics";
    if (url.indexOf("/ticker_metric_grades") !== -1) return "metric_grades";
    if (url.indexOf("/symbol_data/estimates") !== -1) return "earnings_estimates";
    if (url.indexOf("/historical_prices") !== -1) return "price_history";
    if (/\/symbols\/[^/]+\/shares\b/.test(url)) return "ownership";
    if (url.indexOf("/metrics") !== -1) {
      var fields = queryFields(responseUrl);
      if (fields.length === 1 && fields[0] === "primary_price") return "price";
      if (fields.length && fields.every(function (field) { return /_avg_5y$/i.test(field); })) {
        return "valuation_averages_5y";
      }
      if (fields.some(function (field) { return CAPITAL_STRUCTURE_FIELDS.indexOf(field) !== -1; })) {
        return "capital_structure";
      }
      return "valuation_metrics";
    }
    return "symbol";
  }

  function metricTypeFieldMap(payload) {
    var mapping = {};
    if (!payload || !Array.isArray(payload.included)) return mapping;
    payload.included.forEach(function (item) {
      if (!item || item.type !== "metric_type") return;
      var id = cleanText(item.id);
      var attrs = item.attributes || {};
      var field = cleanText(attrs.field);
      if (id && field) mapping[id] = field;
    });
    return mapping;
  }

  function extractMetricValues(payload) {
    var values = {};
    if (Array.isArray(payload)) {
      payload.forEach(function (item) {
        if (!item || typeof item !== "object") return;
        Object.keys(item).forEach(function (key) {
          if (key === "slug" || key === "tickerId") return;
          if (item[key] != null) values[key] = item[key];
        });
      });
      return values;
    }
    if (!payload || !Array.isArray(payload.data)) return values;
    var typeMap = metricTypeFieldMap(payload);
    payload.data.forEach(function (item) {
      var attrs = item && item.attributes;
      var metricRef = item && item.relationships && item.relationships.metric_type && item.relationships.metric_type.data;
      var field = metricRef && metricRef.id != null ? typeMap[String(metricRef.id)] : "";
      if (!field || !attrs || attrs.value == null) return;
      values[field] = attrs.value;
    });
    return values;
  }

  function extractRelativeRankings(payload) {
    var data = payload && payload.data;
    var attrs = data && data.attributes;
    return attrs && typeof attrs === "object" ? attrs : {};
  }

  function extractSummaryPatch(payload, responseUrl) {
    var entry = latestHistoryEntry(payload);
    if (!entry) return {};

    var attrs = entry.attributes || {};
    var ratings = attrs.ratings || {};
    return {
      quant_score: toNumber(ratings.quantRating),
      rating: ratingFromScore(ratings.quantRating),
      author_rating: ratingFromScore(ratings.authorsRating),
      wall_st_rating: ratingFromScore(ratings.sellSideRating),
      grades: {
        value: gradeFromNumeric(ratings.valueGrade),
        growth: gradeFromNumeric(ratings.growthGrade),
        profitability: gradeFromNumeric(ratings.profitabilityGrade),
        momentum: gradeFromNumeric(ratings.momentumGrade),
        revisions: gradeFromNumeric(
          ratings.epsRevisionsGrade != null ? ratings.epsRevisionsGrade : ratings.revisionsGrade
        )
      },
      raw_fields: {
        response_url: cleanText(responseUrl || ""),
        as_date: cleanText(attrs.asDate || ""),
        ticker_id: attrs.tickerId != null ? attrs.tickerId : null,
        sa_history_id: cleanText(entry.id || "")
      }
    };
  }

  function extractNonRatingSummaryPatch(payload, responseUrl) {
    var section = classifySection(responseUrl, window.location.href, payload);
    if (section === "relative_rankings") {
      var rankings = extractRelativeRankings(payload);
      return {
        sector_rank: toNumber(rankings.sectorRank),
        industry_rank: toNumber(rankings.industryRank),
        raw_fields: {
          overall_rank: toNumber(rankings.overallRank),
          sector_name: cleanText(rankings.sectorName || ""),
          industry_name: cleanText(rankings.primaryName || "")
        }
      };
    }
    if (section === "price") {
      var metrics = extractMetricValues(payload);
      return {
        raw_fields: {
          primary_price: metrics.primary_price != null ? metrics.primary_price : null
        }
      };
    }
    return {};
  }

  function createInitialSummary(symbol) {
    var pageUrl = canonicalSymbolUrl(symbol);
    return {
      ticker: symbol,
      url: pageUrl,
      title: cleanText(document.title),
      page_type: "symbol",
      captured_at: new Date().toISOString(),
      rating: "",
      quant_score: null,
      author_rating: "",
      wall_st_rating: "",
      grades: {},
      bookmarklet_version: debugVersion(),
      source: CAPTURE_SOURCE,
      source_ref: pageUrl,
      raw_fields: {
        source: CAPTURE_SOURCE,
        extension_version: EXT_VERSION
      }
    };
  }

  function sendDebugPing(stage, extra) {
    if (!endpoint) return;
    emitToExtension("debug_ping", {
      stage: stage || "",
      version: debugVersion(),
      href: normalizeHref(window.location.href),
      host: window.location.host || "",
      page_type: cleanText((extra && extra.page_type) || detectPageType() || "unknown"),
      extra: extra || {}
    }).catch(function () {
      // ignore debug ping errors
    });
  }

  function ensureCaptureState() {
    if (!isTopFrame()) return null;
    if (captureState) return captureState;
    var symbol = symbolFromLocation();
    if (!symbol) return null;
    var pageUrl = normalizeHref(window.location.href);
    captureState = {
      symbol: symbol,
      pageUrl: pageUrl,
      started: false,
      posted: false,
      postedFingerprint: "",
      pendingRoutes: 0,
      rawResponses: [],
      rawResponseKeys: {},
      sections: {},
      routeDebug: [{ source: "current_page", url: normalizeHref(window.location.href) }],
      summary: createInitialSummary(symbol),
      flushTimer: 0
    };
    syncDebugFields(captureState);
    return captureState;
  }

  function mergeGrades(target, source) {
    if (!source || typeof source !== "object") return;
    Object.keys(source).forEach(function (key) {
      if (source[key]) {
        target[key] = source[key];
      }
    });
  }

  function mergeRawFields(target, source) {
    if (!source || typeof source !== "object") return;
    Object.keys(source).forEach(function (key) {
      var value = source[key];
      if (value == null || value === "") return;
      target[key] = value;
    });
  }

  function syncDebugFields(state) {
    state.summary.scan_debug = {
      requested_routes: state.routeDebug.slice(),
      section_names: Object.keys(state.sections).sort(),
      raw_response_count: state.rawResponses.length
    };
  }

  function ensureSection(state, name) {
    if (!state.sections[name]) {
      state.sections[name] = {
        response_count: 0,
        response_urls: [],
        routes: []
      };
    }
    return state.sections[name];
  }

  function recordKey(record) {
    var historyId = "";
    var firstItem = record && record.payload && Array.isArray(record.payload.data) ? record.payload.data[0] : null;
    if (firstItem && firstItem.id) {
      historyId = cleanText(firstItem.id);
    }
    return [record.section, record.response_url, historyId].join("|");
  }

  function consumeRecord(record) {
    var state = ensureCaptureState();
    if (!state || !record) return;
    if (record.ticker && record.ticker !== state.symbol) return;
    var key = recordKey(record);
    if (state.rawResponseKeys[key]) return;
    state.rawResponseKeys[key] = true;
    state.posted = false;

    if (state.rawResponses.length < MAX_RAW_RESPONSES) {
      state.rawResponses.push(record);
    }

    var section = ensureSection(state, record.section);
    section.response_count += 1;
    if (record.response_url && section.response_urls.indexOf(record.response_url) === -1) {
      section.response_urls.push(record.response_url);
    }
    if (record.route && section.routes.indexOf(record.route) === -1) {
      section.routes.push(record.route);
    }

    var patch = extractSummaryPatch(record.payload, record.response_url);
    if (!Object.keys(patch).length) {
      patch = extractNonRatingSummaryPatch(record.payload, record.response_url);
    }
    if (patch.quant_score != null) state.summary.quant_score = patch.quant_score;
    if (patch.rating) state.summary.rating = patch.rating;
    if (patch.author_rating) state.summary.author_rating = patch.author_rating;
    if (patch.wall_st_rating) state.summary.wall_st_rating = patch.wall_st_rating;
    if (patch.sector_rank != null) state.summary.sector_rank = patch.sector_rank;
    if (patch.industry_rank != null) state.summary.industry_rank = patch.industry_rank;
    mergeGrades(state.summary.grades, patch.grades);
    mergeRawFields(state.summary.raw_fields, patch.raw_fields);
    syncDebugFields(state);
    scheduleFlush();
  }

  function buildRecordFromPayload(payload, responseUrl, frameUrl, sectionHint, routeHint) {
    if (!isInterestingPayload(payload, responseUrl, frameUrl)) return null;
    var frameHref = normalizeHref(frameUrl || window.location.href);
    var responseHref = normalizeHref(responseUrl || frameHref, frameHref);
    var ticker = symbolFromHref(frameHref) || symbolFromHref(responseHref) || symbolFromLocation();
    if (!ticker) return null;

    var serializable = clonePayload(payload);
    if (serializable == null) return null;

    return {
      ticker: ticker,
      section: sectionHint || classifySection(responseHref, frameHref, serializable),
      response_url: responseHref,
      frame_url: frameHref,
      route: normalizeHref(routeHint || frameHref),
      captured_at: new Date().toISOString(),
      payload: serializable
    };
  }

  function publishRecord(record) {
    if (!record) return;
    if (isTopFrame()) {
      consumeRecord(record);
      return;
    }
    try {
      window.top.postMessage({ __brc_sa_record: true, record: record }, window.location.origin);
    } catch (err) {
      try {
        window.top.postMessage({ __brc_sa_record: true, record: record }, "*");
      } catch (innerErr) {
        // ignore messaging errors
      }
    }
  }

  function inspectTextBody(text, responseUrl) {
    if (!text || text.length > 1_000_000) return;
    var parsed;
    try {
      parsed = JSON.parse(text);
    } catch (err) {
      return;
    }
    publishRecord(buildRecordFromPayload(parsed, responseUrl, window.location.href));
  }

  function hasSummarySignal(summary) {
    if (!summary || typeof summary !== "object") return false;
    if (summary.quant_score != null || summary.rating) return true;
    return !!(summary.grades && Object.keys(summary.grades).length);
  }

  function buildCapturePayload() {
    var state = ensureCaptureState();
    if (!state) return null;
    syncDebugFields(state);

    var summary = clonePayload(state.summary) || {};
    summary.raw_fields = summary.raw_fields || {};
    summary.raw_fields.source = CAPTURE_SOURCE;
    summary.raw_fields.extension_version = EXT_VERSION;
    summary.raw_fields.section_names = Object.keys(state.sections).sort();
    summary.raw_fields.raw_response_count = state.rawResponses.length;

    return {
      ticker: state.symbol,
      url: state.pageUrl,
      title: summary.title || cleanText(document.title),
      page_type: "symbol",
      captured_at: new Date().toISOString(),
      bookmarklet_version: summary.bookmarklet_version,
      source: CAPTURE_SOURCE,
      source_ref: state.pageUrl,
      rating: summary.rating || "",
      quant_score: summary.quant_score,
      author_rating: summary.author_rating || "",
      wall_st_rating: summary.wall_st_rating || "",
      grades: summary.grades || {},
      summary: summary,
      sections: clonePayload(state.sections) || {},
      raw_responses: clonePayload(state.rawResponses) || []
    };
  }

  function postSnapshot(payload) {
    if (!endpoint) return Promise.reject(new Error("BRC endpoint not configured"));
    return emitToExtension("post_snapshot", payload);
  }

  function postPageCapture(payload) {
    if (!endpoint) return Promise.reject(new Error("BRC endpoint not configured"));
    return emitToExtension("post_page_capture", payload);
  }

  function flushSnapshot() {
    var state = ensureCaptureState();
    if (!state || state.pendingRoutes > 0 || !endpoint) return;
    var payload = buildCapturePayload();
    if (!payload) return;
    if (!payload.raw_responses.length && !hasSummarySignal(payload.summary)) return;
    var fingerprint = JSON.stringify({
      raw_count: payload.raw_responses.length,
      sections: Object.keys(payload.sections).sort(),
      quant_score: payload.summary && payload.summary.quant_score,
      rating: payload.summary && payload.summary.rating
    });
    if (state.posted && state.postedFingerprint === fingerprint) return;

    state.posted = true;
    state.postedFingerprint = fingerprint;
    console.log("[BRC SA] posting snapshot", payload.ticker, Object.keys(payload.sections).length, payload.raw_responses.length, debugVersion());
    sendDebugPing("pre_post", {
      sections: Object.keys(payload.sections).length,
      raw_count: payload.raw_responses.length
    });
    postSnapshot(payload).then(function () {
      console.log("[BRC SA] symbol snapshot captured", payload.ticker, Object.keys(payload.sections).length);
      sendDebugPing("post_ok", {
        sections: Object.keys(payload.sections).length,
        raw_count: payload.raw_responses.length
      });
    }).catch(function (err) {
      state.posted = false;
      console.warn("[BRC SA] symbol snapshot failed", err && err.message ? err.message : err);
      sendDebugPing("post_fail", {
        message: err && err.message ? err.message : String(err || "")
      });
    });
  }

  function scheduleFlush() {
    var state = ensureCaptureState();
    if (!state) return;
    if (state.flushTimer) {
      clearTimeout(state.flushTimer);
    }
    state.flushTimer = setTimeout(function () {
      state.flushTimer = 0;
      flushSnapshot();
    }, FLUSH_IDLE_MS);
  }

  function buildIntelFingerprint(payload) {
    return JSON.stringify({
      page_type: payload.page_type,
      url: payload.url,
      title: payload.title,
      tickers: payload.tickers || [],
      content_length: (payload.content || "").length,
      published_at: payload.published_at || ""
    });
  }

  function flushIntelCapture() {
    if (!endpoint) return;
    var payload = buildIntelPagePayload();
    if (!payload) return;
    var fingerprint = buildIntelFingerprint(payload);
    if (intelCaptureSeen[fingerprint]) return;
    if (intelCaptureInFlight) {
      scheduleIntelCapture(700);
      return;
    }

    intelCaptureInFlight = true;
    intelCaptureFingerprint = fingerprint;
    console.log("[BRC SA] posting page capture", payload.page_type, payload.ticker || payload.title, debugVersion());
    sendDebugPing("pre_post", {
      page_type: payload.page_type,
      content_length: (payload.content || "").length,
      tickers: (payload.tickers || []).join(",")
    });
    postPageCapture(payload).then(function () {
      intelCaptureSeen[fingerprint] = true;
      intelCaptureInFlight = false;
      console.log("[BRC SA] page capture stored", payload.page_type, payload.ticker || payload.title);
      sendDebugPing("post_ok", {
        page_type: payload.page_type,
        tickers: (payload.tickers || []).join(",")
      });
      if (shouldWatchIntelTransitions()) {
        scheduleIntelCapture(600);
      }
    }).catch(function (err) {
      intelCaptureInFlight = false;
      console.warn("[BRC SA] page capture failed", err && err.message ? err.message : err);
      sendDebugPing("post_fail", {
        page_type: payload.page_type,
        message: err && err.message ? err.message : String(err || "")
      });
      if (shouldWatchIntelTransitions()) {
        scheduleIntelCapture(1200);
      }
    });
  }

  function scheduleIntelCapture(delayMs) {
    if (!isTopFrame()) return;
    if (intelCaptureTimer) clearTimeout(intelCaptureTimer);
    intelCaptureTimer = setTimeout(function () {
      intelCaptureTimer = 0;
      flushIntelCapture();
    }, delayMs || INTEL_CAPTURE_DELAY_MS);
  }

  function startIntelCapture() {
    var keepWatchingHubPage = isHubPagePath(normalizePathname(window.location.href));
    if (intelCaptureStarted || (!keepWatchingHubPage && !isIntelPageType(detectPageType()))) return;
    intelCaptureStarted = true;
    scheduleIntelCapture(INTEL_CAPTURE_DELAY_MS);
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        scheduleIntelCapture(INTEL_CAPTURE_DELAY_MS);
      }, { once: true });
    }
    window.addEventListener("load", function () {
      scheduleIntelCapture(INTEL_RETRY_DELAY_MS);
    }, { once: true });
    if (document.documentElement && typeof MutationObserver !== "undefined") {
      intelObserver = new MutationObserver(function () {
        if (intelCaptureInFlight) return;
        scheduleIntelCapture(900);
      });
      intelObserver.observe(document.documentElement, {
        childList: true,
        subtree: true,
        characterData: true
      });
      setTimeout(function () {
        if (intelObserver) {
          intelObserver.disconnect();
          intelObserver = null;
        }
      }, keepWatchingHubPage ? HUB_INTEL_WATCH_TIMEOUT_MS : 12000);
    }
  }

  function startCaptureForCurrentPage() {
    var pageType = detectPageType();
    if (pageType === "symbol") {
      startCapture();
      return;
    }
    if (isIntelPageType(pageType) || shouldWatchIntelTransitions()) {
      startIntelCapture();
    }
  }

  function applyEndpoint(nextEndpoint) {
    var cleanEndpoint = cleanText(nextEndpoint);
    var changed = cleanEndpoint !== endpoint;
    endpoint = cleanEndpoint;
    if (!changed && endpointAnnounced) {
      return;
    }
    endpointAnnounced = true;
    console.log("[BRC SA] endpoint", endpoint ? "set" : "missing", debugVersion());
    if (endpoint) {
      sendDebugPing("endpoint_set");
    }
    scheduleFlush();
    scheduleIntelCapture(INTEL_CAPTURE_DELAY_MS);
    if (isTopFrame()) {
      setTimeout(startCaptureForCurrentPage, 400);
    }
  }

  function buildUrl(pathname, configure) {
    var url = new URL(pathname, window.location.origin);
    if (typeof configure === "function") configure(url.searchParams);
    return url.toString();
  }

  function buildDirectFetchPlan(symbol, tickerId) {
    var slug = lowerSlug(symbol);
    var routes = [
      {
        section: "relative_rankings",
        url: buildUrl("/api/v3/symbols/" + encodeURIComponent(slug) + "/relative_rankings"),
        via: "api_fetch"
      },
      {
        section: "price",
        url: buildUrl("/api/v3/metrics", function (params) {
          params.set("filter[fields]", "primary_price");
          params.set("filter[slugs]", slug);
          params.set("minified", "true");
        }),
        via: "api_fetch"
      },
      {
        section: "valuation_metrics",
        url: buildUrl("/api/v3/metrics", function (params) {
          params.set("filter[fields]", VALUATION_FIELDS.join(","));
          params.set("filter[slugs]", slug);
          params.set("minified", "false");
        }),
        via: "api_fetch"
      },
      {
        section: "capital_structure",
        url: buildUrl("/api/v3/metrics", function (params) {
          params.set("filter[fields]", CAPITAL_STRUCTURE_FIELDS.join(","));
          params.set("filter[slugs]", slug);
          params.set("minified", "false");
        }),
        via: "api_fetch"
      },
      {
        section: "valuation_averages_5y",
        url: buildUrl("/api/v3/metrics", function (params) {
          params.set("filter[fields]", VALUATION_AVERAGE_FIELDS.join(","));
          params.set("filter[slugs]", slug);
          params.set("minified", "false");
        }),
        via: "api_fetch"
      },
      {
        section: "metric_grades",
        url: buildUrl("/api/v3/ticker_metric_grades", function (params) {
          params.set("filter[fields]", VALUATION_FIELDS.join(","));
          params.set("filter[slugs]", slug);
          params.append("filter[algos][]", "main_quant");
          params.append("filter[algos][]", "dividends");
          params.set("minified", "false");
        }),
        via: "api_fetch"
      },
      {
        section: "sector_metrics",
        url: buildUrl("/api/v3/symbols/" + encodeURIComponent(slug) + "/sector_metrics", function (params) {
          VALUATION_FIELDS.forEach(function (field) {
            params.append("filter[fields][]", field);
          });
        }),
        via: "api_fetch"
      }
    ];

    if (tickerId != null) {
      routes.push({
        section: "earnings_estimates",
        url: buildUrl("/api/v3/symbol_data/estimates", function (params) {
          params.set("estimates_data_items", ESTIMATE_DATA_ITEMS.join(","));
          params.set("period_type", "annual");
          params.set("relative_periods", "0,1,2,3,4");
          params.set("ticker_ids", String(tickerId));
        }),
        via: "api_fetch"
      });
    }

    return routes;
  }

  function directHistoryRoute(symbol) {
    return {
      section: "ratings_history",
      url: buildUrl("/api/v3/symbols/" + encodeURIComponent(symbol) + "/rating/periods", function (params) {
        params.append("filter[periods][]", "0");
        params.append("filter[periods][]", "3");
        params.append("filter[periods][]", "6");
      }),
      via: "api_fetch"
    };
  }

  function fetchApiRoute(route) {
    var state = ensureCaptureState();
    if (!state || typeof nativeFetch !== "function") {
      return Promise.resolve(null);
    }
    appendRouteDebug(route, "queued");
    return Promise.race([
      nativeFetch.call(window, route.url, {
        credentials: "include",
        headers: { Accept: "application/json" }
      }),
      new Promise(function (_, reject) {
        setTimeout(function () {
          reject(new Error("api fetch timeout"));
        }, API_FETCH_TIMEOUT_MS);
      })
    ]).then(function (response) {
      if (!response || !response.ok) {
        appendRouteDebug(route, response ? "http_" + response.status : "error");
        return null;
      }
      return response.text().then(function (text) {
        var parsed;
        try {
          parsed = JSON.parse(text);
        } catch (err) {
          appendRouteDebug(route, "invalid_json");
          return null;
        }
        var record = buildRecordFromPayload(parsed, route.url, state.pageUrl, route.section, state.pageUrl);
        if (record) {
          publishRecord(record);
          appendRouteDebug(route, "captured");
          return { route: route, payload: parsed, record: record };
        }
        appendRouteDebug(route, "ignored");
        return null;
      });
    }).catch(function () {
      appendRouteDebug(route, "error");
      return null;
    });
  }

  function needsFallbackTabScan(state) {
    if (!state) return false;
    var required = ["ratings_history", "relative_rankings", "valuation_metrics", "metric_grades", "sector_metrics"];
    for (var i = 0; i < required.length; i++) {
      if (!state.sections[required[i]]) return true;
    }
    return false;
  }

  function appendRouteDebug(route, status) {
    var state = ensureCaptureState();
    if (!state) return;
    state.routeDebug.push({
      url: route.url,
      section: route.section,
      via: route.via,
      status: status
    });
    syncDebugFields(state);
  }

  function tabSpecForLabel(label) {
    var clean = cleanText(label);
    if (!clean) return null;
    var best = null;
    for (var i = 0; i < TAB_LABEL_PATTERNS.length; i++) {
      var spec = TAB_LABEL_PATTERNS[i];
      if (!spec.pattern.test(clean)) continue;
      if (!best || spec.score > best.score) {
        best = { section: spec.section, score: spec.score };
      }
    }
    return best;
  }

  function collectVisibleTabs() {
    var seen = {};
    var items = [];
    var selector = [
      '[role=\"tab\"]',
      '[role=\"tablist\"] button',
      'button[aria-controls]',
      'button[data-test-id*=\"tab\"]',
      'button[class*=\"tab\"]'
    ].join(',');

    document.querySelectorAll(selector).forEach(function (el) {
      if (!isVisibleElement(el)) return;
      var label = cleanText(el.textContent || el.getAttribute('aria-label') || el.title || '');
      if (!label || label.length > 60) return;
      var spec = tabSpecForLabel(label);
      if (!spec) return;
      var key = label.toLowerCase();
      if (seen[key]) return;
      seen[key] = true;
      items.push({
        element: el,
        label: label,
        section: spec.section,
        score: spec.score
      });
    });

    items.sort(function (a, b) {
      return b.score - a.score || a.label.localeCompare(b.label);
    });
    return items.slice(0, 10);
  }

  function clickVisibleTab(item) {
    return new Promise(function (resolve) {
      try {
        item.element.scrollIntoView({ behavior: "instant", block: "center", inline: "center" });
      } catch (err) {
        // ignore scroll errors
      }
      try {
        item.element.click();
      } catch (err) {
        item.element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
      }
      setTimeout(resolve, TAB_WAIT_MS);
    });
  }

  async function startFallbackTabCapture() {
    var state = ensureCaptureState();
    if (!state || state.fallbackStarted) return;
    state.fallbackStarted = true;
    state.pendingRoutes += 1;
    var tabs = collectVisibleTabs();
    console.log("[BRC SA] tab scan", debugVersion(), tabs.map(function (t) { return t.label; }));
    sendDebugPing("tab_scan", { tabs: tabs.length });
    if (!tabs.length) {
      state.pendingRoutes = Math.max(0, state.pendingRoutes - 1);
      scheduleFlush();
      return;
    }

    for (var i = 0; i < tabs.length && i < 4; i++) {
      var route = {
        url: state.pageUrl,
        section: tabs[i].section,
        via: "visible_tab",
        label: tabs[i].label
      };
      appendRouteDebug(route, "queued");
      try {
        await clickVisibleTab(tabs[i]);
        appendRouteDebug(route, "clicked");
      } catch (err) {
        appendRouteDebug(route, "error");
      }
    }

    state.pendingRoutes = Math.max(0, state.pendingRoutes - 1);
    scheduleFlush();
  }

  async function startCapture() {
    var state = ensureCaptureState();
    if (!state || state.started) return;
    state.started = true;
    state.pendingRoutes += 1;

    var historyRoute = directHistoryRoute(state.symbol);
    appendRouteDebug(historyRoute, "seed");
    var historyResult = await fetchApiRoute(historyRoute);
    var tickerId = historyResult && historyTickerId(historyResult.payload);
    if (tickerId != null) {
      state.summary.raw_fields.ticker_id = tickerId;
    }

    var plan = buildDirectFetchPlan(state.symbol, tickerId);
    console.log("[BRC SA] api fetch plan", debugVersion(), plan.map(function (route) { return route.section; }));
    sendDebugPing("api_fetch_plan", { routes: plan.length });
    await Promise.all(plan.map(fetchApiRoute));

    state.pendingRoutes = Math.max(0, state.pendingRoutes - 1);
    scheduleFlush();

    if (needsFallbackTabScan(state)) {
      startFallbackTabCapture();
    }
  }

  document.documentElement.addEventListener("brc-sa-endpoint", function (event) {
    applyEndpoint(event && event.detail && event.detail.endpoint);
  });

  window.addEventListener("message", function (event) {
    if (event.source !== window) return;
    var data = event && event.data;
    if (!data || !data.__brc_sa_endpoint) return;
    applyEndpoint(data.endpoint);
  }, false);

  if (isTopFrame()) {
    console.log("[BRC SA] hook active", debugVersion(), normalizeHref(window.location.href));
    window.addEventListener("message", function (event) {
      if (event.origin && event.origin !== window.location.origin) return;
      var data = event.data;
      if (!data || !data.__brc_sa_record || !data.record) return;
      consumeRecord(data.record);
    }, false);

    ensureCaptureState();
    document.addEventListener("click", function () {
      if (!shouldWatchIntelTransitions()) return;
      scheduleIntelCapture(intelCaptureInFlight ? 900 : 500);
    }, true);
    setTimeout(startCaptureForCurrentPage, 900);
  }

  if (typeof nativeFetch === "function") {
    window.fetch = function () {
      return nativeFetch.apply(this, arguments).then(function (response) {
        try {
          response.clone().text().then(function (body) {
            inspectTextBody(body, response.url);
          }).catch(function () {});
        } catch (err) {
          // ignore clone/body errors
        }
        return response;
      });
    };
  }

  var originalOpen = XMLHttpRequest.prototype.open;
  var originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url) {
    this.__brcUrl = url;
    return originalOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function () {
    this.addEventListener("load", function () {
      try {
        inspectTextBody(this.responseText, this.responseURL || this.__brcUrl || "");
      } catch (err) {
        // ignore non-text responses
      }
    });
    return originalSend.apply(this, arguments);
  };
})();

/**
 * Seeking Alpha bookmarklet for BoxRoomCapital.
 *
 * Symbol pages on SA are multi-tab and often lazy-loaded. This bookmarklet:
 * 1. scrapes the current DOM and HTML
 * 2. expands likely overflow menus
 * 3. walks visible tab/button controls on symbol pages
 * 4. fetches same-origin symbol subroutes, including guessed routes
 * 5. merges the richest snapshot and posts scan_debug for inspection
 */
(function () {
  var ENDPOINT = window.__BRC_ENDPOINT || "%%ENDPOINT%%";
  var BOOKMARKLET_VERSION = "2026-03-06T14:44Z";
  var TAB_WAIT_MS = 1400;
  var MAX_BUTTON_TABS = 14;
  var MAX_ROUTE_TABS = 14;
  var FETCH_TIMEOUT_MS = 6000;
  var IFRAME_RENDER_WAIT_MS = 2200;
  var IFRAME_TIMEOUT_MS = 8000;
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
    "wall street",
    "earnings",
    "estimate"
  ];
  var GUESSED_SYMBOL_SUFFIXES = [
    "ratings/quant-ratings",
    "ratings/author-ratings",
    "ratings/sell-side-ratings",
    "earnings/revisions",
    "valuation/metrics"
  ];
  var ALLOWED_RATINGS = [
    "strong buy",
    "buy",
    "hold",
    "sell",
    "strong sell",
    "very bullish",
    "bullish",
    "neutral",
    "bearish",
    "very bearish"
  ];

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function cleanText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
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

  function bodyText(root) {
    if (!root || !root.body) return "";
    return cleanText(root.body.innerText || root.body.textContent || "");
  }

  function documentHtml(root) {
    if (!root || !root.documentElement) return "";
    return String(root.documentElement.outerHTML || "");
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

  function firstMatch(text, patterns) {
    var haystack = String(text || "");
    for (var i = 0; i < patterns.length; i++) {
      var match = haystack.match(patterns[i]);
      if (match && cleanText(match[1])) {
        return cleanText(match[1]);
      }
    }
    return "";
  }

  function firstNumber(text, patterns) {
    var haystack = String(text || "");
    for (var i = 0; i < patterns.length; i++) {
      var match = haystack.match(patterns[i]);
      if (!match) continue;
      var value = parseFloat(match[1]);
      if (!isNaN(value)) {
        return value;
      }
    }
    return null;
  }

  function validFiveScale(value) {
    return value != null && value > 0 && value <= 5.1;
  }

  function isSeekingAlphaHost(hostname) {
    var host = String(hostname || "").toLowerCase();
    return host === "seekingalpha.com" || /(?:^|\\.)seekingalpha\\.com$/.test(host);
  }

  function sendDebugPing(stage, extra) {
    try {
      var params = new URLSearchParams();
      params.set('stage', cleanText(stage || 'unknown'));
      params.set('v', BOOKMARKLET_VERSION);
      params.set('href', normalizeHref((extra && extra.href) || window.location.href));
      params.set('host', String(window.location.hostname || ''));
      params.set('page_type', cleanText((extra && extra.page_type) || ''));
      var img = new Image();
      img.referrerPolicy = 'no-referrer';
      img.src = ENDPOINT + '/api/webhooks/sa_debug_ping?' + params.toString() + '&_ts=' + Date.now();
    } catch (err) {
      return;
    }
  }

  function fetchWithTimeout(url, options, timeoutMs) {
    var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = null;
    var config = Object.assign({}, options || {});
    if (controller) config.signal = controller.signal;
    if (controller && timeoutMs > 0) {
      timer = setTimeout(function () { controller.abort(); }, timeoutMs);
    }
    return fetch(url, config).finally(function () {
      if (timer) clearTimeout(timer);
    });
  }

  function loadIframeTab(item, status) {
    return new Promise(function (resolve, reject) {
      var frame = document.createElement('iframe');
      var finished = false;
      var timer = null;

      function cleanup() {
        if (timer) clearTimeout(timer);
        if (frame && frame.parentNode) frame.parentNode.removeChild(frame);
      }

      function fail(err) {
        if (finished) return;
        finished = true;
        cleanup();
        reject(err instanceof Error ? err : new Error(String(err || 'iframe load failed')));
      }

      function succeed(data) {
        if (finished) return;
        finished = true;
        cleanup();
        resolve(data);
      }

      frame.style.cssText = 'position:fixed;left:-99999px;top:-99999px;width:1280px;height:2400px;opacity:0;pointer-events:none;';
      frame.setAttribute('aria-hidden', 'true');
      frame.referrerPolicy = 'no-referrer';

      timer = setTimeout(function () {
        fail(new Error('Timeout loading ' + item.href));
      }, IFRAME_TIMEOUT_MS);

      frame.onload = function () {
        Promise.resolve().then(async function () {
          status.textContent = 'Rendering route: ' + item.label;
          await sleep(IFRAME_RENDER_WAIT_MS);
          var doc = frame.contentDocument;
          if (!doc || !doc.documentElement) {
            throw new Error('Iframe document unavailable for ' + item.href);
          }
          succeed(snapshotFromDocument(doc, item.href, null, { kind: 'route', label: item.label }));
        }).catch(fail);
      };

      frame.onerror = function () {
        fail(new Error('Iframe load error for ' + item.href));
      };

      document.body.appendChild(frame);
      frame.src = item.href;
    });
  }

  function normalizeRatingValue(value) {
    var clean = cleanText(value).toLowerCase();
    if (!clean) return "";
    clean = clean.replace(/quant rating/gi, " ").replace(/rating/g, " ").replace(/recommendation/g, " ");
    clean = clean.replace(/\s+/g, " ").trim();
    for (var i = 0; i < ALLOWED_RATINGS.length; i++) {
      var allowed = ALLOWED_RATINGS[i];
      if (clean === allowed) return allowed;
      if (new RegExp("\\b" + allowed.replace(/\s+/g, "\\s+") + "\\b", "i").test(clean)) {
        return allowed;
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

  function uniqueList(values) {
    var out = [];
    var seen = {};
    (values || []).forEach(function (value) {
      var clean = cleanText(value).toUpperCase();
      if (!clean || seen[clean]) return;
      seen[clean] = true;
      out.push(clean);
    });
    return out;
  }

  function detectPageType(url) {
    if (/\/symbol\//i.test(url)) return "symbol";
    if (/\/article\//i.test(url)) return "article";
    return "article";
  }

  function currentSymbolPath() {
    var match = window.location.pathname.match(/\/symbol\/[^/?#]+/i);
    return match ? match[0] : "";
  }

  function primarySymbolFromUrl(url) {
    var match = String(url || "").match(/\/symbol\/([A-Z.=\-]+)/i);
    return match ? match[1].toUpperCase() : "";
  }

  function extractTickerList(root, url, pageType) {
    var primary = primarySymbolFromUrl(url);
    if (pageType === "symbol" && primary) {
      return [primary];
    }
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
    if (primary && !seen[primary]) out.unshift(primary);
    return out;
  }

  function classifyContext(url, label, kind) {
    var haystack = (cleanText(url) + " " + cleanText(label)).toLowerCase();
    var isBase = kind === "base";
    return {
      kind: kind || "base",
      // Base symbol pages are noisy. Only treat quant as explicit when the route itself is quant-related.
      quant: /quant/.test(haystack) || /\/ratings\/quant-ratings/.test(haystack),
      author: isBase || /author|sa analysts?/.test(haystack),
      sellSide: isBase || /sell-side|wall st|wall street|analysts?/.test(haystack),
      grades: isBase || /factor|grade|valuation|growth|profitability|momentum|revision|revisions|dividend/.test(haystack)
    };
  }

  function ratingFromFiveScale(score) {
    if (!validFiveScale(score)) return "";
    if (score >= 4.5) return "strong buy";
    if (score >= 3.5) return "buy";
    if (score >= 2.5) return "hold";
    if (score >= 1.5) return "sell";
    return "strong sell";
  }

  function extractRatingByPatterns(pageText, htmlText, patterns) {
    var textValue = normalizeRatingValue(firstMatch(pageText, patterns.text || []));
    if (textValue) return textValue;
    return normalizeRatingValue(firstMatch(htmlText, patterns.html || []));
  }

  function extractQuantRating(pageText, htmlText, flags) {
    if (!flags.quant) return "";
    return extractRatingByPatterns(pageText, htmlText, {
      text: [
        /Quant (?:Rating|Recommendation)(?:\s*[:\-]|\s{1,3})\s*(Strong Buy|Buy|Hold|Sell|Strong Sell|Very Bullish|Bullish|Neutral|Bearish|Very Bearish)\b/i,
        /Quant (?:Rating|Recommendation)[^A-Za-z]{0,20}(Strong Buy|Buy|Hold|Sell|Strong Sell|Very Bullish|Bullish|Neutral|Bearish|Very Bearish)\b/i
      ],
      html: [
        /(?:quantRatingLabel|quant_rating_label|quantRecommendation|quant_recommendation|quantRating|quant_rating)["'=:>\s\[{,]{0,40}"?(strong buy|buy|hold|sell|strong sell|very bullish|bullish|neutral|bearish|very bearish)\b/i,
        /Quant (?:Rating|Recommendation)(?:\s*[:\-]|\s{1,3})\s*(Strong Buy|Buy|Hold|Sell|Strong Sell|Very Bullish|Bullish|Neutral|Bearish|Very Bearish)\b/i
      ]
    });
  }

  function extractAuthorRating(pageText, htmlText, flags) {
    if (!flags.author) return "";
    return extractRatingByPatterns(pageText, htmlText, {
      text: [
        /SA Analysts?'? Rating[^A-Za-z]{0,24}(Strong Buy|Buy|Hold|Sell|Strong Sell|Very Bullish|Bullish|Neutral|Bearish|Very Bearish)/i,
        /Author(?:s)?'? Rating[^A-Za-z]{0,24}(Strong Buy|Buy|Hold|Sell|Strong Sell|Very Bullish|Bullish|Neutral|Bearish|Very Bearish)/i
      ],
      html: [
        /(?:authorRating|author_rating|saAuthorsRating|sa_authors_rating)["'=:>\s\[{,]{0,40}"?([^"'<\]}]{2,40})/i
      ]
    });
  }

  function extractWallStreetRating(pageText, htmlText, flags) {
    if (!flags.sellSide) return "";
    return extractRatingByPatterns(pageText, htmlText, {
      text: [
        /Wall St\.? Analysts?'? Rating[^A-Za-z]{0,24}(Strong Buy|Buy|Hold|Sell|Strong Sell|Very Bullish|Bullish|Neutral|Bearish|Very Bearish)/i,
        /Wall Street(?: Analysts?)? Rating[^A-Za-z]{0,24}(Strong Buy|Buy|Hold|Sell|Strong Sell|Very Bullish|Bullish|Neutral|Bearish|Very Bearish)/i,
        /Sell[- ]Side Rating[^A-Za-z]{0,24}(Strong Buy|Buy|Hold|Sell|Strong Sell|Very Bullish|Bullish|Neutral|Bearish|Very Bearish)/i
      ],
      html: [
        /(?:wallStreetRating|wall_st_rating|sellSideRating|sell_side_rating|analystRating|analyst_rating)["'=:>\s\[{,]{0,40}"?([^"'<\]}]{2,40})/i
      ]
    });
  }

  function extractQuantScore(root, pageText, htmlText, flags) {
    if (!flags.quant) return null;

    var directSelectors = [
      '[data-test-id="quant-score"]',
      '[data-test-id*="quant-score"]',
      '[data-test-id*="quantScore"]',
      '[data-test-id*="quant-rating-score"]',
      '.quant-score',
      '.quantScore',
      '[class*="quantScore"]'
    ];
    for (var i = 0; i < directSelectors.length; i++) {
      var els = (root || document).querySelectorAll(directSelectors[i]);
      for (var j = 0; j < els.length; j++) {
        var directText = cleanText(els[j].textContent);
        if (!directText) continue;
        if (/buy|hold|sell|bullish|bearish/i.test(directText) && !/\/\s*5|out of 5/i.test(directText)) continue;
        var value = firstNumber(directText, [
          /(\d\.\d{1,2})\s*(?:\/\s*5|out of 5)?/i,
          /(\d)\s*\/\s*5/i
        ]);
        if (validFiveScale(value)) return value;
      }
    }

    var textValue = firstNumber(pageText, [
      /Quant (?:Rating|Score)[^0-9]{0,16}(\d\.\d{1,2})\s*(?:\/\s*5|out of 5)?/i,
      /Quant (?:Rating|Score)[^0-9]{0,16}(\d)\s*\/\s*5/i
    ]);
    if (validFiveScale(textValue)) return textValue;

    var htmlValue = firstNumber(htmlText, [
      /(?:quantRatingScore|quant_rating_score|quantScore|quant_score)["'=:>\s\[{,]{0,40}(\d\.\d{1,2})/i,
      /(?:quantRatingScore|quant_rating_score|quantScore|quant_score)["'=:>\s\[{,]{0,40}(\d)\s*\/\s*5/i,
      /Quant (?:Rating|Score)[^0-9]{0,20}(\d\.\d{1,2})\s*(?:\/\s*5|out of 5)?/i,
      /Quant (?:Rating|Score)[^0-9]{0,20}(\d)\s*\/\s*5/i
    ]);
    if (validFiveScale(htmlValue)) return htmlValue;

    return null;
  }

  function extractGradesFromText(pageText) {
    var out = {};
    ["Value", "Growth", "Profitability", "Momentum", "Revisions"].forEach(function (label) {
      var re = new RegExp(label + "[^A-F]{0,24}([A-F][+-]?)", "i");
      var match = String(pageText || "").match(re);
      if (match) out[label.toLowerCase()] = match[1].toUpperCase();
    });
    return out;
  }

  function extractGrades(root, pageText, htmlText, flags) {
    if (!flags.grades) return {};
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
    if (Object.keys(out).length > 0) return out;

    out = extractGradesFromText(pageText);
    if (Object.keys(out).length > 0) return out;

    [
      ["value", /(?:valueGrade|value_grade|value grade)[\s\S]{0,40}?([A-F][+-]?)/i],
      ["growth", /(?:growthGrade|growth_grade|growth grade)[\s\S]{0,40}?([A-F][+-]?)/i],
      ["profitability", /(?:profitabilityGrade|profitability_grade|profitability grade)[\s\S]{0,40}?([A-F][+-]?)/i],
      ["momentum", /(?:momentumGrade|momentum_grade|momentum grade)[\s\S]{0,40}?([A-F][+-]?)/i],
      ["revisions", /(?:revisionsGrade|revisions_grade|revisions grade)[\s\S]{0,40}?([A-F][+-]?)/i]
    ].forEach(function (entry) {
      var match = String(htmlText || "").match(entry[1]);
      if (match) out[entry[0]] = match[1].toUpperCase();
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

  function summarizeSnapshot(data) {
    return {
      page_type: data.page_type,
      ticker: data.ticker || "",
      rating: data.rating || "",
      quant_score: data.quant_score,
      author_rating: data.author_rating || "",
      wall_st_rating: data.wall_st_rating || "",
      grades: Object.keys(data.grades || {}).length,
      title: cleanText(data.title || "").slice(0, 80)
    };
  }

  function buildMeta(flags, data) {
    return {
      rating_conf: data.rating ? (flags.quant ? 100 : 20) : 0,
      quant_conf: data.quant_score != null ? (flags.quant ? 100 : 10) : 0,
      author_conf: data.author_rating ? (flags.author ? 100 : 20) : 0,
      wall_conf: data.wall_st_rating ? (flags.sellSide ? 100 : 20) : 0,
      grade_conf: Object.keys(data.grades || {}).length * 10 + (flags.grades ? 20 : 0)
    };
  }

  function snapshotFromDocument(root, url, htmlText, context) {
    var pageText = bodyText(root);
    var html = String(htmlText || documentHtml(root));
    var pageType = detectPageType(url || window.location.href);
    var flags = classifyContext(url || window.location.href, context && context.label, context && context.kind);
    var tickers = extractTickerList(root, url, pageType);
    var quantScore = extractQuantScore(root, pageText, html, flags);
    var quantRating = extractQuantRating(pageText, html, flags);
    if (!quantRating && flags.quant) {
      quantRating = ratingFromFiveScale(quantScore);
    }
    var data = {
      bookmarklet_version: BOOKMARKLET_VERSION,
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
      rating: quantRating,
      quant_score: quantScore,
      author_rating: extractAuthorRating(pageText, html, flags),
      wall_st_rating: extractWallStreetRating(pageText, html, flags),
      grades: extractGrades(root, pageText, html, flags),
      captured_at: new Date().toISOString(),
      content: extractArticleContent(root, pageText)
    };
    data.__meta = buildMeta(flags, data);
    return data;
  }

  function mergeData(base, extra) {
    var merged = Object.assign({}, base || {});
    var next = extra || {};
    var mergedMeta = merged.__meta || { rating_conf: 0, quant_conf: 0, author_conf: 0, wall_conf: 0, grade_conf: 0 };
    var nextMeta = next.__meta || { rating_conf: 0, quant_conf: 0, author_conf: 0, wall_conf: 0, grade_conf: 0 };

    merged.source = merged.source || next.source || "seeking_alpha";
    merged.bookmarklet_version = merged.bookmarklet_version || next.bookmarklet_version || BOOKMARKLET_VERSION;
    merged.page_type = merged.page_type || next.page_type || "article";
    merged.url = merged.url || next.url || window.location.href;
    merged.title = merged.title || next.title || document.title || "";
    merged.author = merged.author || next.author || "";

    if (merged.page_type === "symbol") {
      var symbol = primarySymbolFromUrl(merged.url || next.url || window.location.href);
      merged.tickers = symbol ? [symbol] : uniqueList([].concat(merged.tickers || [], next.tickers || []));
      merged.ticker = symbol || merged.ticker || next.ticker || (merged.tickers[0] || "");
    } else {
      var tickers = uniqueList([].concat(merged.tickers || [], next.tickers || []));
      merged.tickers = tickers;
      if (!merged.ticker && tickers.length) merged.ticker = tickers[0];
      if (next.ticker && !merged.ticker) merged.ticker = next.ticker;
    }

    if (next.rating && nextMeta.rating_conf >= mergedMeta.rating_conf) {
      merged.rating = next.rating;
      mergedMeta.rating_conf = nextMeta.rating_conf;
    }
    if (next.quant_score != null && nextMeta.quant_conf >= mergedMeta.quant_conf) {
      merged.quant_score = next.quant_score;
      mergedMeta.quant_conf = nextMeta.quant_conf;
    }
    if (next.author_rating && nextMeta.author_conf >= mergedMeta.author_conf) {
      merged.author_rating = next.author_rating;
      mergedMeta.author_conf = nextMeta.author_conf;
    }
    if (next.wall_st_rating && nextMeta.wall_conf >= mergedMeta.wall_conf) {
      merged.wall_st_rating = next.wall_st_rating;
      mergedMeta.wall_conf = nextMeta.wall_conf;
    }
    if (Object.keys(next.grades || {}).length && nextMeta.grade_conf >= mergedMeta.grade_conf) {
      merged.grades = Object.assign({}, next.grades || {});
      mergedMeta.grade_conf = nextMeta.grade_conf;
    } else {
      merged.grades = Object.assign({}, merged.grades || {}, next.grades || {});
    }

    var mergedContent = String(merged.content || "");
    var nextContent = String(next.content || "");
    if (nextContent.length > mergedContent.length) merged.content = nextContent;
    else merged.content = mergedContent;

    merged.captured_at = new Date().toISOString();
    merged.scan_debug = merged.scan_debug || next.scan_debug || undefined;
    merged.__meta = mergedMeta;
    return merged;
  }

  function keywordScore(label, href) {
    var haystack = (cleanText(label) + " " + cleanText(href)).toLowerCase();
    var score = 0;
    TAB_KEYWORDS.forEach(function (keyword, index) {
      if (haystack.indexOf(keyword) >= 0) score += 100 - index;
    });
    return score;
  }

  function buildRouteItem(href, label, source) {
    var normalized = normalizeHref(href);
    if (!normalized) return null;
    return {
      href: normalized,
      label: cleanText(label || normalized),
      source: source || "link",
      score: keywordScore(label, normalized)
    };
  }

  async function expandPossibleMenus(status, debug) {
    var selector = [
      'button[aria-haspopup="menu"]',
      'button[aria-expanded="false"]',
      'button[data-test-id*="menu"]',
      'button[title*="More" i]',
      'button[aria-label*="More" i]'
    ].join(',');
    var buttons = Array.prototype.slice.call(document.querySelectorAll(selector)).filter(function (el) {
      var label = cleanText(el.textContent || el.getAttribute('aria-label') || el.title || '');
      return isVisible(el) && /more|menu|tabs|ratings|factors|analysis/i.test(label) && !/user|profile|account|settings|notification/i.test(label);
    }).slice(0, 4);

    for (var i = 0; i < buttons.length; i++) {
      var label = cleanText(buttons[i].textContent || buttons[i].getAttribute('aria-label') || buttons[i].title || 'menu');
      try {
        status.textContent = 'Opening menu: ' + label;
        buttons[i].click();
        debug.menus_opened.push(label);
        await sleep(500);
      } catch (err) {
        debug.menu_errors.push({ label: label, error: String(err && err.message || err) });
      }
    }
  }

  function collectRouteTabs() {
    var symbolPath = currentSymbolPath();
    if (!symbolPath) return [];
    var origin = window.location.origin;
    var currentUrl = normalizeHref(window.location.href);
    var items = [];
    var seen = {};

    function add(item) {
      if (!item || !item.href || item.href === currentUrl || seen[item.href]) return;
      if (item.href.indexOf(origin + symbolPath) !== 0) return;
      seen[item.href] = true;
      items.push(item);
    }

    document.querySelectorAll('a[href]').forEach(function (el) {
      var href = el.getAttribute('href');
      var label = cleanText(el.textContent || el.getAttribute('aria-label') || el.title || href || '');
      var item = buildRouteItem(href, label, isVisible(el) ? 'visible-link' : 'hidden-link');
      if (!item) return;
      if (!item.score && !/ratings|valuation|earnings|analysis|financials/i.test(item.href)) return;
      add(item);
    });

    GUESSED_SYMBOL_SUFFIXES.forEach(function (suffix) {
      add(buildRouteItem(origin + symbolPath + '/' + suffix, suffix, 'guessed'));
    });

    items.sort(function (a, b) {
      return b.score - a.score || a.href.localeCompare(b.href);
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
      if (!label || label.length > 60) return;
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
      status.textContent = 'Scanning tab: ' + item.label;
      item.element.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'center' });
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
    if (after === before) await sleep(500);
    return snapshotFromDocument(document, window.location.href, null, { kind: 'tab', label: item.label });
  }

  function isSafeInPageTab(item) {
    var el = item && item.element;
    if (!el) return false;
    if (el.tagName && el.tagName.toLowerCase() === 'a') return false;
    if (el.closest && el.closest('a[href]')) return false;
    if (el.getAttribute('href') || el.getAttribute('data-href') || el.getAttribute('data-url')) return false;
    var role = cleanText(el.getAttribute('role') || '').toLowerCase();
    var controls = cleanText(el.getAttribute('aria-controls') || '');
    if (role === 'tab' && controls) return true;
    return false;
  }

  async function fetchRouteTab(item, status) {
    try {
      status.textContent = 'Loading route: ' + item.label;
      var iframeData = await loadIframeTab(item, status);
      iframeData.__route_method = 'iframe';
      return iframeData;
    } catch (iframeErr) {
      status.textContent = 'Fetching route HTML: ' + item.label;
      var response = await fetchWithTimeout(item.href, {
        method: 'GET',
        credentials: 'include',
        headers: { Accept: 'text/html' }
      }, FETCH_TIMEOUT_MS);
      if (!response.ok) {
        throw new Error('HTTP ' + response.status + ' for ' + item.href);
      }
      var html = await response.text();
      var parser = new DOMParser();
      var doc = parser.parseFromString(html, 'text/html');
      var htmlData = snapshotFromDocument(doc, item.href, html, { kind: 'route', label: item.label });
      htmlData.__route_method = 'fetch';
      htmlData.__route_fallback = String(iframeErr && iframeErr.message || iframeErr);
      return htmlData;
    }
  }

  async function enrichSymbolSnapshot(data, status) {
    var merged = mergeData({}, data);
    var debug = {
      bookmarklet_version: BOOKMARKLET_VERSION,
      base: summarizeSnapshot(data),
      menus_opened: [],
      menu_errors: [],
      button_tabs: [],
      route_tabs: []
    };

    sendDebugPing('symbol_enrich_start', { href: data.url, page_type: data.page_type });
    await expandPossibleMenus(status, debug);

    var buttonTabs = collectButtonTabs();
    for (var i = 0; i < buttonTabs.length; i++) {
      if (!isSafeInPageTab(buttonTabs[i])) {
        debug.button_tabs.push({
          label: buttonTabs[i].label,
          skipped: 'unsafe_navigation'
        });
        continue;
      }
      try {
        var buttonSnap = await clickButtonTab(buttonTabs[i], status);
        merged = mergeData(merged, buttonSnap);
        debug.button_tabs.push({
          label: buttonTabs[i].label,
          summary: summarizeSnapshot(buttonSnap)
        });
      } catch (err) {
        debug.button_tabs.push({
          label: buttonTabs[i].label,
          error: String(err && err.message || err)
        });
      }
    }

    var routeTabs = collectRouteTabs();
    sendDebugPing('symbol_routes_collected', {
      href: data.url,
      page_type: data.page_type
    });
    for (var j = 0; j < routeTabs.length; j++) {
      try {
        var routeSnap = await fetchRouteTab(routeTabs[j], status);
        merged = mergeData(merged, routeSnap);
        debug.route_tabs.push({
          label: routeTabs[j].label,
          href: routeTabs[j].href,
          source: routeTabs[j].source,
          method: routeSnap.__route_method || 'unknown',
          fallback: routeSnap.__route_fallback || '',
          summary: summarizeSnapshot(routeSnap)
        });
      } catch (err) {
        debug.route_tabs.push({
          label: routeTabs[j].label,
          href: routeTabs[j].href,
          source: routeTabs[j].source,
          error: String(err && err.message || err)
        });
      }
    }

    debug.final = summarizeSnapshot(merged);
    merged.scan_debug = debug;
    merged.tickers = [merged.ticker || primarySymbolFromUrl(merged.url || window.location.href)].filter(Boolean);
    sendDebugPing('symbol_enrich_done', {
      href: merged.url || data.url,
      page_type: merged.page_type
    });
    return merged;
  }

  function stripInternalFields(data) {
    var payload = Object.assign({}, data);
    delete payload.__meta;
    return payload;
  }

  function postJson(path, payload) {
    return fetch(ENDPOINT + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(stripInternalFields(payload))
    }).then(function (resp) {
      return resp.json().then(function (body) {
        return { ok: resp.ok, status: resp.status, body: body };
      });
    });
  }

  function showStatusBox() {
    var status = document.createElement('div');
    status.style.cssText =
      'position:fixed;top:10px;right:10px;z-index:99999;padding:12px 20px;' +
      'background:#1a1a2e;color:#00ff88;font-family:monospace;font-size:13px;' +
      'border:1px solid #00ff88;border-radius:6px;box-shadow:0 4px 20px rgba(0,255,136,0.3);max-width:460px';
    status.textContent = 'Capturing Seeking Alpha data...';
    document.body.appendChild(status);
    return status;
  }

  async function main() {
    var status = showStatusBox();
    try {
      sendDebugPing('start');
      if (!isSeekingAlphaHost(window.location.hostname)) {
        sendDebugPing('blocked_non_sa');
        status.style.borderColor = '#ff4444';
        status.style.color = '#ff4444';
        status.textContent = 'This bookmarklet only runs on seekingalpha.com pages.';
        setTimeout(function () { status.remove(); }, 6000);
        return;
      }

      var data = snapshotFromDocument(document, window.location.href, null, { kind: 'base', label: 'base' });
      if (data.page_type === 'symbol') {
        data = await enrichSymbolSnapshot(data, status);
      }

      var requests = [];
      var hasQuant = !!(data.ticker && (data.rating || data.quant_score != null || Object.keys(data.grades || {}).length));
      var hasIntel = data.page_type === 'article' && String(data.content || '').length >= 200;

      if (hasQuant) requests.push(postJson('/api/webhooks/sa_quant_capture', data));
      if (hasIntel) requests.push(postJson('/api/webhooks/sa_intel', data));

      if (!requests.length) {
        sendDebugPing('no_payload', { href: data.url, page_type: data.page_type });
        status.style.borderColor = '#ff4444';
        status.style.color = '#ff4444';
        status.textContent = 'No article or quant data detected on this page.';
        setTimeout(function () { status.remove(); }, 6000);
        return;
      }

      sendDebugPing('pre_post', { href: data.url, page_type: data.page_type });
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
          messages.push('SA quant stored for ' + payload.body.ticker + ' (score ' + payload.body.layer_score.score + ', ' + BOOKMARKLET_VERSION.slice(11, 16) + ')');
        } else if (payload.body.job_id) {
          messages.push('LLM intel queued ' + String(payload.body.job_id).substring(0, 8) + ' ' + BOOKMARKLET_VERSION.slice(11, 16));
        } else if (payload.body.message) {
          messages.push(payload.body.message + ' ' + BOOKMARKLET_VERSION.slice(11, 16));
        }
      });

      if (failures.length) {
        sendDebugPing('post_fail', { href: data.url, page_type: data.page_type });
        status.style.borderColor = '#ff4444';
        status.style.color = '#ff4444';
        status.textContent = failures.join(' | ');
      } else {
        sendDebugPing('post_ok', { href: data.url, page_type: data.page_type });
        status.textContent = messages.join(' | ');
      }
      setTimeout(function () { status.remove(); }, 9000);
    } catch (err) {
      sendDebugPing('exception');
      status.style.borderColor = '#ff4444';
      status.style.color = '#ff4444';
      status.textContent = 'Failed: ' + err.message;
      setTimeout(function () { status.remove(); }, 9000);
    }
  }

  main();
})();

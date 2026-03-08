# BRC SA Network Capture

Captures Seeking Alpha symbol, analysis, and news pages from the authenticated browser.

## Install

1. Open `chrome://extensions`
2. Enable `Developer mode`
3. Click `Load unpacked`
4. Select this folder: `browser_extensions/sa_network_capture`
5. Open the extension `Details` page
6. Click `Extension options`
7. Set the BoxRoomCapital endpoint to your app base URL

Example endpoint:

`https://your-replit-app.replit.dev`

Do not include a trailing slash or any path.

## Use

1. Stay logged into Seeking Alpha in Chrome
2. Open either:
   - a symbol page such as `https://seekingalpha.com/symbol/MU`
   - an analysis article such as `https://seekingalpha.com/article/...`
   - a news page such as `https://seekingalpha.com/news/...`
3. Let the page finish loading
4. The extension detects the page type and chooses the right collector automatically
5. One structured capture is forwarded automatically

## What it sends

- Symbol pages:
  - top-level symbol metadata: `ticker`, `url`, `title`, `page_type=symbol`
  - normalized summary fields: `quant_score`, `rating`, `author_rating`, `wall_st_rating`, `grades`
  - `sections`: response metadata grouped by symbol route family
  - `normalized_sections`: structured API-derived sections such as `ratings_history`, `relative_rankings`, `valuation_metrics`, `metric_grades`, `sector_metrics`, and `earnings_estimates`
  - `raw_responses`: the JSON payloads captured from the symbol routes
- Analysis/news pages:
  - `page_type=article|news`
  - `url`, `canonical_url`, `title`, `author`, `tickers`
  - cleaned article/news body text
  - summary/description metadata
  - publish/modified timestamps when available
  - lightweight raw metadata for the intel pipeline

## Current behavior

- Symbol pages use API-first capture with a small fallback tab scan
- Analysis/news pages use a DOM+metadata extractor tuned for text pages
- Symbol captures post to `sa_symbol_capture`
- Analysis/news captures post to `sa_page_capture`
- Plain hub/index pages such as `/market-news` are ignored, but inline-expanded news stories on that page are captured individually as you open them
- Dedupe is done per page session using content fingerprints, with backend canonical-URL suppression for repeat analysis

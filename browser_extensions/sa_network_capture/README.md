# BRC SA Network Capture

Captures full Seeking Alpha symbol snapshots from the authenticated browser and forwards them to `sa_symbol_capture`.

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
2. Open a symbol page such as `https://seekingalpha.com/symbol/MU`
3. Let the page finish loading
4. The extension fetches the stable symbol APIs directly from the authenticated page session, then falls back to a limited tab scan only if key sections are missing
5. One merged symbol snapshot is forwarded automatically

## What it sends

- top-level symbol metadata: `ticker`, `url`, `title`, `page_type=symbol`
- normalized summary fields: `quant_score`, `rating`, `author_rating`, `wall_st_rating`, `grades`
- `sections`: response metadata grouped by symbol route family
- `normalized_sections`: structured API-derived sections such as `ratings_history`, `relative_rankings`, `valuation_metrics`, `metric_grades`, `sector_metrics`, and `earnings_estimates`
- `raw_responses`: the JSON payloads captured from the symbol routes
- the first ratings-history entry is still normalized into the summary so the existing SA signal path continues to work

## Current behavior

- Works on live symbol pages without depending on DOM scraping
- Prefers direct JSON API collection for stable endpoints
- Uses visible tab clicks only as a fallback discovery pass
- Posts one aggregated symbol snapshot per page session
- Dedupe is done per section + response URL + history id in the page session

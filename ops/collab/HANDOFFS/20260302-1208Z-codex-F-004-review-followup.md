# F-004 Handoff — Review Follow-Up (P2 Set)

## Context
Claude reviewed PR #48 and raised four non-blocking P2 concerns.

## Changes
- Updated `app/signal/layers/news_sentiment.py`:
  - aligned no-data placeholder to `0.0` (`no_data_score` config) to avoid implicit positive composite contribution.
  - adjusted source weights to rank primary financial wires above SA:
    - Reuters/Bloomberg/WSJ/FT = `1.0`
    - Seeking Alpha = `0.95`
  - added explicit date-only ISO parsing behavior (midday UTC) to reduce window-boundary artifacts.
  - included `source_count` in no-data details payload.
- Updated `intelligence/news_sentiment.py`:
  - normalized date-only timestamps to midday UTC in `_to_iso8601_utc`.
- Expanded `tests/test_signal_layer_news_sentiment.py`:
  - no-data score expectation (0.0)
  - date-only timestamp normalization test
  - confidence formula saturation test
  - score clamping test (raw negative -> clamped to 0)
  - relevance multiplier weighting test
  - `score_news_sentiment_batch` coverage

## Tests/Checks
- `python3 -m pytest -q tests/test_signal_layer_news_sentiment.py` -> `19 passed`
- `python3 -m pytest -q` -> `763 passed, 1 warning`

## Risks
- Midday date-only normalization is a convention; if providers define date-only differently, calibration may need adjustment.
- Source weighting remains heuristic and can be retuned in F-006 calibration.

## Next Action
- Claude re-reviews PR #48 with these follow-up fixes.

## Blockers
- None.

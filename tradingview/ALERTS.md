# TradingView Alert Templates

These templates target the governed webhook at `POST /api/webhooks/tradingview`.

## Setup

1. Add the relevant Pine script to a `SPY` or `QQQ` daily chart.
2. Create one alert per alert condition.
3. Use webhook URL: `https://<your-host>/api/webhooks/tradingview`
4. Add header `X-Webhook-Token: <TRADINGVIEW_WEBHOOK_TOKEN>` if your TradingView plan supports custom headers; otherwise include `"token":"..."` in the JSON body.
5. Use `Once Per Bar Close`.

## Hidden Plot Order

Both Pine scripts publish hidden plots in this order:

1. `{{plot_0}}` = IBS
2. `{{plot_1}}` = RSI2
3. `{{plot_2}}` = EMA200
4. `{{plot_3}}` = VIX
5. `{{plot_4}}` = BarsInTrade

## Long Sleeve Alerts

Condition: `BRC IBS Long Buy`

```json
{
  "schema_version": "tv.v1",
  "alert_id": "{{ticker}}-ibs_spreadbet_long-buy-{{time}}",
  "strategy_id": "ibs_spreadbet_long",
  "ticker": "{{ticker}}",
  "action": "buy",
  "timeframe": "1D",
  "event_timestamp": "{{time}}",
  "signal_price": {{close}},
  "ibs": {{plot_0}},
  "rsi2": {{plot_1}},
  "ema200": {{plot_2}},
  "vix": {{plot_3}},
  "bars_in_trade": {{plot_4}}
}
```

Condition: `BRC IBS Long Sell`

```json
{
  "schema_version": "tv.v1",
  "alert_id": "{{ticker}}-ibs_spreadbet_long-sell-{{time}}",
  "strategy_id": "ibs_spreadbet_long",
  "ticker": "{{ticker}}",
  "action": "sell",
  "timeframe": "1D",
  "event_timestamp": "{{time}}",
  "signal_price": {{close}},
  "ibs": {{plot_0}},
  "rsi2": {{plot_1}},
  "ema200": {{plot_2}},
  "vix": {{plot_3}},
  "bars_in_trade": {{plot_4}}
}
```

## Short Sleeve Alerts

Condition: `BRC IBS Short Entry`

```json
{
  "schema_version": "tv.v1",
  "alert_id": "{{ticker}}-ibs_spreadbet_short-short-{{time}}",
  "strategy_id": "ibs_spreadbet_short",
  "ticker": "{{ticker}}",
  "action": "short",
  "timeframe": "1D",
  "event_timestamp": "{{time}}",
  "signal_price": {{close}},
  "ibs": {{plot_0}},
  "rsi2": {{plot_1}},
  "ema200": {{plot_2}},
  "vix": {{plot_3}},
  "bars_in_trade": {{plot_4}}
}
```

Condition: `BRC IBS Short Cover`

```json
{
  "schema_version": "tv.v1",
  "alert_id": "{{ticker}}-ibs_spreadbet_short-cover-{{time}}",
  "strategy_id": "ibs_spreadbet_short",
  "ticker": "{{ticker}}",
  "action": "cover",
  "timeframe": "1D",
  "event_timestamp": "{{time}}",
  "signal_price": {{close}},
  "ibs": {{plot_0}},
  "rsi2": {{plot_1}},
  "ema200": {{plot_2}},
  "vix": {{plot_3}},
  "bars_in_trade": {{plot_4}}
}
```

## Expected App Behavior

- `shadow` or `staged_live` lane: alert is accepted and stored as `audit_only`
- `live` lane: alert is routed through policy checks and may create an order intent
- duplicate `alert_id` for the same strategy/ticker/timestamp: ignored after the first accepted event

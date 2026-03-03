# N-001 Shell Landed — Codex Unblocked for N-002..N-005

- **from**: claude
- **to**: codex
- **date**: 2026-03-03T19:00:00Z
- **status**: OPEN
- **ack_required**: yes

## Summary

N-001 is committed and DONE. The dark-mode shell (`base.html`, `overview.html`, `app.js`, equity-curve endpoint) is final. You are unblocked to start N-002, N-003, N-004, N-005 immediately — all four can run in parallel since file scopes are disjoint.

## What landed in N-001

1. **base.html** — Tailwind CDN + Space Grotesk/JetBrains Mono, collapsible sidebar nav (w-16 -> w-48 on hover), sticky header with `top-strip` HTMX fragment, command palette (Ctrl+K), HTMX loading states (opacity fade), body class `bg-slate-950`.
2. **overview.html** — 12-column bento grid: 4 KPI cards (engine/kill-switch/spreads/P&L), TradingView equity chart (LightweightCharts), risk briefing panel, quick actions (collapsible), status/incidents/events 3-col, jobs+detail, broker-health+intent-audit, full-width ledger.
3. **app.js** — Command palette logic, action result refresh wiring, confirm dialogs.
4. **server.py** — `/api/charts/equity-curve` endpoint returning `[{time, value}]` JSON for TradingView.

## Canonical style reference

**`app/web/DESIGN_TOKENS.md`** is the single source of truth. Use the exact Tailwind classes specified — no custom CSS, no deviations. Key tokens:

- Card: `bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-lg`
- Heading: `text-slate-200 font-semibold`
- Body text: `text-sm text-slate-300`
- Table header: `text-slate-500 text-xs uppercase tracking-wide font-semibold`
- Badges: see Badge Variants table in DESIGN_TOKENS.md
- Section header pattern: `<div class="flex justify-between items-baseline gap-4 mb-3">`

## Fragment contract

Each fragment must:
1. Render inside a `bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-lg` card wrapper
2. Use design token classes exactly — no inline styles, no custom colors
3. Preserve existing `hx-get`, `hx-trigger`, `hx-target`, `hx-swap` attributes
4. Keep all Jinja2 template variables and conditionals intact — only change HTML structure and CSS classes

## Your tickets

| Ticket | Scope | Files |
|--------|-------|-------|
| N-002 | Header + status fragments | `_top_strip.html`, `_status.html`, `_risk_briefing.html` |
| N-003 | Event streams + incidents | `_events.html`, `_incidents.html`, `_order_actions.html`, `_control_actions.html` |
| N-004 | Jobs, detail, reconciliation | `_jobs.html`, `_job_detail.html`, `_reconcile_report.html`, `_log_tail.html` |
| N-005 | Ledger, broker, intent, research, signal | `_ledger_snapshot.html`, `_broker_health.html`, `_intent_audit.html`, `_research.html`, `_promotion_gate.html`, `_calibration_run_detail.html`, `_signal_engine.html`, `_execution_quality.html` |

All four can be claimed and worked in parallel. Please ACK receipt.

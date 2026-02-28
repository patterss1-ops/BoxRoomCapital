# Final Spec Comparison: Perplexity Doc vs Current Roadmap

## Documents Compared
- Perplexity spec: `/Users/stephenpatterson/Downloads/BOUTIQUE_HEDGE_FUND_APP_SPECIFICATION.md`
- Claude latest plan: `~/.claude/plans/breezy-twirling-volcano.md`
- Local merged plans:
  - `FULL_SPECTRUM_PLAN.md`
  - `MERGED_PLAN_COMPARISON.md`
  - `CRYPTO_STRATEGY_VALIDATION.md`
  - `GRID_TRADING_DEEP_DIVE.md`

## Executive Verdict
The Perplexity doc is strong as a **vision + product specification**, but too broad and too optimistic to use directly as a build plan for a one-person live operation.

Best use:
- Keep it as the **north-star architecture** and feature catalog.
- Use the local/Claude merged roadmap as the **execution and risk-gated delivery plan**.

## High-Value Additions from Perplexity Spec (Adopt)
1. Signal Engine architecture:
   - 3-tier pipeline (quant layers -> intelligence feeds -> AI panel) is well-structured.
2. Practical asset/account framing:
   - Clear split by sleeve and account purpose is useful for portfolio operations.
3. Concrete role specialization for AI panel:
   - Explicit model roles reduce ambiguity and improve operator workflow.
4. Useful implementation decomposition:
   - Layer/module breakdown can be mapped into repo epics and tickets.

## Major Gaps / Risks (Modify Before Build)
1. Evidence quality mixing:
   - Peer-reviewed findings and marketing/performance claims are mixed at equal weight.
2. Over-ambitious phase scope:
   - 16-week timeline plus multi-broker + 8-layer scoring + 4-model AI panel is too dense for safe rollout.
3. Operational risk underweight:
   - Needs stricter promotion gates, SLOs, incident playbooks, and reconciliation hardening.
4. Compliance/policy ambiguity:
   - Prediction market paths and VPN-dependent workflows should stay outside production lanes.
5. Data dependency bloat:
   - Too many external feeds early increases fragility, cost, and troubleshooting burden.

## Direct Comparison: Sleeve-by-Sleeve

### Sleeve 1 (Equity Mean Reversion)
- Decision: **Adopt with guardrails**
- Notes:
  - Fit with existing IBS experience.
  - Keep in candidate lane until IBKR execution and risk controls are proven.

### Sleeve 2 (Commodity Trend Following)
- Decision: **Adopt**
- Notes:
  - Already aligned with existing findings that some IG instruments survive costs better.

### Sleeve 3 (Crypto; parked)
- Decision: **Adopt as candidate only**
- Notes:
  - Funding carry + momentum are stronger core candidates.
  - Grid remains conditional satellite (not regular core), per local backtest.

### Sleeve 4 (Signal Engine; primary focus)
- Decision: **Adopt in staged form**
- Notes:
  - Start with fewer layers (eg, 3-4 robust layers), then expand.
  - Avoid launching all 8 layers + AI panel at once.

### Sleeve 5 (Options income)
- Decision: **Adopt**
- Notes:
  - Strong overlap with your current options direction.
  - Move to IBKR options once adapter and reconciliation are stable.

### Sleeve 6 (Cash/bonds rotation)
- Decision: **Adopt**
- Notes:
  - Good defensive sleeve, operationally simple, useful for risk normalization.

## AI Panel Assessment
- Keep:
  - Role-based prompts and adversarial risk role are valuable.
- Change:
  - Move to API automation only after Tier-1 quant pipeline is stable.
  - Introduce scoring calibration and auditability before any execution coupling.
- Defer:
  - Full 4-model always-on panel for every candidate; start with selective invocation.

## Data/Tool Stack Assessment
- Build now:
  - TradingView webhooks, core market data, SEC/FINRA essentials, existing internal feeds.
- Build next:
  - One or two premium feeds with clear marginal alpha impact.
- Defer:
  - Broad vendor stack until pipeline reliability and attribution prove value.

## Final Merged Build Plan (Recommended)

### Phase A (Now): Execution and Control Foundation
1. IBKR adapter (paper first)
2. Account router + unified order intent model
3. Unified ledger and reconciliation across IG + IBKR
4. Hard risk gates and kill-switch hierarchy

### Phase B: First Live Expansion
1. GTAA + GEM in IBKR ISA (small capital, staged promotion)
2. Preserve IG options sleeve and continue reliability hardening
3. Add sleeve-level NAV/PnL and risk reporting

### Phase C: Signal Engine MVP
1. Implement 3-4 strongest quant layers first
2. Add shortlist/threshold logic and Telegram operations flow
3. Add limited AI panel review for flagged/high-value names

### Phase D: Candidate Expansion
1. GIA options/pairs as candidate sleeves
2. Smarkets adapter and paper models
3. Crypto carry + momentum candidate rollout; grid only with strict regime gate

### Phase E: Scale and Governance
1. Promotion committee workflow (research -> paper -> shadow -> live)
2. Monthly attribution and risk-budget reallocation
3. Controlled feed/vendor expansion only when attribution supports it

## Non-Negotiable Rules
1. No live strategy without backtest + shadow + paper + approval.
2. No scale-up without clean reconciliation and incident-rate threshold pass.
3. Every order must map to strategy version, account route, and risk decision.
4. Keep policy-grey paths out of production.

## Bottom Line
The Perplexity document is a strong strategic blueprint.  
To make it executable and safe for your operation, combine it with the local risk-gated implementation approach and staged rollout discipline already defined in this repo.

# Plan Comparison and Merged Recommendation

## Compared Documents
- Claude latest: `~/.claude/plans/breezy-twirling-volcano.md` (updated 2026-02-27 23:04)
- Local operating plan: `FULL_SPECTRUM_PLAN.md`
- Local crypto validation: `CRYPTO_STRATEGY_VALIDATION.md`
- Local grid deep dive: `GRID_TRADING_DEEP_DIVE.md`

## Where Claude Plan Is Strong
1. Clear vision and structure:
   - Five sleeves and six architectural divisions are easy to reason about.
2. Good inventorying:
   - Explicit broker/tool inventory (IG, CityIndex, IBKR, II, Smarkets, Polymarket, TradingView, etc.).
3. Concrete phased build details:
   - Week-by-week Phase 1 tasks are implementable.
4. Preserves existing live edge:
   - Keeps current IG options/IBS stack as a core sleeve.

## Where Local Plan Is Stronger
1. Build discipline and risk gating:
   - Enforces promotion gates and research/paper/live separation.
2. Operational realism for one-person desk:
   - Reduces simultaneous strategy launches and prioritizes reliability.
3. Compliance posture:
   - Keeps policy-grey workflows out of core production lanes.
4. Evidence hygiene:
   - Distinguishes research-grade evidence from marketing claims.
5. Crypto strategy robustness:
   - Funding carry/trend/range stack with explicit kill conditions.

## Key Conflicts to Resolve
1. Strategy breadth:
   - Claude scope is broad; merge should constrain initial live set.
2. Prediction market path:
   - Keep as research/candidate until legal/ops policy is fully decided.
3. Data vendor sprawl:
   - Stage integrations by marginal utility to avoid operational overhead.
4. Timeline aggressiveness:
   - Claude week plan is ambitious; sequence by dependency and testability.

## Merged Recommendation (Adopt Now)

### Core Live (keep/expand first)
1. Sleeve 1 IG options + mean reversion (existing)
2. Sleeve 2 IBKR ISA banker strategies (GTAA + GEM)

### Candidate (paper/shadow first)
1. Sleeve 3 GIA options/pairs
2. Sleeve 4 Smarkets models
3. Crypto range engine (grid) only as conditional satellite

### Lab/Research only
1. Polymarket arb/market making
2. DeFi/MEV
3. Any latency-arb strategies

## Execution Order (Practical)
1. IBKR adapter + execution router + unified ledger
2. Fund/risk layer (NAV, sleeve PnL, hard limits, kill switches)
3. Intelligence MVP (TradingView webhook + calendar/news basics)
4. Promotion pipeline (research -> paper -> shadow -> live)
5. Smarkets adapter and paper strategies
6. Expand candidates only after stable incident/risk metrics

## Crypto-Specific Merge Decision
1. Funding carry = core crypto sleeve
2. Momentum = directional sleeve
3. Grid = conditional satellite only (not regular/core)
4. Apply strict trend veto, breakout stops, fee budget, and drawdown kill switch

## Canonical Governance Rules
1. No new live strategy without backtest + shadow + paper + approval.
2. No capital scale-up without stable reconciliation and risk compliance.
3. Every trade must be attributable to strategy version and approval state.
4. Maintain one-console operations with exception-based human workflow.

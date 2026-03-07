# Research System Plan — Final Merged Document

**Date:** 2026-03-07 | **Sources:** Research Report + Claude Review + Codex Review
**Status:** Parked — waiting for architecture rework (Codex) before build

---

## Consensus Principles

These are agreed across all three reviews and should constrain all future design decisions.

| Principle | Source |
|-----------|--------|
| Edge taxonomy, not free-form ideation | Report |
| Falsification blocks promotion; voting doesn't force trades | Report + Claude |
| ~6 functional services, not 10 anthropomorphized agents | Claude + Codex |
| Deterministic pipeline first, LLM assistance second | Codex |
| Narrow MVP: liquid futures + large-cap event/revision | Report + Claude |
| Process discipline is the edge, not information advantage | All three |
| Migration from existing codebase, not greenfield | Claude + Codex |
| No fully autonomous capital allocation in MVP | Report |
| Options as expression layer only | Report + Claude |
| 4-state outcomes: promote / revise / park / reject | Report + Claude |

---

## Target Design: Services + Artifacts + Gates

Per Codex's recommendation: think services and typed artifacts, not anthropomorphized agents.

### Deterministic Services (code-first, no LLM)

| Service | Responsibility |
|---------|---------------|
| **Intake & Normalization** | Dedup, timestamp, source classification, entity-to-instrument mapping, raw provenance preservation |
| **Source Reliability Scoring** | Credibility score by source class (official filing > wire > social), corroboration tracking |
| **Taxonomy Enforcement** | Hypotheses must declare an edge family from approved list; reject if none fits |
| **Experiment Registry** | Freeze hypothesis ID, data sources, splits, search budget, variant count before any backtest |
| **Promotion Gate** | State machine: shadow → staged_live → live with 4-state outcomes (already partially built in `fund/promotion_gate.py`) |
| **Cost Model** | IG-specific spread, funding, slippage; asset-class cost templates |
| **Kill-Rule Monitor** | Track declared invalidators; auto-pause within preauthorized limits; never auto-scale up |

### LLM-Assisted Services (model calls scoped to specific tasks)

| Service | LLM Role |
|---------|----------|
| **Signal Extraction** | Convert material events into structured observations (what changed, for whom, vs what expectation) |
| **Hypothesis Formation** | Generate small number of candidates within edge taxonomy; output typed `HypothesisCard` |
| **Challenge & Falsification** | Retrieve prior evidence, find cheapest alternative explanation, flag crowding/beta leakage |
| **Regime Context** | Maintain point-in-time state vector (macro, vol, trend); condition hypotheses on current environment |
| **Bounded Synthesis** | Summarize research artifacts for human review; never smooth away unresolved objections |

### Canonical Artifacts

| Artifact | Key Fields |
|----------|-----------|
| `EventCard` | source IDs, source class, timestamp, corroboration count, claims, affected instruments, materiality, credibility |
| `HypothesisCard` | hypothesis ID, edge family, market-implied view, variant view, mechanism, catalyst, horizon, direction, invalidators, failure regimes, candidate expressions |
| `FalsificationMemo` | cheapest alternative explanation, beta leakage check, crowding check, unresolved objections |
| `TestSpec` | point-in-time datasets, feature list, train/val/test splits, baselines, search budget, cost model, eval metrics |
| `ExperimentReport` | gross/net performance, robustness checks, capacity, correlation, implementation caveats |
| `TradeSheet` | instrument(s), sizing, entry/exit, holding period, hedge plan, risk limits, kill criteria |
| `RetirementMemo` | trigger, diagnosis, lessons, dead vs parked |

### Scoring Rubric

100-point score before penalties:
- Source integrity: 10
- Mechanism clarity: 15
- Prior empirical support: 15
- Incremental information advantage: 10
- Regime fit: 10
- Point-in-time testability: 10
- Implementation realism/costs/capacity: 15
- Portfolio fit: 10
- Monitoring/kill clarity: 5

Penalties: search-space/complexity up to -15, crowding up to -10, vendor/data fragility up to -10.

Thresholds: <60 reject, 60-69 revise/park, 70-79 eligible for registered testing, 80-89 eligible for paper/micro pilot, 90+ eligible for live pilot with human sign-off.

---

## Approved Edge Taxonomy

Hypotheses must map to one of these families:

1. **Underreaction / Revision** — post-earnings drift, analyst revision, slow information diffusion
2. **Carry / Risk Transfer** — interest rate differential, term premium, insurance premium
3. **Trend / Momentum** — time-series continuation, cross-sectional momentum
4. **Flow / Positioning** — hedging pressure, forced selling, index rebalancing
5. **Relative Value** — law-of-one-price violations, temporary divergences among close substitutes
6. **Convexity / Insurance** — variance risk premium, skew premium, event-specific vol
7. **Regime Dislocation** — structural breaks, liquidity regime shifts, policy regime changes

If a hypothesis doesn't map to one of these, it gets parked or rejected. No "miscellaneous" bucket.

---

## MVP Scope

**Instruments:** Liquid futures (indices, rates, commodities) + US/UK large-cap equities via IG spread bets

**Edge families for MVP:** Trend/carry (futures) + underreaction/revision (equities)

**Options:** Expression layer only — validated underlying thesis required first

**Excluded from MVP:** Autonomous crypto, OTC credit, vol-specialist premium harvesting, high-frequency stat arb

**Sources:** Primary releases, earnings transcripts, major news wires, curated research notes, whitelisted official X accounts. Social media as attention radar only, not signal source.

---

## Migration Path from Current System

### Prerequisites (architecture rework — Codex leading)

Per `ops/ARCHITECTURE_PLAN_v2.md`:
- P0: Runtime stability + observability
- P1: E2E fixture fix
- P2: API boundary cleanup (server.py split)
- P3: Data layer consolidation (trade_db.py domain split)
- P4: Intel pipeline refactor
- P5: Config centralization
- P6: Dead code cleanup

### Research system build (after architecture rework)

| Phase | Work | Builds on |
|-------|------|-----------|
| 1 | Define typed artifact schemas (`EventCard`, `HypothesisCard`, etc.) as dataclasses/Pydantic models | P4 complete |
| 2 | Replace 4-model council vote with `HypothesisCard` → `FalsificationMemo` → `ExperimentReport` artifact flow | Phase 1 |
| 3 | Add edge taxonomy enforcement — hypotheses must declare edge family | Phase 2 |
| 4 | Add regime/state context service — macro, vol, trend state as conditioning input | Phase 3 |
| 5 | Improve cost modeling in backtester with IG-specific spread/funding/slippage | Phase 3 |
| 6 | Formalize retirement/kill pathway with tracked invalidators | Phase 5 |

---

## Governance (Practical, Not Institutional)

- Human sign-off required before any live promotion
- Locked operator checklists before tests are opened
- Immutable audit artifacts (prompts, model versions, test specs, results)
- Clear kill rules per strategy
- No same context window both generates and approves a strategy
- Material prompt or model change = revalidation event

---

## Source Documents

| File | Role |
|------|------|
| `ops/RESEARCH_REPORT.md` | Original commissioned report — philosophy and principles |
| `ops/RESEARCH_REPORT_REVIEW.md` | Claude's assessment — migration path and gap analysis |
| `ops/RESEARCH_REPORT_REVIEW_codex.md` | Codex's assessment — deterministic-first, citation critique |
| `ops/ARCHITECTURE_PLAN_v2.md` | Merged architecture priorities (prerequisite for this plan) |

# Review of AI-Agent Trading System Research Report

**Reviewer:** Claude (Opus 4.6) | **Date:** 2026-03-07 | **Status:** Honest assessment for team review

---

## Summary

The commissioned research report proposes replacing BoxRoomCapital's current 4-model council (Anthropic/OpenAI/Grok/Google voting on ideas) with a 10-agent stage-gated research and deployment system organized around an approved edge taxonomy, hard falsification gates, and cost-aware validation. This review assesses what to adopt, what to simplify, and how to migrate from the existing codebase.

---

## What's Right (High Confidence — Adopt These)

### 1. Edge taxonomy over free-form ideation
Building around an approved set of edge families (trend/momentum, carry, underreaction/revision, relative value, convexity/insurance, regime dislocation) is categorically better than letting AI generate unconstrained hypotheses. The report's evidence on publication decay, multiple testing bias, and factor zoo inflation is well-sourced and correct.

### 2. "No democracy among agents"
One unresolved falsification should block promotion. Five enthusiastic AI votes should not force a trade. The current council's majority-vote pattern is exactly the "parliament of eloquent bots voting themselves into delusion" the report warns against.

### 3. MVP scope: narrow and liquid
Liquid futures trend/carry + large-cap event/revision underreaction. Options as expression layer only. No autonomous crypto. Narrower than the current VISION.md ambition, but correct for proving the process works before expanding.

### 4. Implementation cost emphasis
Gross Sharpe is meaningless until netted against real costs — IG spread-bet spreads, funding, slippage, roll. This is the most practically important takeaway for our execution environment.

### 5. Capital ladder concept
The report's Stage 10-11 (paper -> micro -> pilot -> scale, never the reverse) already maps to our existing promotion pipeline (shadow -> staged_live -> live). Validation that the current architecture was heading in the right direction.

### 6. Four-state outcome discipline
Every gate outputs only `promote`, `revise`, `park`, or `reject`. This is cleaner than our current binary pass/fail and should be adopted in the promotion gate.

---

## What Concerns Me (Proceed With Caution)

### 1. 10 agents is overengineered for a one-person operation

The report's own red team (objection 5) admits a solo operator can't replicate institutional independence. But the main body still proposes 10 specialized agents — an organizational chart for a 50-person quant fund mapped onto LLM prompts. Recommended consolidation:

| Report proposes | Collapse into | Rationale |
|---|---|---|
| Source Reliability + State/Regime | **Intake & Context Agent** | One agent normalizes events AND maintains state vector |
| Mechanism/Literature + Red-Team | **Challenge Agent** | Retrieval + falsification are the same skill: "find reasons this is wrong" |
| Experiment Registrar + Backtest/Cost | **Test Agent** | Registration is the preamble to testing, not a separate function |
| Portfolio Expression + Deployment Gate | **Expression & Risk Agent** | Instrument choice and risk check are one decision |
| Hypothesis & Divergence | **Hypothesis Agent** | Stays as-is but with hard taxonomy constraint |
| Live Monitoring & Retirement | **Monitor Agent** | Stays as-is |

Result: ~6 functional agents instead of 10. More realistic for token budget, latency, and debugging complexity.

### 2. Institutional governance frameworks don't translate 1:1

SR 11-7 is for banks with 20+ person model risk teams. ESMA guidance is for regulated asset managers with compliance departments. Direct mapping creates two risks:
- **Process paralysis**: more time documenting than trading
- **False confidence**: checkbox compliance != actual risk management

Adopt the spirit (auditability, separation of concerns, effective challenge). Don't adopt the letter.

### 3. The "no informational advantage" objection is existential

Red team objection #1 is the most important point in the entire report and deserves 10x more attention. If we're reading public news with public models and public data, our informational edge is literally zero before costs. The report's own evidence says social media signals are transitory and cost-blind, published anomalies decay, and common models create correlated positioning.

**Implication:** Our edge — if any — comes from process discipline and rejection quality, not from better information. That's a valid edge (most retail traders have zero process), but it's thin and it caps realistic ambition. The system should be designed to be honest about this rather than pretending AI coverage creates informational alpha.

### 4. No engagement with the existing codebase

The report doesn't mention that we already have:
- 8 signal layers (L1-L8) doing some of what Agents 1-3 propose
- An intel pipeline with 4-model council voting (`intelligence/intel_pipeline.py`)
- An idea pipeline with lifecycle management (`intelligence/idea_pipeline.py`)
- An event store with provenance (`intelligence/event_store.py`)
- A promotion gate with shadow/staged/live (`fund/promotion_gate.py`)
- A feature store for ML features (`intelligence/feature_store.py`)
- 91K lines of working code that trades real money via IG
- 2300+ tests

The migration path from current state to proposed state is the hardest part, and the report is silent on it.

---

## Mapping: Report Recommendations vs Current Architecture

| Report concept | Current implementation | Gap | Effort |
|---|---|---|---|
| Edge taxonomy | Not implemented — ideas are free-form | Need `HypothesisCard` schema with edge_family enum | Medium |
| Source reliability scoring | `EventStore` has provenance but no trust scoring | Need source_class + credibility_score on events | Small |
| State/regime context | Not implemented | Need `StateSnapshot` service (macro/vol/trend regime) | Large |
| Constrained hypothesis generation | `idea_pipeline.py` — unconstrained | Major redesign — taxonomy gates on idea creation | Large |
| Falsification/challenge | Council votes yes/no — no structured falsification | Need `FalsificationMemo` with cheapest-alternative-explanation | Medium |
| Experiment registration | Not implemented | Need `TestSpec` schema + frozen-before-backtest rule | Medium |
| Cost-realistic backtesting | `analytics/` has backtester but cost modeling is thin | Need IG-specific cost model (spread, funding, slippage) | Large |
| 4-state promotion outcomes | Binary pass/fail in promotion gate | Extend to promote/revise/park/reject | Small |
| Canonical artifact schemas | Partial — `LayerScore`, `CompositeRequest` exist | Need `HypothesisCard`, `TestSpec`, `TradeSheet`, `RetirementMemo` | Medium |
| Live monitoring & retirement | Signal shadow cycle monitors health | Need formal kill-criteria tracking and `RetirementMemo` | Medium |
| Human CIO approval gate | Not enforced in code | Need approval requirement before live promotion | Small |
| Scoring rubric (100-point) | Composite scorer exists but different structure | Redesign scoring around report's rubric weights | Medium |

---

## Recommended Migration Path

Build incrementally from existing code. Each phase is independently valuable and testable.

### Phase 1: Artifact Schemas & Edge Taxonomy
- Define `HypothesisCard`, `FalsificationMemo`, `TestSpec`, `TradeSheet`, `RetirementMemo` as typed dataclasses
- Add `EdgeFamily` enum (underreaction, carry, trend, relative_value, convexity, regime_dislocation)
- Enforce edge_family on idea creation in `idea_pipeline.py`
- Extend promotion gate with 4-state outcomes (promote/revise/park/reject)

### Phase 2: Replace Council Vote with Structured Challenge
- Replace `intel_pipeline.py` council voting with: generate hypothesis -> retrieve prior evidence -> falsify -> score
- Each step produces a typed artifact, not free-form prose
- Kill the "balanced synthesizer" pattern — no agent smooths away objections

### Phase 3: State/Regime Context
- Build `StateSnapshot` service: macro regime, vol regime, trend state, carry environment
- Feed as conditioning input to hypothesis generation and promotion decisions
- Use point-in-time data only — no look-ahead

### Phase 4: Cost Realism
- IG-specific cost model: actual spread widths, overnight funding, slippage estimates
- Integrate into backtester so no strategy is evaluated on gross returns alone
- Add capacity estimation (position size vs typical volume)

### Phase 5: Monitoring & Retirement Formalization
- Track explicit invalidators per strategy
- Formalize kill criteria: data breach, control failure, thesis invalidation, persistent underperformance
- `RetirementMemo` archived for institutional memory

### Pre-requisite: Complete Architecture Plan P0-P2
The infrastructure cleanup in `ops/ARCHITECTURE_PLAN.md` (server.py split, data layer unification, intel pipeline cleanup) should be done BEFORE this migration. Redesigning the council workflow on top of a 5000-line server.py and fragmented data layer will create unnecessary pain.

---

## Bottom Line

**Adopt the philosophy. Simplify the agent count. Build incrementally from what exists.**

The report is right that the current council is wrong — voting models is theatre. It's right that edge taxonomy + falsification gates + cost realism is the correct architecture. It's right that options should be expression-only and scope should be narrow.

But don't try to build 10 agents from scratch. The existing codebase has significant infrastructure that maps onto this vision. The migration should preserve what works (promotion pipeline, event store, signal layers, test suite) and replace what doesn't (free-form ideation, council voting, binary pass/fail, missing cost realism).

The honest assessment of our edge: it comes from process discipline and rejection quality, not from information advantage. The system should be designed around that truth.

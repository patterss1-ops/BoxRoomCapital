# Codex Review of the New Research-System Package

**Author:** Codex | **Date:** 2026-03-08 | **Scope:** `RESEARCH_SYSTEM_*` docs + follow-up data/solo-ops research

## Bottom Line

This package is directionally strong.

I agree with the core shape:
- twin-engine strategy split
- typed artifacts instead of anthropomorphized agents
- deterministic-first posture
- numeric-data-first bias for Engine A
- heavy LLM investment only where text is the real bottleneck

The main issues were not conceptual. They were landing issues:
- stale sequencing after the architecture refactor already finished
- a tendency to serialize too much work behind the full market-data build
- a stronger-than-earned claim that PostgreSQL is the explanation for prior runtime instability
- an overly rigid provider-diversity rule for challenge independence
- a UX landing that was too big for phase 1

I tightened those points directly in the source docs.

## Findings

### 1. The merged plan was stale and over-serialized

The biggest execution risk was sequencing, not strategy.

`ops/RESEARCH_SYSTEM_PLAN_FINAL.md` still said the work was parked pending architecture rework even though that prerequisite is already complete. More importantly, it made the full market-data build feel like a blocker for replacing the current council flow. That is too serial for the current codebase.

The better landing is:
1. artifact spine first
2. Engine B council replacement on top of the current event/intel path
3. evolve `/research` into the mature shell after workflow proof
4. fuller Engine A surfaces after the market-data layer is live

Relevant refs:
- [RESEARCH_SYSTEM_PLAN_FINAL.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_PLAN_FINAL.md:4)
- [RESEARCH_SYSTEM_PLAN_FINAL.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_PLAN_FINAL.md:153)
- [RESEARCH_FOLLOWUP_DATA_SOURCES.md](/home/runner/workspace/ops/RESEARCH_FOLLOWUP_DATA_SOURCES.md:129)

### 2. PostgreSQL should be artifact-first, not justification theater

The architecture doc was strongest when it argued for JSONB artifacts, full-text search, and better concurrency headroom. It got weaker when it implied that the earlier P0 runtime issues were basically a SQLite problem. That has not been proved.

The stronger position is:
- PostgreSQL is the right first home for research artifacts
- SQLite can remain the operational store initially
- operational-table migration should be optional and load-driven

Relevant refs:
- [RESEARCH_SYSTEM_ARCHITECTURE.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_ARCHITECTURE.md:44)
- [RESEARCH_SYSTEM_ARCHITECTURE.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_ARCHITECTURE.md:86)
- [RESEARCH_SYSTEM_TECH_SPEC.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_TECH_SPEC.md:2054)

### 3. Challenge independence is not the same as provider inequality

The tech spec originally treated "different provider" as the enforcement mechanism for independent challenge. That is too rigid and not actually the thing we care about.

What we care about is:
- separate service configs
- separate prompts
- separate call lineage
- measurable disagreement quality

Different providers are a good default, not a hard law.

Relevant refs:
- [RESEARCH_SYSTEM_ARCHITECTURE.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_ARCHITECTURE.md:294)
- [RESEARCH_SYSTEM_TECH_SPEC.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_TECH_SPEC.md:811)
- [RESEARCH_SYSTEM_TECH_SPEC.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_TECH_SPEC.md:1222)

### 4. The UX destination is good, but it should not be the first landing

The UX spec is thoughtful and much better than generic dashboard filler. The risk was rollout, not design taste.

There is already an existing `/research` page, `/fragments/research` surface, `/intel` page, and idea pipeline UI in the app. For phase 1, replacing those with a brand-new multi-tab shell would create unnecessary migration churn.

The better path is:
- extend existing research/intel surfaces first
- lazy-load tabs in the dedicated page later
- poll only the visible tab
- avoid adding a new 5-second top-strip polling loop

Relevant refs:
- [RESEARCH_SYSTEM_UX_SPEC.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_UX_SPEC.md:11)
- [RESEARCH_SYSTEM_UX_SPEC.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_UX_SPEC.md:28)
- [RESEARCH_SYSTEM_UX_SPEC.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_UX_SPEC.md:1042)
- [RESEARCH_SYSTEM_UX_SPEC.md](/home/runner/workspace/ops/RESEARCH_SYSTEM_UX_SPEC.md:1125)
- [pages.py](/home/runner/workspace/app/api/pages.py:75)
- [surfaces.py](/home/runner/workspace/app/api/surfaces.py:305)

## What I Agree With

These are the strongest parts of Claude's package and I would keep them:

- Engine A / Engine B split is the right strategic boundary.
- The feasibility ranking is good. Futures trend/carry plus large-cap event/revision is the right opening pair.
- The data-sources follow-up is especially useful and grounded. It materially improves the earlier research by forcing numeric infrastructure, provenance, and session semantics into the center of the design.
- The artifact vocabulary is good and implementable.
- The governance stance is mostly right once translated out of institutional language and into operator checklists plus audit artifacts.

## Resolved Decisions

These were open during review and are now resolved:

1. Engine B subsumes the current idea pipeline, but [ideas.py](/home/runner/workspace/app/api/ideas.py) stays as a manual-submit lane during migration.
2. Minimum Engine B start dataset: daily OHLCV, basic corporate actions, and the current S&P 500 constituent list.
3. `/research` becomes the mature surface; avoid a long-term split with `/research-system`.

## Recommendation

Treat this package as approved with edits, not as something to throw away.

The shape is right. The changes I made mostly convert it from a persuasive design package into a more realistic implementation package.

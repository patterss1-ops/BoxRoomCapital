# UI Rebuild Plan (Phases 1-6)

## Objective
Transform the control plane from a dense single-page utility surface into a best-in-class operator interface for systematic trading operations.

## Targets
1. 10-second comprehension: operator can quickly answer system health, safety, and required action.
2. 2-click execution for high-frequency actions (start/stop/pause/resume/scan/reconcile).
3. Workflow-first structure: Overview -> Investigate -> Act -> Verify.
4. Progressive disclosure: advanced controls hidden by default.
5. Accessibility baseline: clear labels, keyboard navigation, visible focus, strong contrast.
6. Reduced cognitive load: fewer simultaneous controls per view, stronger visual hierarchy.

## Phase 1: UX Blueprint
- Define core operator jobs and map task flows.
- Prioritize pages around operator intent instead of backend implementation.
- Produce low-fidelity layout blueprint for:
  - Overview
  - Trading
  - Research
  - Incidents & Jobs
  - Settings

### Operator Job Flows
1. Confirm safety before enabling execution.
2. Execute common control actions quickly.
3. Investigate incidents and failed jobs.
4. Run research pipelines and inspect calibration quality.
5. Promote strategy parameter sets with auditability.

### IA Blueprint
- Global shell:
  - Persistent top status strip (state/mode/kill-switch/risk/latest incident)
  - Left or top navigation with 5 sections
  - Command palette for keyboard-first operation (`Cmd/Ctrl+K`)
- Section pages:
  - Overview: health + KPIs + quick actions + newest alerts
  - Trading: execution controls + risk controls + active positions + reconcile
  - Research: discovery/calibration + run detail + parameter lab
  - Incidents & Jobs: failures timeline + jobs table + payload drill-down
  - Settings: environment and operational docs/status

## Phase 2: Information Architecture + Navigation
- Split monolithic page into distinct routes.
- Keep common controls discoverable but context-appropriate.
- Add persistent global status strip and nav highlighting.

## Phase 3: Design System Foundation
- Introduce design tokens (color, type scale, spacing, radius, elevation).
- Standardize components:
  - KPI cards
  - Action groups
  - Data tables
  - Event timeline
  - Detail panels
  - Confirmation modals/guards
- Enforce semantic color usage for status and risk states.

## Phase 4: Screen-by-Screen Rebuild
- Recompose all pages using the new shell/components.
- Move destructive/rare controls under advanced sections.
- Add explicit callouts for risk and incident visibility.

## Phase 5: Polish + Operator Hardening
- Keyboard shortcuts and command palette actions.
- Better loading/empty/error states.
- Accessible focus handling and interaction targets.
- Subtle motion/transitions for state changes.

## Phase 6: Validation & Review
- Validate against targets with concrete checks:
  1. Can operator assess safety in <=10 seconds?
  2. Are core actions <=2 interactions?
  3. Are failures discoverable and diagnosable from one area?
  4. Are research and promotion workflows end-to-end operable without terminal commands?
  5. Is keyboard navigation viable for primary operations?
- Perform implementation review and identify remaining gaps and next iteration items.

## Deliverables
1. New UI shell with multi-page navigation.
2. Refactored templates by workflow area.
3. Updated styling system and component classes.
4. Command palette implementation.
5. Updated UX behavior for progressive disclosure and confirmation.
6. Post-implementation review summary against targets.

## Execution Review (Post-Implementation)
### Implemented Artifacts
- App shell + nav + live status strip.
- Multi-page IA:
  - `/overview`
  - `/trading`
  - `/research`
  - `/incidents`
  - `/settings`
- New command palette (`Cmd/Ctrl+K`) with navigation and action execution.
- Progressive disclosure for advanced controls (`details` sections on Trading/Research).
- Inline incident/job triage flow with research job payload drill-down.

### Target Scorecard
1. 10-second comprehension:
   - Status: **Mostly met**
   - Evidence: persistent status strip + KPI cards on Overview + reduced form density.
2. 2-click execution for core actions:
   - Status: **Met**
   - Evidence: quick actions on Overview + command palette shortcuts.
3. Workflow-first structure:
   - Status: **Met**
   - Evidence: dedicated pages by operator workflow.
4. Progressive disclosure:
   - Status: **Met**
   - Evidence: risk/manual controls moved to expandable advanced sections.
5. Accessibility baseline:
   - Status: **Partially met**
   - Evidence: visible focus states, keyboard command palette, stronger contrast.
   - Gap: no full WCAG audit yet (screen-reader semantics and contrast measurements not formally tested).
6. Reduced cognitive load:
   - Status: **Mostly met**
   - Evidence: monolith split into task-focused pages; improved hierarchy and action grouping.
   - Gap: status fragment remains data-dense and can be further condensed into role-based views.

### Validation Performed
- Python compile checks for updated backend/test modules.
- Full test suite pass (`18 passed`).
- Route/fragment smoke checks for all new pages and key fragments.

### Next UX Iteration Candidates
1. Condense status fragment into a compact overview with expandable technical details.
2. Add user-selectable “operator mode” presets (Basic / Advanced).
3. Add table-level filters/sorts and sticky headers in incidents/jobs/research tables.
4. Run formal accessibility test pass (keyboard-only walkthrough + contrast audit).

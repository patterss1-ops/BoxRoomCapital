# Research System UX Specification

**Date:** 2026-03-08 | **Status:** Ready for review
**Companion to:** `RESEARCH_SYSTEM_ARCHITECTURE.md` + `RESEARCH_SYSTEM_TECH_SPEC.md`
**Theme:** Light Bloomberg-density (bg-gray-50 body, bg-white panels, Space Grotesk + JetBrains Mono)

**Implementation note:** this is the mature destination UX. Phase 1 should extend the existing `/research` and `/intel` surfaces first, then evolve `/research` into the full research-system shell once the artifact workflow is stable.

---

## 1. Navigation & Information Architecture

### 1.1 New Sidebar Entries

Add to existing sidebar (between Research and Intel):

```
/overview          ← existing
/trading           ← existing
/research          ← existing, evolves into the mature research-system surface
/intel             ← existing (Engine B intake remains here in phase 1)
/incidents         ← existing
/settings          ← existing
```

The existing `/research` page should become the primary operator control surface for the mature research pipeline. Avoid a long-term split between `/research` and `/research-system`.

### 1.2 Page Structure

The research system gets ONE primary page (`/research`) with a tabbed layout, plus modal/slide-over panels for deep artifact inspection. This avoids scattering research UX across multiple pages.

Only the active tab should load and poll. Hidden tabs should be lazy-loaded on first activation and remain idle when not visible.

```
┌─────────────────────────────────────────────────────────────────┐
│ Top Bar (existing — add research pipeline KPIs)                 │
│ Logo | ... existing KPIs ... | ▸ 3 hypotheses active | $2.14 today │
└─────────────────────────────────────────────────────────────────┘
┌──────┬──────────────────────────────────────────────────────────┐
│      │  Research System                                         │
│  S   │  ┌─────┬──────┬─────────┬──────┬────────┬──────┐       │
│  i   │  │Dash │Eng A │ Eng B   │Costs │ Decay  │Archive│      │
│  d   │  └─────┴──────┴─────────┴──────┴────────┴──────┘       │
│  e   │                                                          │
│  b   │  Tab content area                                        │
│  a   │                                                          │
│  r   │                                                          │
└──────┴──────────────────────────────────────────────────────────┘
```

---

## 2. Tab 1: Research Dashboard

**Purpose:** Pipeline funnel overview, key metrics, active alerts, recent decisions.

### 2.1 Layout

```
grid grid-cols-1 lg:grid-cols-12 gap-2

┌──────────────────────────┬──────────────────────────────────────┐
│  Pipeline Funnel (4)     │  Active Hypotheses (8)               │
│  lg:col-span-4           │  lg:col-span-8                       │
├──────────────────────────┼──────────────────────────────────────┤
│  Engine Status (4)       │  Recent Decisions (4)  │ Alerts (4)  │
│                          │                        │             │
└──────────────────────────┴────────────────────────┴─────────────┘
```

### 2.2 Pipeline Funnel Panel

Visual funnel showing hypothesis counts at each stage:

```html
<div class="bg-white border border-gray-200 rounded-md p-1.5">
  <h3 class="text-xs font-semibold text-gray-800 uppercase tracking-wide mb-1.5">Pipeline</h3>

  <!-- Funnel stages as horizontal bar chart -->
  <div class="space-y-1">
    <!-- Each row -->
    <div class="flex items-center gap-2">
      <span class="text-[10px] text-gray-400 w-16 text-right">INTAKE</span>
      <div class="flex-1 bg-gray-100 rounded-sm h-4 relative">
        <div class="bg-blue-500/20 border border-blue-500/30 rounded-sm h-4"
             style="width: {{ (intake_count / max_count * 100) }}%">
          <span class="text-[10px] font-mono font-semibold text-blue-600 px-1">{{ intake_count }}</span>
        </div>
      </div>
    </div>
    <!-- Repeat for: HYPOTHESIS, CHALLENGE, SCORING, EXPERIMENT, SHADOW, STAGED, LIVE -->
    <!-- Color progression: blue → blue → amber → amber → emerald → emerald → emerald -->
  </div>

  <!-- Summary stats below funnel -->
  <div class="grid grid-cols-3 gap-1 mt-2 pt-1.5 border-t border-gray-200">
    <div class="text-center">
      <span class="text-[10px] text-gray-400 block">Pass Rate</span>
      <span class="font-mono text-xs font-semibold text-gray-800">{{ pass_rate }}%</span>
    </div>
    <div class="text-center">
      <span class="text-[10px] text-gray-400 block">Avg Score</span>
      <span class="font-mono text-xs font-semibold text-gray-800">{{ avg_score }}/100</span>
    </div>
    <div class="text-center">
      <span class="text-[10px] text-gray-400 block">Active</span>
      <span class="font-mono text-xs font-semibold text-gray-800">{{ active_count }}</span>
    </div>
  </div>
</div>
```

HTMX: `hx-get="/fragments/research/pipeline-funnel" hx-trigger="load, every 30s"`

### 2.3 Active Hypotheses Table

Compact table of all non-retired hypotheses, sorted by score descending:

| Column | Width | Content |
|--------|-------|---------|
| Stage | 60px | Badge (intake/hypothesis/challenge/scoring/etc.) |
| Engine | 30px | `A` or `B` badge |
| Ticker | 50px | Font-mono, linked to artifact chain |
| Edge | 80px | Edge family abbreviated |
| Direction | 30px | `↑` green or `↓` red |
| Score | 40px | `/100`, color-coded by threshold |
| Objections | 30px | Count, red if >0 |
| Age | 40px | `2d`, `5h` etc. |
| Action | 60px | Context buttons (Review / Promote / Kill) |

Clicking any row opens the **Artifact Chain Viewer** (section 8).

HTMX: `hx-get="/fragments/research/active-hypotheses" hx-trigger="load, every 15s"`

### 2.4 Engine Status Cards

Two side-by-side cards:

```
┌─────────────────────┐  ┌─────────────────────┐
│ ENGINE A             │  │ ENGINE B             │
│ Futures / Macro      │  │ Equity / Events      │
│                      │  │                      │
│ Regime: RISK_ON      │  │ Pipeline: IDLE       │
│ Sizing: 1.0×         │  │ Active: 3 hypotheses │
│ Positions: 8/12      │  │ Pending review: 1    │
│ Last run: 08:15      │  │ Last event: 11:42    │
│ Next: 08:15 tomorrow │  │ Events today: 7      │
│                      │  │                      │
│ [View Engine A →]    │  │ [View Engine B →]    │
└─────────────────────┘  └─────────────────────┘
```

### 2.5 Recent Decisions Panel

Last 10 promotion decisions with:
- Timestamp (relative)
- Ticker + direction
- Outcome badge (promote/revise/park/reject — green/amber/blue/red)
- Score
- Actor (system / operator)

### 2.6 Alerts Panel

Active alerts requiring operator attention:

```html
<!-- Decay alert -->
<div class="bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5 mb-1">
  <div class="flex items-center gap-2">
    <span class="text-amber-600 text-xs font-semibold">⚠ DECAY</span>
    <span class="font-mono text-[11px] text-gray-700">IBS_LONG</span>
    <span class="text-[10px] text-gray-400">Win rate 32% (floor: 35%)</span>
  </div>
  <div class="flex gap-1 mt-1">
    <button class="bg-amber-100 text-amber-700 text-[10px] font-semibold px-2 py-0.5 rounded"
            hx-post="/api/research/acknowledge-review" ...>
      Acknowledge
    </button>
  </div>
</div>

<!-- Kill alert -->
<div class="bg-red-50 border border-red-200 rounded-md px-2 py-1.5 mb-1">
  <div class="flex items-center gap-2">
    <span class="text-red-600 text-xs font-semibold">⛔ KILL TRIGGERED</span>
    <span class="font-mono text-[11px] text-gray-700">AAPL_PEAD</span>
    <span class="text-[10px] text-gray-400">VIX > 30 (threshold breached)</span>
  </div>
  <div class="flex gap-1 mt-1">
    <button class="bg-red-100 text-red-700 ..." hx-post="/api/research/confirm-kill">Confirm Kill</button>
    <button class="bg-gray-100 text-gray-700 ..." hx-post="/api/research/override-kill">Override</button>
  </div>
</div>
```

HTMX: `hx-get="/fragments/research/alerts" hx-trigger="load, every 10s"`

---

## 3. Tab 2: Engine A — Futures/Macro

**Purpose:** Monitor the deterministic futures pipeline. Regime state, signal heatmap, portfolio positions, rebalance status.

### 3.1 Layout

```
grid grid-cols-1 lg:grid-cols-12 gap-2

┌────────────────────┬───────────────────────────────────────────┐
│  Regime Panel (3)  │  Signal Heatmap (9)                       │
├────────────────────┼───────────────────────┬───────────────────┤
│  Portfolio (6)     │  Target vs Current (6)│                   │
├────────────────────┼───────────────────────┤                   │
│  Rebalance (6)     │  Regime Journal (6)   │                   │
└────────────────────┴───────────────────────┴───────────────────┘
```

### 3.2 Regime Panel

Large-text regime indicators with color coding:

```html
<div class="bg-white border border-gray-200 rounded-md p-1.5">
  <h3 class="text-xs font-semibold text-gray-800 uppercase tracking-wide mb-1.5">Regime</h3>

  <div class="space-y-1.5">
    <!-- Each regime dimension -->
    <div class="flex items-center justify-between">
      <span class="text-[11px] text-gray-400">Vol</span>
      <span class="inline-flex items-center px-1.5 py-0.5 rounded-sm text-[10px] font-bold uppercase
                   bg-emerald-500/15 text-emerald-600 border border-emerald-500/30">
        NORMAL
      </span>
    </div>
    <div class="flex items-center justify-between">
      <span class="text-[11px] text-gray-400">Trend</span>
      <span class="... bg-emerald-500/15 text-emerald-600 ...">STRONG</span>
    </div>
    <div class="flex items-center justify-between">
      <span class="text-[11px] text-gray-400">Carry</span>
      <span class="... bg-amber-500/15 text-amber-600 ...">FLAT</span>
    </div>
    <div class="flex items-center justify-between">
      <span class="text-[11px] text-gray-400">Macro</span>
      <span class="... bg-emerald-500/15 text-emerald-600 ...">RISK ON</span>
    </div>

    <div class="pt-1.5 border-t border-gray-200">
      <div class="flex items-center justify-between">
        <span class="text-[11px] text-gray-400">Sizing Factor</span>
        <span class="font-mono text-xs font-semibold text-gray-800">1.0×</span>
      </div>
    </div>
  </div>
</div>
```

Color mapping: risk_on/low/normal/strong/steep = emerald, transition/choppy/flat = amber, risk_off/high/crisis/reversal/inverted = red.

### 3.3 Signal Heatmap

Grid showing each instrument's signal components as colored cells:

```
           Trend  Carry  Value  Mom   Combined  Target
ES         ██     ██     ░░     ██    +0.72     +3
NQ         ██     ░░     ░░     ██    +0.45     +2
RTY        ░░     ░░     ██     ░░    +0.15     +1
ZN         ░░     ██     ██     ░░    +0.38     +4
GC         ██     ░░     ░░     ██    +0.55     +2
CL         ░░     ░░     ██     ░░    -0.20     -1
6E         ██     ██     ░░     ░░    +0.40     +2
```

Cell colors: green intensity for positive signals, red intensity for negative. White for neutral (~0). Font-mono throughout.

Implementation: HTML table with `style="background-color: rgba(16,185,129, {{ abs(score) }})"` for positive, `rgba(220,38,38, {{ abs(score) }})` for negative.

### 3.4 Portfolio: Target vs Current

Side-by-side table:

| Instrument | Current | Target | Delta | Cost Est | Trade? |
|-----------|---------|--------|-------|----------|--------|
| ES micro | +3 | +3 | 0 | — | — |
| NQ micro | +1 | +2 | +1 | $1.24 | Yes |
| ZN | +4 | +4 | 0 | — | — |

Delta column: green for buys, red for sells. Trade? column shows "Yes" only if delta passes cost filter.

### 3.5 Pending Rebalance

If a rebalance is pending, show it prominently:

```html
<div class="bg-blue-50 border border-blue-200 rounded-md p-2">
  <div class="flex items-center justify-between mb-1.5">
    <h3 class="text-xs font-semibold text-blue-800 uppercase">Rebalance Pending</h3>
    <span class="text-[10px] text-gray-400">Generated 08:15 today</span>
  </div>
  <!-- Trade list -->
  <table class="w-full text-[11px] font-mono">
    <tr><td>Buy +1 NQ micro</td><td class="text-right text-gray-400">$1.24 est cost</td></tr>
    <tr><td>Sell -2 CL micro</td><td class="text-right text-gray-400">$1.86 est cost</td></tr>
  </table>
  <div class="flex gap-1 mt-2">
    <button class="bg-blue-600 text-white text-[10px] font-semibold px-2.5 py-1 rounded"
            hx-post="/api/research/engine-a/execute-rebalance">
      Execute Rebalance
    </button>
    <button class="bg-gray-200 text-gray-700 text-[10px] font-semibold px-2.5 py-1 rounded"
            hx-post="/api/research/engine-a/dismiss-rebalance">
      Dismiss
    </button>
  </div>
</div>
```

### 3.6 Regime Journal

Scrollable list of regime transition journal entries:

```html
<div class="space-y-1.5 overflow-y-auto" style="max-height: 300px">
  <div class="border-l-2 border-amber-400 pl-2 py-0.5">
    <div class="flex items-center gap-2">
      <span class="text-[10px] text-gray-400">Mar 5, 14:30</span>
      <span class="text-[10px] font-semibold text-amber-600">NORMAL → HIGH vol</span>
    </div>
    <p class="text-[11px] text-gray-600 mt-0.5">
      VIX spiked to 28 on weaker-than-expected payrolls. Trend signals remain positive
      but sizing factor reduced to 0.75. Watch for follow-through...
    </p>
  </div>
</div>
```

---

## 4. Tab 3: Engine B — Equity Events

**Purpose:** Monitor the LLM research pipeline. Event intake, hypothesis pipeline, active experiments, operator review queue.

### 4.1 Layout

```
grid grid-cols-1 lg:grid-cols-12 gap-2

┌─────────────────────┬──────────────────────────────────────────┐
│  Intake Feed (4)    │  Hypothesis Pipeline Board (8)            │
├─────────────────────┼──────────────────────────────────────────┤
│  Review Queue (6)   │  Experiments (6)                          │
├─────────────────────┼──────────────────────────────────────────┤
│  Quick Submit (12)                                              │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 Intake Feed

Chronological feed of recent EventCards:

```html
<div class="bg-white border border-gray-200 rounded-md p-1.5">
  <h3 class="text-xs font-semibold text-gray-800 uppercase tracking-wide mb-1.5">
    Events <span class="text-gray-400 font-normal">today: {{ event_count }}</span>
  </h3>

  <div class="space-y-1 overflow-y-auto" style="max-height: 400px">
    <!-- Event card (compact) -->
    <div class="border border-gray-200 rounded-sm p-1.5 hover:bg-gray-50 cursor-pointer"
         hx-get="/api/research/artifact/{{ artifact_id }}" hx-target="#artifact-viewer"
         hx-swap="innerHTML" @click="openSlideOver()">
      <div class="flex items-center gap-1.5">
        <!-- Source badge -->
        <span class="inline-flex items-center px-1 py-0 rounded text-[9px] font-bold uppercase
                     bg-blue-500/15 text-blue-600 border border-blue-500/30">
          EARN
        </span>
        <!-- Tickers -->
        <span class="font-mono text-[11px] text-gray-800 font-semibold">AAPL</span>
        <!-- Materiality -->
        <span class="text-[10px] text-emerald-600 font-semibold">HIGH</span>
        <!-- Time -->
        <span class="text-[10px] text-gray-400 ml-auto">2h ago</span>
      </div>
      <p class="text-[10px] text-gray-500 mt-0.5 line-clamp-1">
        {{ event.claims[0] }}
      </p>
    </div>
  </div>
</div>
```

Source badges: EARN (earnings), REV (analyst revision), NEWS, SA (Seeking Alpha), X (Twitter), MAN (manual).

### 4.3 Hypothesis Pipeline Board

Kanban-style columns showing hypothesis progression:

```
┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐
│ HYPOTHESIS│CHALLENGE │ SCORING  │EXPERIMENT│ SHADOW   │ LIVE     │
│          │          │          │          │          │          │
│ ┌──────┐ │ ┌──────┐ │ ┌──────┐ │          │ ┌──────┐ │ ┌──────┐ │
│ │MSFT  │ │ │AAPL  │ │ │NVDA  │ │          │ │GOOG  │ │ │META  │ │
│ │trend │ │ │pead  │ │ │rev   │ │          │ │flow  │ │ │pead  │ │
│ │72/100│ │ │↑ 0.8 │ │ │75/100│ │          │ │3d    │ │ │12d   │ │
│ │⚠ 1obj│ │ │      │ │ │✓     │ │          │ │      │ │ │+2.1% │ │
│ └──────┘ │ └──────┘ │ └──────┘ │          │ └──────┘ │ └──────┘ │
│          │          │          │          │          │          │
│ ┌──────┐ │          │          │          │          │          │
│ │AMZN  │ │          │          │          │          │          │
│ │carry │ │          │          │          │          │          │
│ │      │ │          │          │          │          │          │
│ └──────┘ │          │          │          │          │          │
└──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘
```

Each card is a compact hypothesis summary. Clicking opens the Artifact Chain Viewer.

Card content:
- Ticker (font-mono, bold)
- Edge family (abbreviated, text-[10px])
- Score if scored (color-coded)
- Objection count (red warning if >0)
- Direction arrow (↑ green / ↓ red)
- Age or P&L if live

Implementation: `flex gap-2` with each column as a `flex-col` with `min-w-[120px]`. Cards use existing panel styling but smaller.

### 4.4 Review Queue

Hypotheses requiring operator decision. This is the main action panel:

```html
<div class="bg-white border border-gray-200 rounded-md p-1.5">
  <h3 class="text-xs font-semibold text-gray-800 uppercase tracking-wide mb-1.5">
    Needs Review <span class="bg-red-500/15 text-red-600 text-[10px] font-bold px-1 py-0 rounded ml-1">{{ count }}</span>
  </h3>

  <div class="space-y-1.5">
    <!-- Review item -->
    <div class="border border-amber-200 bg-amber-50/50 rounded-md p-2">
      <div class="flex items-center gap-2 mb-1">
        <span class="font-mono text-xs font-semibold text-gray-800">AAPL</span>
        <span class="text-[10px] text-gray-400">underreaction_revision</span>
        <span class="font-mono text-[10px] font-semibold text-emerald-600">↑ LONG</span>
        <span class="font-mono text-[11px] font-semibold text-gray-800 ml-auto">75/100</span>
      </div>

      <!-- Thesis summary (1 line) -->
      <p class="text-[10px] text-gray-600 mb-1.5">
        AAPL guided FY revenue +8% vs consensus +5%; post-guidance drift expected 3-5 days
      </p>

      <!-- Objections (red, prominent) -->
      <div class="bg-red-50 border border-red-200 rounded-sm px-1.5 py-1 mb-1.5">
        <span class="text-[10px] text-red-600 font-semibold">⚠ 1 Unresolved Objection:</span>
        <p class="text-[10px] text-red-500 mt-0.5">After-hours move (+4%) may have captured most drift</p>
      </div>

      <!-- Action buttons -->
      <div class="flex gap-1">
        <button class="bg-emerald-600 text-white text-[10px] font-semibold px-2 py-0.5 rounded"
                hx-post="/api/research/decide" hx-vals='{"chain_id":"...","outcome":"promote"}'>
          Promote
        </button>
        <button class="bg-amber-100 text-amber-700 text-[10px] font-semibold px-2 py-0.5 rounded"
                hx-post="/api/research/decide" hx-vals='{"chain_id":"...","outcome":"revise"}'>
          Revise
        </button>
        <button class="bg-blue-100 text-blue-700 text-[10px] font-semibold px-2 py-0.5 rounded"
                hx-post="/api/research/decide" hx-vals='{"chain_id":"...","outcome":"park"}'>
          Park
        </button>
        <button class="bg-red-100 text-red-700 text-[10px] font-semibold px-2 py-0.5 rounded"
                hx-post="/api/research/decide" hx-vals='{"chain_id":"...","outcome":"reject"}'>
          Reject
        </button>
        <button class="bg-gray-100 text-gray-600 text-[10px] font-semibold px-2 py-0.5 rounded ml-auto"
                hx-get="/api/research/artifact-chain/{{ chain_id }}" hx-target="#artifact-viewer">
          Full Review →
        </button>
      </div>
    </div>
  </div>
</div>
```

### 4.5 Quick Submit

Submit new content for Engine B processing:

```html
<div class="bg-white border border-gray-200 rounded-md p-2">
  <h3 class="text-xs font-semibold text-gray-800 uppercase tracking-wide mb-1.5">Submit Content</h3>
  <form hx-post="/api/research/engine-b/submit" hx-target="#submit-result" hx-swap="innerHTML"
        class="flex gap-2">
    <select name="source_class" class="bg-gray-100 border border-gray-300 text-gray-800 rounded
                                       px-2 py-1 text-[11px]">
      <option value="transcript">Transcript</option>
      <option value="news_wire">News</option>
      <option value="analyst_revision">Analyst Revision</option>
      <option value="social_curated">X / Social</option>
      <option value="filing">Filing</option>
    </select>
    <input type="text" name="content" placeholder="Paste content or URL..."
           class="flex-1 bg-gray-100 border border-gray-300 text-gray-800 rounded px-2 py-1 text-xs
                  placeholder-gray-400 focus:ring-1 focus:ring-blue-500/50">
    <button type="submit"
            class="bg-blue-600 text-white text-[10px] font-semibold px-3 py-1 rounded">
      Process →
    </button>
  </form>
  <div id="submit-result" class="mt-1"></div>
</div>
```

---

## 5. Tab 4: Model Costs

**Purpose:** Track LLM spending by model, service, engine, and day. Budget alerting.

### 5.1 Layout

```
grid grid-cols-1 lg:grid-cols-12 gap-2

┌──────────────────────────┬──────────────────────────────────────┐
│  Today's Spend (4)       │  Cost by Service (8)                 │
├──────────────────────────┼──────────────────────────────────────┤
│  Cost by Model (6)       │  Daily Trend (6)                     │
├──────────────────────────┴──────────────────────────────────────┤
│  Recent Calls Log (12)                                          │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 Today's Spend

Large KPI card:

```html
<div class="bg-white border border-gray-200 rounded-md p-2">
  <h3 class="text-xs font-semibold text-gray-800 uppercase tracking-wide mb-1">Today</h3>
  <div class="text-2xl font-mono font-bold text-gray-800">${{ today_total | round(2) }}</div>
  <div class="text-[10px] text-gray-400 mt-0.5">{{ today_calls }} calls</div>

  <div class="grid grid-cols-2 gap-1 mt-2 pt-1.5 border-t border-gray-200">
    <div>
      <span class="text-[10px] text-gray-400 block">This week</span>
      <span class="font-mono text-xs text-gray-700">${{ week_total | round(2) }}</span>
    </div>
    <div>
      <span class="text-[10px] text-gray-400 block">This month</span>
      <span class="font-mono text-xs text-gray-700">${{ month_total | round(2) }}</span>
    </div>
  </div>

  <!-- Budget bar -->
  <div class="mt-2">
    <div class="flex justify-between text-[10px] text-gray-400">
      <span>Daily budget</span>
      <span>${{ daily_budget }}</span>
    </div>
    <div class="bg-gray-100 rounded-full h-1.5 mt-0.5">
      <div class="rounded-full h-1.5 {{ 'bg-emerald-500' if pct < 75 else 'bg-amber-500' if pct < 100 else 'bg-red-500' }}"
           style="width: {{ min(pct, 100) }}%"></div>
    </div>
  </div>
</div>
```

### 5.3 Cost by Service Table

| Service | Calls | Input Tokens | Output Tokens | Cost | Avg Latency |
|---------|-------|-------------|---------------|------|------------|
| signal_extraction | 12 | 45,200 | 8,100 | $0.82 | 3.2s |
| hypothesis_formation | 8 | 12,400 | 6,200 | $0.14 | 1.8s |
| challenge_falsification | 6 | 28,600 | 9,800 | $1.24 | 4.5s |
| regime_journal | 1 | 2,100 | 800 | $0.04 | 1.1s |

### 5.4 Cost by Model Table

| Model | Provider | Calls | Cost | % of Total |
|-------|----------|-------|------|-----------|
| claude-opus-4-6 | Anthropic | 8 | $1.82 | 62% |
| gpt-5.4 | OpenAI | 6 | $0.68 | 23% |
| grok-3 | xAI | 10 | $0.32 | 11% |
| gemini-2.5-pro | Google | 3 | $0.12 | 4% |

### 5.5 Daily Trend Chart

Lightweight Charts bar chart showing daily spend over last 30 days. Color-coded: green under budget, amber near budget, red over budget.

### 5.6 Recent Calls Log

Scrollable table of last 50 model calls:

| Time | Service | Model | Artifact | In Tok | Out Tok | Cost | Latency | Status |
|------|---------|-------|----------|--------|---------|------|---------|--------|
| 11:42 | signal_extraction | claude-opus | evt_a3f2 | 4,200 | 680 | $0.11 | 3.1s | ✓ |

---

## 6. Tab 5: Decay & Health

**Purpose:** Strategy health monitoring, decay alerts, review history.

### 6.1 Layout

```
grid grid-cols-1 lg:grid-cols-12 gap-2

┌─────────────────────────────────────────────────────────────────┐
│  Strategy Health Grid (12)                                      │
├──────────────────────────┬──────────────────────────────────────┤
│  Pending Reviews (6)     │  Review History (6)                  │
└──────────────────────────┴──────────────────────────────────────┘
```

### 6.2 Strategy Health Grid

One card per active strategy, color-coded by health:

```html
<div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-1.5">
  <!-- Healthy strategy -->
  <div class="bg-white border border-emerald-200 rounded-md p-1.5">
    <div class="flex items-center justify-between">
      <span class="font-mono text-xs font-semibold text-gray-800">GTAA_ISA</span>
      <span class="inline-flex items-center px-1 py-0 rounded text-[9px] font-bold uppercase
                   bg-emerald-500/15 text-emerald-600 border border-emerald-500/30">HEALTHY</span>
    </div>
    <div class="grid grid-cols-2 gap-x-2 mt-1 text-[10px]">
      <div><span class="text-gray-400">Win rate</span> <span class="font-mono text-gray-700">58%</span></div>
      <div><span class="text-gray-400">PF</span> <span class="font-mono text-gray-700">1.8</span></div>
      <div><span class="text-gray-400">Consec L</span> <span class="font-mono text-gray-700">2</span></div>
      <div><span class="text-gray-400">30d P&L</span> <span class="font-mono text-emerald-600">+3.2%</span></div>
    </div>
  </div>

  <!-- Warning strategy -->
  <div class="bg-white border border-amber-200 rounded-md p-1.5">
    <div class="flex items-center justify-between">
      <span class="font-mono text-xs font-semibold text-gray-800">IBS_LONG</span>
      <span class="... bg-amber-500/15 text-amber-600 border-amber-500/30">WARNING</span>
    </div>
    <!-- metrics with amber highlighting on breached values -->
    <div class="grid grid-cols-2 gap-x-2 mt-1 text-[10px]">
      <div><span class="text-gray-400">Win rate</span> <span class="font-mono text-amber-600 font-semibold">32%</span></div>
      <!-- ... -->
    </div>
  </div>

  <!-- Decay strategy (red border) -->
  <div class="bg-white border border-red-200 rounded-md p-1.5">
    <!-- ... -->
  </div>
</div>
```

### 6.3 Pending Reviews

Same pattern as Dashboard Alerts panel (section 2.6) but focused on decay reviews with full acknowledge/decision flow.

### 6.4 Review History

Table of past decay review decisions:

| Date | Strategy | Trigger | Flags | Decision | Operator | Notes |
|------|----------|---------|-------|----------|----------|-------|
| Mar 6 | IBS_SHORT | decay | win_rate_floor | park | operator | Pausing until vol normalizes |

---

## 7. Tab 6: Archive & Post-Mortems

**Purpose:** Searchable archive of retired strategies and completed trades with lessons learned.

### 7.1 Layout

```
grid grid-cols-1 lg:grid-cols-12 gap-2

┌─────────────────────────────────────────────────────────────────┐
│  Search + Filters (12)                                          │
├─────────────────────────────────────────────────────────────────┤
│  Results (12)                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 Search & Filters

```html
<div class="bg-white border border-gray-200 rounded-md p-2">
  <div class="flex gap-2 flex-wrap">
    <input type="text" name="q" placeholder="Search hypotheses, tickers, lessons..."
           class="flex-1 min-w-[200px] bg-gray-100 border border-gray-300 text-gray-800 rounded
                  px-2 py-1 text-xs placeholder-gray-400 focus:ring-1 focus:ring-blue-500/50"
           hx-get="/fragments/research/archive-results" hx-trigger="keyup changed delay:300ms"
           hx-target="#archive-results" hx-include="[name='filter_engine'],[name='filter_edge'],[name='filter_outcome']">

    <select name="filter_engine" class="bg-gray-100 border border-gray-300 text-[11px] rounded px-2 py-1"
            hx-get="/fragments/research/archive-results" hx-trigger="change" hx-target="#archive-results"
            hx-include="[name='q'],[name='filter_edge'],[name='filter_outcome']">
      <option value="">All Engines</option>
      <option value="engine_a">Engine A</option>
      <option value="engine_b">Engine B</option>
    </select>

    <select name="filter_edge" class="bg-gray-100 border border-gray-300 text-[11px] rounded px-2 py-1"
            hx-get="/fragments/research/archive-results" hx-trigger="change" hx-target="#archive-results"
            hx-include="[name='q'],[name='filter_engine'],[name='filter_outcome']">
      <option value="">All Edge Families</option>
      <option value="underreaction_revision">Underreaction / Revision</option>
      <option value="trend_momentum">Trend / Momentum</option>
      <option value="carry_risk_transfer">Carry / Risk Transfer</option>
      <!-- ... -->
    </select>

    <select name="filter_outcome" class="bg-gray-100 border border-gray-300 text-[11px] rounded px-2 py-1"
            hx-get="/fragments/research/archive-results" hx-trigger="change" hx-target="#archive-results"
            hx-include="[name='q'],[name='filter_engine'],[name='filter_edge']">
      <option value="">All Outcomes</option>
      <option value="reject">Rejected</option>
      <option value="park">Parked</option>
      <option value="dead">Dead (killed)</option>
      <option value="completed">Completed (trade closed)</option>
    </select>
  </div>
</div>
```

### 7.3 Results List

Each result is an expandable card:

```html
<div id="archive-results" class="space-y-1">
  <details class="bg-white border border-gray-200 rounded-md">
    <summary class="px-2 py-1.5 cursor-pointer hover:bg-gray-50 flex items-center gap-2">
      <span class="font-mono text-xs font-semibold text-gray-800">AAPL</span>
      <span class="text-[10px] text-gray-400">underreaction_revision</span>
      <span class="inline-flex items-center px-1 py-0 rounded text-[9px] font-bold uppercase
                   bg-red-500/15 text-red-600 border border-red-500/30">DEAD</span>
      <span class="text-[10px] text-gray-400">Mar 3 → Mar 8</span>
      <span class="font-mono text-[10px] text-red-600 ml-auto">-1.8%</span>
    </summary>
    <div class="px-2 pb-2 border-t border-gray-200 pt-1.5">
      <!-- RetirementMemo contents -->
      <div class="grid grid-cols-[80px_1fr] gap-1 text-[11px]">
        <span class="text-gray-400">Trigger</span>
        <span class="text-gray-700">Drawdown exceeded 2% stop</span>
        <span class="text-gray-400">Diagnosis</span>
        <span class="text-gray-700">After-hours move had already captured the drift — thesis was correct but too late</span>
        <span class="text-gray-400">Lessons</span>
        <span class="text-gray-700">
          <ul class="list-disc list-inside">
            <li>Filter out events where AH move > 3%</li>
            <li>Reduce position size when VIX > 25</li>
          </ul>
        </span>
      </div>
      <button class="mt-1.5 bg-gray-100 text-gray-600 text-[10px] font-semibold px-2 py-0.5 rounded"
              hx-get="/api/research/artifact-chain/{{ chain_id }}" hx-target="#artifact-viewer">
        View Full Chain →
      </button>
    </div>
  </details>
</div>
```

---

## 8. Artifact Chain Viewer (Slide-Over Panel)

**Purpose:** Full deep-dive into a hypothesis chain. Shows every artifact in sequence. This is the most important review surface.

### 8.1 Trigger

Any clickable hypothesis/ticker in the UI opens the slide-over:

```html
<!-- Slide-over container (always in DOM, hidden by default) -->
<div id="artifact-slide-over"
     class="fixed inset-y-0 right-0 w-[600px] bg-white border-l border-gray-200 shadow-2xl
            transform translate-x-full transition-transform duration-200 z-50 overflow-y-auto"
     style="display: none;">

  <div class="sticky top-0 bg-white border-b border-gray-200 px-3 py-2 flex items-center justify-between z-10">
    <h2 class="text-sm font-semibold text-gray-800" id="viewer-title">Artifact Chain</h2>
    <button onclick="closeSlideOver()" class="text-gray-400 hover:text-gray-600 text-lg">×</button>
  </div>

  <div id="artifact-viewer" class="p-3">
    <!-- Content loaded via HTMX -->
  </div>
</div>

<!-- Backdrop -->
<div id="artifact-backdrop"
     class="fixed inset-0 bg-gray-500/30 backdrop-blur-sm z-40"
     style="display: none;"
     onclick="closeSlideOver()">
</div>
```

### 8.2 Chain Content

The viewer shows the complete artifact chain as a vertical timeline:

```html
<!-- Chain loaded into #artifact-viewer -->
<div class="space-y-3">

  <!-- ── EVENT CARD ────────────────────────── -->
  <section class="border border-gray-200 rounded-md">
    <div class="bg-gray-50 px-2 py-1 rounded-t-md flex items-center gap-2">
      <span class="text-[10px] font-bold text-blue-600 uppercase">Event Card</span>
      <span class="text-[10px] text-gray-400">{{ event.created_at | relative }}</span>
      <span class="inline-flex items-center px-1 py-0 rounded text-[9px] font-bold uppercase
                   bg-blue-500/15 text-blue-600 border border-blue-500/30 ml-auto">
        {{ event.source_class }}
      </span>
    </div>
    <div class="p-2 space-y-1">
      <div class="grid grid-cols-[80px_1fr] gap-1 text-[11px]">
        <span class="text-gray-400">Claims</span>
        <ul class="text-gray-700 list-disc list-inside">
          {% for claim in event.claims %}
          <li>{{ claim }}</li>
          {% endfor %}
        </ul>
        <span class="text-gray-400">Instruments</span>
        <span class="font-mono text-gray-700">{{ event.affected_instruments | join(', ') }}</span>
        <span class="text-gray-400">Prior</span>
        <span class="text-gray-700">{{ event.market_implied_prior }}</span>
        <span class="text-gray-400">Materiality</span>
        <span class="text-gray-700">{{ event.materiality }}</span>
        <span class="text-gray-400">Credibility</span>
        <span class="font-mono text-gray-700">{{ (event.source_credibility * 100) | round }}%</span>
      </div>
    </div>
  </section>

  <!-- Connecting line -->
  <div class="flex justify-center"><div class="w-px h-4 bg-gray-300"></div></div>

  <!-- ── HYPOTHESIS CARD ────────────────────── -->
  <section class="border border-gray-200 rounded-md">
    <div class="bg-gray-50 px-2 py-1 rounded-t-md flex items-center gap-2">
      <span class="text-[10px] font-bold text-blue-600 uppercase">Hypothesis</span>
      <span class="inline-flex items-center px-1 py-0 rounded text-[9px] font-bold uppercase
                   bg-blue-500/15 text-blue-600 border border-blue-500/30">
        {{ hypothesis.edge_family }}
      </span>
      <span class="font-mono text-[10px] font-semibold {{ 'text-emerald-600' if hypothesis.direction == 'long' else 'text-red-600' }} ml-auto">
        {{ '↑ LONG' if hypothesis.direction == 'long' else '↓ SHORT' }}
      </span>
    </div>
    <div class="p-2 space-y-1">
      <div class="grid grid-cols-[80px_1fr] gap-1 text-[11px]">
        <span class="text-gray-400">Thesis</span>
        <span class="text-gray-700">{{ hypothesis.variant_view }}</span>
        <span class="text-gray-400">Mechanism</span>
        <span class="text-gray-700">{{ hypothesis.mechanism }}</span>
        <span class="text-gray-400">Catalyst</span>
        <span class="text-gray-700">{{ hypothesis.catalyst }}</span>
        <span class="text-gray-400">Horizon</span>
        <span class="text-gray-700">{{ hypothesis.horizon }}</span>
        <span class="text-gray-400">Confidence</span>
        <span class="font-mono text-gray-700">{{ (hypothesis.confidence * 100) | round }}%</span>
        <span class="text-gray-400">Invalidators</span>
        <ul class="text-gray-700 list-disc list-inside">
          {% for inv in hypothesis.invalidators %}<li>{{ inv }}</li>{% endfor %}
        </ul>
      </div>
    </div>
  </section>

  <div class="flex justify-center"><div class="w-px h-4 bg-gray-300"></div></div>

  <!-- ── FALSIFICATION MEMO ────────────────── -->
  <section class="border border-gray-200 rounded-md">
    <div class="bg-gray-50 px-2 py-1 rounded-t-md flex items-center gap-2">
      <span class="text-[10px] font-bold text-amber-600 uppercase">Challenge</span>
      <span class="text-[10px] text-gray-400">by {{ falsification.challenge_model }}</span>
    </div>
    <div class="p-2 space-y-1.5">
      <div class="grid grid-cols-[80px_1fr] gap-1 text-[11px]">
        <span class="text-gray-400">Alternative</span>
        <span class="text-gray-700">{{ falsification.cheapest_alternative }}</span>
        <span class="text-gray-400">Beta leak</span>
        <span class="text-gray-700">
          {{ 'Yes — ' if falsification.beta_leakage_check.is_just_market_exposure else 'No — ' }}
          {{ falsification.beta_leakage_check.explanation }}
        </span>
        <span class="text-gray-400">Crowding</span>
        <span class="font-semibold {{ 'text-emerald-600' if falsification.crowding_check.crowding_level == 'low'
                                      else 'text-amber-600' if falsification.crowding_check.crowding_level == 'medium'
                                      else 'text-red-600' }}">
          {{ falsification.crowding_check.crowding_level | upper }}
        </span>
      </div>

      <!-- UNRESOLVED OBJECTIONS — most important element, visually prominent -->
      {% if falsification.unresolved_objections %}
      <div class="bg-red-50 border border-red-200 rounded-md px-2 py-1.5">
        <span class="text-[10px] text-red-600 font-bold uppercase">Unresolved Objections</span>
        <ul class="text-[11px] text-red-600 list-disc list-inside mt-0.5">
          {% for obj in falsification.unresolved_objections %}
          <li>{{ obj }}</li>
          {% endfor %}
        </ul>
      </div>
      {% endif %}

      {% if falsification.resolved_objections %}
      <div class="bg-emerald-50 border border-emerald-200 rounded-md px-2 py-1.5">
        <span class="text-[10px] text-emerald-600 font-bold uppercase">Resolved</span>
        <ul class="text-[11px] text-emerald-600 list-disc list-inside mt-0.5">
          {% for obj in falsification.resolved_objections %}
          <li>{{ obj }}</li>
          {% endfor %}
        </ul>
      </div>
      {% endif %}

      <!-- Prior evidence -->
      <div>
        <span class="text-[10px] text-gray-400 font-semibold uppercase">Prior Evidence</span>
        <div class="space-y-0.5 mt-0.5">
          {% for ev in falsification.prior_evidence %}
          <div class="flex items-start gap-1 text-[10px]">
            <span class="{{ 'text-emerald-600' if ev.supports_hypothesis else 'text-red-600' }}">
              {{ '✓' if ev.supports_hypothesis else '✗' }}
            </span>
            <span class="text-gray-600">{{ ev.description }}</span>
            <span class="text-gray-400 ml-auto">[{{ ev.strength }}]</span>
          </div>
          {% endfor %}
        </div>
      </div>
    </div>
  </section>

  <div class="flex justify-center"><div class="w-px h-4 bg-gray-300"></div></div>

  <!-- ── SCORING RESULT ──────────────────────── -->
  <section class="border border-gray-200 rounded-md">
    <div class="bg-gray-50 px-2 py-1 rounded-t-md flex items-center gap-2">
      <span class="text-[10px] font-bold text-gray-600 uppercase">Score</span>
      <span class="font-mono text-sm font-bold ml-auto
                   {{ 'text-emerald-600' if scoring.final_score >= 70
                      else 'text-amber-600' if scoring.final_score >= 60
                      else 'text-red-600' }}">
        {{ scoring.final_score }}/100
      </span>
    </div>
    <div class="p-2">
      <!-- Dimension breakdown as horizontal bars -->
      <div class="space-y-0.5">
        {% for dim, score in scoring.dimension_scores.items() %}
        <div class="flex items-center gap-1">
          <span class="text-[10px] text-gray-400 w-28 text-right">{{ dim | replace('_', ' ') | title }}</span>
          <div class="flex-1 bg-gray-100 rounded-sm h-3 relative">
            <div class="bg-blue-500/30 rounded-sm h-3"
                 style="width: {{ (score / DIMENSION_WEIGHTS[dim] * 100) }}%">
            </div>
          </div>
          <span class="font-mono text-[10px] text-gray-700 w-8 text-right">{{ score }}/{{ DIMENSION_WEIGHTS[dim] }}</span>
        </div>
        {% endfor %}
      </div>

      <!-- Penalties -->
      {% if scoring.penalties %}
      <div class="mt-1.5 pt-1 border-t border-gray-200">
        {% for name, val in scoring.penalties.items() %}
        <div class="flex items-center gap-1 text-[10px]">
          <span class="text-red-500">{{ val }}</span>
          <span class="text-gray-400">{{ name | replace('_', ' ') }}</span>
        </div>
        {% endfor %}
      </div>
      {% endif %}

      <!-- Outcome badge -->
      <div class="mt-1.5 pt-1 border-t border-gray-200 flex items-center gap-2">
        <span class="text-[10px] text-gray-400">Outcome</span>
        <span class="inline-flex items-center px-1.5 py-0.5 rounded-sm text-[10px] font-bold uppercase
                     {{ outcome_badge_class(scoring.outcome) }}">
          {{ scoring.outcome }}
        </span>
        <span class="text-[10px] text-gray-500 ml-1">{{ scoring.outcome_reason }}</span>
      </div>
    </div>
  </section>

  <!-- Continue with ExperimentReport, TradeSheet if they exist in chain -->
  <!-- Same pattern: section header + key-value grid -->

  <!-- ── OPERATOR DECISION ──────────────────── -->
  {% if needs_decision %}
  <section class="border-2 border-blue-300 rounded-md bg-blue-50/50 p-2">
    <h3 class="text-xs font-semibold text-blue-800 uppercase mb-1.5">Your Decision</h3>

    <form hx-post="/api/research/decide" hx-target="#decision-result">
      <input type="hidden" name="chain_id" value="{{ chain_id }}">

      <textarea name="notes" rows="2" placeholder="Notes (optional)..."
                class="w-full bg-white border border-gray-300 text-gray-800 rounded px-2 py-1 text-xs
                       placeholder-gray-400 focus:ring-1 focus:ring-blue-500/50 mb-1.5"></textarea>

      <div class="flex gap-1.5">
        <button type="submit" name="outcome" value="promote"
                class="bg-emerald-600 text-white text-[10px] font-semibold px-3 py-1 rounded flex-1">
          Promote
        </button>
        <button type="submit" name="outcome" value="revise"
                class="bg-amber-500 text-white text-[10px] font-semibold px-3 py-1 rounded flex-1">
          Revise
        </button>
        <button type="submit" name="outcome" value="park"
                class="bg-blue-500 text-white text-[10px] font-semibold px-3 py-1 rounded flex-1">
          Park
        </button>
        <button type="submit" name="outcome" value="reject"
                class="bg-red-600 text-white text-[10px] font-semibold px-3 py-1 rounded flex-1">
          Reject
        </button>
      </div>
    </form>

    <div id="decision-result" class="mt-1"></div>
  </section>
  {% endif %}

</div>
```

---

## 9. Top Bar KPI Additions

Add to existing top strip (piggyback on the existing page refresh cadence; do not add a new 5s poll loop):

```html
<!-- After existing KPIs -->
<span class="text-gray-300">|</span>
<span class="text-[11px] font-mono">
  <span class="text-gray-400">Research:</span>
  <span class="text-blue-500">{{ pipeline_active }} active</span>
  {% if pending_review > 0 %}
  <span class="text-amber-500 font-semibold animate-pulse">{{ pending_review }} review</span>
  {% endif %}
</span>
<span class="text-gray-300">|</span>
<span class="text-[11px] font-mono text-gray-400">LLM: ${{ today_cost | round(2) }}</span>
```

---

## 10. API Endpoints

### 10.1 Page Routes

```python
# app/api/pages.py evolution (phase 2 shell)

@router.get("/research", response_class=HTMLResponse)
def research_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "research_system_page.html", {})
```

### 10.2 Fragment Routes

```python
# app/api/research_fragments.py (new router)

@router.get("/fragments/research/pipeline-funnel")        # Dashboard funnel
@router.get("/fragments/research/active-hypotheses")       # Dashboard hypothesis table
@router.get("/fragments/research/engine-status")           # Dashboard engine cards
@router.get("/fragments/research/recent-decisions")        # Dashboard decision log
@router.get("/fragments/research/alerts")                  # Dashboard alerts

@router.get("/fragments/research/engine-a/regime")         # Engine A regime panel
@router.get("/fragments/research/engine-a/heatmap")        # Engine A signal heatmap
@router.get("/fragments/research/engine-a/portfolio")      # Engine A positions
@router.get("/fragments/research/engine-a/rebalance")      # Engine A pending rebalance
@router.get("/fragments/research/engine-a/journal")        # Engine A regime journal

@router.get("/fragments/research/engine-b/intake")         # Engine B event feed
@router.get("/fragments/research/engine-b/board")          # Engine B kanban board
@router.get("/fragments/research/engine-b/review-queue")   # Engine B review queue

@router.get("/fragments/research/costs/summary")           # Costs overview
@router.get("/fragments/research/costs/by-service")        # Costs by service
@router.get("/fragments/research/costs/by-model")          # Costs by model
@router.get("/fragments/research/costs/calls-log")         # Recent calls

@router.get("/fragments/research/decay/health-grid")       # Strategy health cards
@router.get("/fragments/research/decay/pending-reviews")   # Pending decay reviews
@router.get("/fragments/research/decay/review-history")    # Past reviews

@router.get("/fragments/research/archive-results")         # Archive search results
```

### 10.3 Action Endpoints

```python
# app/api/research_actions.py (new router)

@router.post("/api/research/decide")                       # Operator decision (promote/revise/park/reject)
@router.post("/api/research/acknowledge-review")           # Acknowledge decay review
@router.post("/api/research/confirm-kill")                 # Confirm kill trigger
@router.post("/api/research/override-kill")                # Override kill (with notes required)
@router.post("/api/research/engine-a/execute-rebalance")   # Execute pending rebalance
@router.post("/api/research/engine-a/dismiss-rebalance")   # Dismiss pending rebalance
@router.post("/api/research/engine-b/submit")              # Submit new content for processing
@router.get("/api/research/artifact-chain/{chain_id}")     # Fetch full chain for viewer
@router.get("/api/research/artifact/{artifact_id}")        # Fetch single artifact
```

---

## 11. HTMX Polling Budget

To avoid the P0 stability issues, all fragment polling is budgeted:

| Fragment | Interval | Priority | Notes |
|----------|----------|----------|-------|
| Pipeline funnel | 30s | Low | Slow-moving aggregate |
| Active hypotheses | 15s | Medium | Operator needs current state |
| Engine status | 15s | Medium | Position/regime awareness |
| Alerts | 10s | High | Requires fast operator response |
| Review queue | 10s | High | Time-sensitive decisions |
| Event intake feed | 15s | Medium | New events arrival |
| Kanban board | 20s | Low | Visual overview |
| Costs summary | 60s | Low | Not time-critical |
| Calls log | 30s | Low | Diagnostic |
| Health grid | 30s | Medium | Health awareness |
| Regime panel | 30s | Low | Changes infrequently |
| Signal heatmap | 30s | Low | Daily signals |
| Portfolio | 15s | Medium | Position awareness |

**Scope rule:** the table above applies only to the currently visible tab. Hidden tabs do not poll.

**Staggering:** Each fragment uses `delay:XXms` on initial load to spread requests:
```
load delay:0ms     — alerts, review queue (highest priority)
load delay:200ms   — active hypotheses, engine status
load delay:400ms   — intake feed, portfolio
load delay:600ms   — funnel, heatmap, board
load delay:800ms   — costs, journal, health
```

---

## 12. Charting Layer

Based on solo operator evidence (Carver, Alvarez, Davey), charts serve four distinct jobs — not decorative visualization:

### 12.1 Chart Types

| Chart | Job | Tab | Priority |
|-------|-----|-----|----------|
| **Symbol Chart** | Data validation: spot spikes, broken sessions, roll glitches, vendor mismatches | Engine A, Engine B | P0 |
| **Trade Replay Chart** | Show entry, exit, ranking state, and the data visible at that moment | Engine B (hypothesis detail) | P1 |
| **Portfolio Heat/Scanner** | Dense multi-symbol monitoring — Davey's RadarScreen pattern | Engine A (heatmap), Engine B (board) | P1 |
| **Regime Dashboard** | Macro/vol/trend state visualization over time | Engine A, Dashboard | P0 |
| **Futures Strip/Roll Chart** | Term structure, carry relationships, roll dates | Engine A | P1 |

### 12.2 Charting Implementation

**Library:** Lightweight JS charting (Lightweight Charts by TradingView, or Chart.js for non-financial charts). No heavy charting framework — operator needs fast diagnostic views, not a Bloomberg terminal clone.

```html
<!-- Symbol chart fragment (HTMX-loaded) -->
<div id="symbol-chart"
     hx-get="/fragments/research/chart/symbol?ticker={{ ticker }}&period=1y"
     hx-trigger="load"
     class="bg-white border border-gray-200 rounded-md p-1.5 h-64">
</div>
```

**Chart interaction model:**
- Click-to-inspect: hover shows OHLCV + indicator values + snapshot state at that bar
- Trade overlay: entry/exit markers from TradeSheet artifacts
- Regime bands: background color indicates regime state (green=risk-on, amber=transition, red=risk-off)
- Roll markers: vertical lines on futures charts showing roll dates
- Quality flags: red dots on bars with quality issues (spikes, missing data)

### 12.3 Chart Endpoints

```python
# app/api/research_charts.py (new router)

@router.get("/fragments/research/chart/symbol")        # Single-symbol OHLCV + overlays
@router.get("/fragments/research/chart/trade-replay")   # Trade entry/exit with context
@router.get("/fragments/research/chart/regime")          # Regime state over time
@router.get("/fragments/research/chart/futures-strip")   # Term structure / carry
@router.get("/fragments/research/chart/scanner")         # Multi-symbol heat scanner
```

### 12.4 Scanner View (Engine A Heatmap Enhancement)

The existing Engine A heatmap (Section 5) is enhanced to serve as a RadarScreen-style scanner:

```
┌──────────────────────────────────────────────────────────────┐
│  Scanner: 20 instruments × 5 signals                         │
│                                                              │
│  Symbol  │ Trend │ Carry │ Value │ Mom  │ Combined │ Action  │
│  ────────┼───────┼───────┼───────┼──────┼──────────┼──────── │
│  ES      │  0.8  │  0.3  │  0.1  │  0.6 │   0.55   │  HOLD  │
│  CL      │ -0.4  │  0.7  │ -0.2  │ -0.3 │   0.05   │  FLAT  │
│  GC      │  0.6  │ -0.1  │  0.5  │  0.4 │   0.42   │  LONG  │
│  ...     │       │       │       │      │          │        │
│                                                              │
│  Click row → opens symbol chart + position detail             │
└──────────────────────────────────────────────────────────────┘
```

---

## 13. Template Files

### 13.1 New Templates

| File | Type | Description |
|------|------|-------------|
| `research_system_page.html` | Page | Main research system page with tabs |
| `_research_dashboard.html` | Fragment | Dashboard tab content |
| `_research_engine_a.html` | Fragment | Engine A tab content |
| `_research_engine_b.html` | Fragment | Engine B tab content |
| `_research_costs.html` | Fragment | Costs tab content |
| `_research_decay.html` | Fragment | Decay & health tab content |
| `_research_archive.html` | Fragment | Archive tab content |
| `_research_pipeline_funnel.html` | Fragment | Funnel visualization |
| `_research_active_hypotheses.html` | Fragment | Hypothesis table |
| `_research_engine_status.html` | Fragment | Engine status cards |
| `_research_alerts.html` | Fragment | Alert cards |
| `_research_regime.html` | Fragment | Regime indicators |
| `_research_heatmap.html` | Fragment | Signal heatmap grid |
| `_research_portfolio.html` | Fragment | Position table |
| `_research_rebalance.html` | Fragment | Pending rebalance |
| `_research_journal.html` | Fragment | Regime journal feed |
| `_research_intake.html` | Fragment | Event intake feed |
| `_research_board.html` | Fragment | Kanban pipeline board |
| `_research_review_queue.html` | Fragment | Review queue with actions |
| `_research_cost_summary.html` | Fragment | Cost KPIs |
| `_research_cost_service.html` | Fragment | Cost by service table |
| `_research_cost_model.html` | Fragment | Cost by model table |
| `_research_cost_calls.html` | Fragment | Recent calls log |
| `_research_health_grid.html` | Fragment | Strategy health cards |
| `_research_decay_reviews.html` | Fragment | Pending decay reviews |
| `_research_review_history.html` | Fragment | Past review decisions |
| `_research_archive_results.html` | Fragment | Search results |
| `_research_artifact_chain.html` | Fragment | Full chain viewer content |
| `_research_chart_symbol.html` | Fragment | Symbol OHLCV chart with overlays |
| `_research_chart_replay.html` | Fragment | Trade replay chart |
| `_research_chart_regime.html` | Fragment | Regime state timeline |
| `_research_chart_strip.html` | Fragment | Futures term structure chart |
| `_research_chart_scanner.html` | Fragment | Multi-symbol scanner grid |

### 13.2 Modified Templates

| File | Change |
|------|--------|
| `base.html` | Keep `/research` nav item, add slide-over container |
| `_top_strip.html` | Add research pipeline KPIs |

---

## 14. Interaction Summary

### 14.1 Operator Workflows

**Morning check (2 minutes):**
1. Open `/research` → Dashboard tab
2. Glance at funnel, engine status, alerts
3. If alerts: acknowledge or defer
4. Switch to Engine A tab: check regime, pending rebalance
5. If rebalance pending: execute or dismiss

**Event processing (as-needed):**
1. New event arrives → appears in Engine B intake feed
2. Pipeline runs automatically
3. If hypothesis scores ≥70: appears in Review Queue
4. Operator reads chain in slide-over viewer
5. Makes 4-state decision with optional notes

**Weekly review (10 minutes):**
1. Costs tab: check spend, review cost trends
2. Decay tab: review strategy health grid
3. Archive tab: search recent retired strategies, read lessons

### 14.2 Decision Points

Every operator decision is exactly 4 buttons + optional notes:

| Button | Color | Effect |
|--------|-------|--------|
| **Promote** | Green | Advance to next pipeline stage |
| **Revise** | Amber | Send back for modification |
| **Park** | Blue | Pause — revisit later |
| **Reject** | Red | Kill — generate RetirementMemo |

No ambiguity. No free-form "what do you want to do?" dialogs.

---

## 15. Mobile Responsiveness

The research system should be usable on mobile (operator checking from phone):

- Dashboard: stack panels vertically (`grid-cols-1`)
- Engine A: regime panel full-width, heatmap scrollable horizontally
- Engine B: board scrolls horizontally, review queue stacks
- Artifact viewer: full-screen on mobile instead of slide-over
- All text sizes already `text-xs` / `text-[11px]` — readable on mobile
- Touch targets: all buttons minimum 32px tap area

```html
<!-- Mobile detection for viewer -->
<div class="hidden lg:block"> <!-- slide-over on desktop --> </div>
<div class="lg:hidden"> <!-- full-page on mobile --> </div>
```

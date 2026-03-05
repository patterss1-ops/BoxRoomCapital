# Design Tokens — Prop Terminal (Bloomberg-Density)

Canonical reference for all Tailwind CSS class combinations used across the terminal UI.
Both Claude and Codex MUST use these exact tokens — no deviations.

## Layout

| Token | Tailwind Classes |
|-------|-----------------|
| Body | `h-screen w-screen overflow-hidden bg-slate-950 text-slate-300 font-sans m-0` |
| Top Bar | `h-8 bg-slate-900 border-b border-slate-800 px-2 flex items-center` |
| Left Rail (collapsed) | `w-12 bg-slate-900 border-r border-slate-800 flex flex-col` |
| Left Rail (expanded) | `w-44 bg-slate-900 border-r border-slate-800 flex flex-col` |
| Main Grid | `grid grid-cols-[20%_1fr_20%] gap-2 p-2 h-full overflow-hidden` |
| Panel | `bg-slate-900 border border-slate-800 rounded-md p-1.5 overflow-y-auto` |

## Spacing

| Token | Value |
|-------|-------|
| Panel padding | `p-1.5` |
| Gap (grid/flex) | `gap-2` max |
| Section margin | `mb-1.5` |
| Form gap | `gap-1.5` |

## Typography

| Token | Tailwind Classes |
|-------|-----------------|
| Base text | `text-xs leading-tight` |
| Table / Log text | `text-[11px] leading-4 font-mono` |
| Data / Ticker | `font-mono text-xs text-slate-300` |
| Heading (section) | `text-xs font-semibold text-slate-200 uppercase tracking-wide` |
| Heading (panel) | `text-sm font-semibold text-slate-200` |
| Label / Muted | `text-[11px] text-slate-500` |
| Primary | `text-xs text-slate-300` |
| Bright / Emphasis | `text-white` |

## Backgrounds

| Token | Tailwind Classes | Hex |
|-------|-----------------|-----|
| Body | `bg-slate-950` | #0F111A |
| Panel / Card | `bg-slate-900` | #1A1D27 |
| Input / Inset | `bg-slate-800` | #1e293b |
| Overlay | `bg-black/50 backdrop-blur-sm` | — |

## Semantic Colors

| Token | Tailwind Classes | Usage |
|-------|-----------------|-------|
| Profit / OK | `text-emerald-400` | Running, completed, live, positive P&L |
| Loss / Critical | `text-red-500` | Failed, error, danger, negative P&L |
| Warning | `text-amber-500` | Running, retrying, staged_live |
| Brand / Active | `text-blue-500` | Active nav, links, primary actions |
| Info | `text-sky-400` | Queued, shadow, informational |

## Borders

| Token | Tailwind Classes |
|-------|-----------------|
| Default | `border-slate-800` |
| Input | `border-slate-700` |
| Subtle | `border-slate-800/50` |
| Active / Focus | `ring-1 ring-blue-500/50` |

## Panel (Card)

Standard panel container:
```
bg-slate-900 border border-slate-800 rounded-md p-1.5
```

## KPI (Top Bar inline)

```
font-mono text-xs text-slate-300
```
- Label: `text-[10px] text-slate-500 uppercase`
- Value (up): `text-emerald-400 font-semibold`
- Value (down): `text-red-500 font-semibold`

## Badge Variants

All badges share base: `inline-flex items-center px-1.5 py-0.5 rounded-sm text-[10px] font-bold uppercase tracking-wide border`

| Status | Classes |
|--------|---------|
| completed / ok | `bg-emerald-500/15 text-emerald-400 border-emerald-500/30` |
| live | `bg-emerald-500/15 text-emerald-400 border-emerald-500/30` |
| running / retrying | `bg-amber-500/15 text-amber-400 border-amber-500/30` |
| staged_live | `bg-amber-500/15 text-amber-400 border-amber-500/30` |
| failed / error / rejection | `bg-red-500/15 text-red-400 border-red-500/30` |
| queued / shadow | `bg-blue-500/15 text-blue-400 border-blue-500/30` |
| archived | `bg-slate-500/15 text-slate-400 border-slate-500/30` |

## Table

```
Header row: text-slate-500 text-[10px] uppercase tracking-wide font-semibold
Body text:  text-[11px] text-slate-300 font-mono leading-4
Row border: border-b border-slate-800/50
Cell pad:   px-1.5 py-1
```

Full table: `w-full text-[11px] text-left font-mono`
- `<th>`: `text-slate-500 text-[10px] uppercase tracking-wide font-semibold px-1.5 py-1 border-b border-slate-800`
- `<td>`: `text-slate-300 px-1.5 py-1 border-b border-slate-800/50`

## Form Inputs

```
bg-slate-800 border border-slate-700 text-slate-200 rounded px-2 py-1 text-xs
placeholder-slate-500 focus:ring-1 focus:ring-blue-500/50 focus:border-blue-500
```

## Buttons

| Variant | Classes |
|---------|---------|
| Primary | `bg-blue-600 hover:bg-blue-500 text-white font-semibold px-2.5 py-1 rounded text-xs border border-blue-500 transition-colors` |
| Danger | `bg-red-600 hover:bg-red-500 text-white font-semibold px-2.5 py-1 rounded text-xs border border-red-500 transition-colors` |
| Secondary | `bg-slate-700 hover:bg-slate-600 text-slate-200 font-semibold px-2.5 py-1 rounded text-xs border border-slate-600 transition-colors` |
| Ghost | `bg-transparent hover:bg-slate-800 text-slate-300 font-semibold px-2.5 py-1 rounded text-xs border border-slate-700 transition-colors` |
| Kill Switch | `bg-red-700 hover:bg-red-600 text-white font-bold px-3 py-1 rounded text-[10px] uppercase tracking-wider border border-red-500` |

## Row (Key-Value Pair)

```html
<div class="grid grid-cols-[110px_1fr] gap-1 items-baseline border-b border-slate-800/50 py-0.5">
  <span class="text-[11px] text-slate-500">Label</span>
  <span class="text-[11px] text-slate-300 font-mono">Value</span>
</div>
```

## Chip (Status Strip)

```
inline-flex items-center px-1.5 py-0.5 rounded-sm text-[10px] font-medium border
```
- Default: `bg-slate-800/50 text-slate-300 border-slate-700`
- OK: `bg-emerald-500/20 text-emerald-300 border-emerald-500/40`
- Warn: `bg-amber-500/20 text-amber-300 border-amber-500/40`
- Danger: `bg-red-500/20 text-red-300 border-red-500/40`

## Event List

```html
<ul class="space-y-0 overflow-y-auto">
  <li class="border-b border-slate-800/50 py-1">
    <span class="inline-block min-w-[130px] text-slate-500 font-mono text-[11px]">timestamp</span>
    <span class="font-semibold text-slate-200 text-xs">headline</span>
  </li>
</ul>
```

## Log Viewer / JSON View

```
bg-slate-950 text-slate-400 font-mono text-[11px] rounded p-2 overflow-auto max-h-full
leading-4 whitespace-pre-wrap break-words
```

## Timestamp

```
font-mono text-[11px] text-slate-500
```

## Section Header

```html
<div class="flex justify-between items-baseline gap-2 mb-1.5">
  <h3 class="text-xs font-semibold text-slate-200 uppercase tracking-wide">Title</h3>
  <span class="text-[10px] text-slate-500">Subtitle</span>
</div>
```

## Tabbed Pane

```html
<div class="flex gap-0 border-b border-slate-800 mb-1.5">
  <button class="px-2.5 py-1 text-[11px] font-semibold border-b-2 border-blue-500 text-white">Active</button>
  <button class="px-2.5 py-1 text-[11px] text-slate-500 hover:text-slate-300 border-b-2 border-transparent">Inactive</button>
</div>
```

## HTMX Loading States

Applied globally via CSS (in base.html `<style>` block):

```css
.htmx-request {
  opacity: 0.5;
  cursor: wait;
  transition: opacity 150ms ease;
}
.htmx-request button {
  pointer-events: none;
}
```

Inline spinner SVG (insert inside htmx containers):
```html
<svg class="htmx-indicator animate-spin h-3 w-3 text-slate-500 inline" viewBox="0 0 24 24" fill="none">
  <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" class="opacity-25"/>
  <path fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" class="opacity-75"/>
</svg>
```

## Intelligence Feed

```
Panel: bg-slate-900 border border-slate-800 rounded-md p-1.5
Regime badge: inline-flex items-center px-1.5 py-0.5 rounded-sm text-[10px] font-bold uppercase
Layer dot (fresh): w-1.5 h-1.5 rounded-full bg-emerald-400
Layer dot (stale): w-1.5 h-1.5 rounded-full bg-red-400
Layer dot (none):  w-1.5 h-1.5 rounded-full bg-slate-600
Layer grid: grid grid-cols-4 gap-0.5
Candidate score (high): text-emerald-400
Candidate score (mid):  text-amber-400
Candidate score (low):  text-red-400
```

## Pipeline Status

```
State badge (running): bg-emerald-500/15 text-emerald-400 border-emerald-500/30
State badge (off):     bg-slate-500/15 text-slate-400 border-slate-500/30
Inline button:         bg-transparent hover:bg-slate-800 text-slate-400 px-1 py-0.5 rounded text-[9px] border border-slate-700
DAG node result:       text-[10px] font-mono, color by status
```

## Sidebar Nav

```
Fixed left rail: w-12 hover:w-44 bg-slate-900 border-r border-slate-800
Nav icon: w-4 h-4 flex-shrink-0
Nav item: flex items-center gap-2 px-3 py-2 text-xs text-slate-400 hover:text-white hover:bg-slate-800/50
Active: text-white bg-slate-800/50 border-l-2 border-blue-500
```

## Command Palette

```
Backdrop: fixed inset-0 bg-black/50 backdrop-blur-sm z-50
Dialog: bg-slate-900 border border-slate-800 rounded-md shadow-2xl w-[min(600px,calc(100%-2rem))] mt-[15vh] mx-auto
Input: bg-slate-800 border-slate-700 text-slate-200 text-xs
Item: px-2 py-1.5 rounded text-xs cursor-pointer text-slate-300
Item active: bg-slate-800 text-white
Help: text-slate-500 text-[10px] border-t border-slate-800 px-2 py-1
```

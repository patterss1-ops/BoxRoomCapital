# Design Tokens — Prop Terminal (Light Mode, Bloomberg-Density)

Canonical reference for all Tailwind CSS class combinations used across the terminal UI.
Both Claude and Codex MUST use these exact tokens — no deviations.

## Layout

| Token | Tailwind Classes |
|-------|-----------------|
| Body | `h-screen w-screen overflow-hidden bg-gray-50 text-gray-700 font-sans m-0` |
| Top Bar | `h-8 bg-white border-b border-gray-200 px-2 flex items-center` |
| Left Rail (collapsed) | `w-12 bg-white border-r border-gray-200 flex flex-col` |
| Left Rail (expanded) | `w-44 bg-white border-r border-gray-200 flex flex-col` |
| Main Grid | `grid grid-cols-[20%_1fr_20%] gap-2 p-2 h-full overflow-hidden` |
| Panel | `bg-white border border-gray-200 rounded-md p-1.5 overflow-y-auto` |

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
| Data / Ticker | `font-mono text-xs text-gray-700` |
| Heading (section) | `text-xs font-semibold text-gray-800 uppercase tracking-wide` |
| Heading (panel) | `text-sm font-semibold text-gray-800` |
| Label / Muted | `text-[11px] text-gray-400` |
| Primary | `text-xs text-gray-700` |
| Bright / Emphasis | `text-gray-900` |

## Backgrounds

| Token | Tailwind Classes | Hex |
|-------|-----------------|-----|
| Body | `bg-gray-50` | #F9FAFB |
| Panel / Card | `bg-white` | #FFFFFF |
| Input / Inset | `bg-gray-100` | #F3F4F6 |
| Overlay | `bg-gray-500/30 backdrop-blur-sm` | — |

## Semantic Colors

| Token | Tailwind Classes | Usage |
|-------|-----------------|-------|
| Profit / OK | `text-emerald-600` | Running, completed, live, positive P&L |
| Loss / Critical | `text-red-600` | Failed, error, danger, negative P&L |
| Warning | `text-amber-600` | Running, retrying, staged_live |
| Brand / Active | `text-blue-600` | Active nav, links, primary actions |
| Info | `text-sky-600` | Queued, shadow, informational |

## Borders

| Token | Tailwind Classes |
|-------|-----------------|
| Default | `border-gray-200` |
| Input | `border-gray-300` |
| Subtle | `border-gray-200` |
| Active / Focus | `ring-1 ring-blue-500/50` |

## Panel (Card)

Standard panel container:
```
bg-white border border-gray-200 rounded-md p-1.5
```

## KPI (Top Bar inline)

```
font-mono text-xs text-gray-700
```
- Label: `text-[10px] text-gray-400 uppercase`
- Value (up): `text-emerald-600 font-semibold`
- Value (down): `text-red-600 font-semibold`

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
| archived / neutral | `bg-gray-100 text-gray-500 border-gray-300` |

## Table

```
Header row: text-gray-400 text-[10px] uppercase tracking-wide font-semibold
Body text:  text-[11px] text-gray-700 font-mono leading-4
Row border: border-b border-gray-200
Cell pad:   px-1.5 py-1
```

Full table: `w-full text-[11px] text-left font-mono`
- `<th>`: `text-gray-400 text-[10px] uppercase tracking-wide font-semibold px-1.5 py-1 border-b border-gray-200`
- `<td>`: `text-gray-700 px-1.5 py-1 border-b border-gray-200`

## Form Inputs

```
bg-gray-100 border border-gray-300 text-gray-800 rounded px-2 py-1 text-xs
placeholder-gray-400 focus:ring-1 focus:ring-blue-500/50 focus:border-blue-500
```

## Buttons

| Variant | Classes |
|---------|---------|
| Primary | `bg-blue-600 hover:bg-blue-500 text-white font-semibold px-2.5 py-1 rounded text-xs border border-blue-500 transition-colors` |
| Danger | `bg-red-600 hover:bg-red-500 text-white font-semibold px-2.5 py-1 rounded text-xs border border-red-500 transition-colors` |
| Secondary | `bg-gray-200 hover:bg-gray-300 text-gray-800 font-semibold px-2.5 py-1 rounded text-xs border border-gray-300 transition-colors` |
| Ghost | `bg-transparent hover:bg-gray-100 text-gray-700 font-semibold px-2.5 py-1 rounded text-xs border border-gray-300 transition-colors` |
| Kill Switch | `bg-red-700 hover:bg-red-600 text-white font-bold px-3 py-1 rounded text-[10px] uppercase tracking-wider border border-red-500` |

## Row (Key-Value Pair)

```html
<div class="grid grid-cols-[110px_1fr] gap-1 items-baseline border-b border-gray-200 py-0.5">
  <span class="text-[11px] text-gray-400">Label</span>
  <span class="text-[11px] text-gray-700 font-mono">Value</span>
</div>
```

## Section Header

```html
<div class="flex justify-between items-baseline gap-2 mb-1.5">
  <h3 class="text-xs font-semibold text-gray-800 uppercase tracking-wide">Title</h3>
  <span class="text-[10px] text-gray-400">Subtitle</span>
</div>
```

## Sidebar Nav

```
Fixed left rail: w-12 hover:w-44 bg-white border-r border-gray-200
Nav icon: w-4 h-4 flex-shrink-0
Nav item: flex items-center gap-2 px-3 py-2 text-xs text-gray-500 hover:text-gray-900 hover:bg-gray-100
Active: text-gray-900 bg-gray-100 border-l-2 border-blue-500
```

## Command Palette

```
Backdrop: fixed inset-0 bg-gray-500/30 backdrop-blur-sm z-50
Dialog: bg-white border border-gray-200 rounded-md shadow-2xl w-[min(600px,calc(100%-2rem))] mt-[15vh] mx-auto
Input: bg-gray-100 border-gray-300 text-gray-800 text-xs
Item: px-2 py-1.5 rounded text-xs cursor-pointer text-gray-700
Item active: bg-gray-100 text-gray-900
Help: text-gray-400 text-[10px] border-t border-gray-200 px-2 py-1
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

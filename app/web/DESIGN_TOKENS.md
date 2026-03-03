# Design Tokens — Phase N Tactical Dark Mode

Canonical reference for all Tailwind CSS class combinations used across the control plane UI.
Both Claude and Codex MUST use these exact tokens — no deviations.

## Backgrounds

| Token | Tailwind Classes | Hex |
|-------|-----------------|-----|
| Body | `bg-slate-950` | #0F111A |
| Card / Panel | `bg-slate-900` | #1A1D27 |
| Input / Inset | `bg-slate-800` | #1e293b |
| Overlay backdrop | `bg-black/50 backdrop-blur-sm` | — |

## Text

| Token | Tailwind Classes |
|-------|-----------------|
| Primary | `text-slate-300` |
| Muted / Label | `text-slate-500` |
| Heading | `text-slate-200 font-semibold` |
| Bright / Emphasis | `text-white` |
| Data / Ticker / Mono | `font-mono text-slate-300` |

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
| Active / Focus | `ring-2 ring-blue-500/50` |

## Card

Standard card container used everywhere:
```
bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-lg
```

## KPI Card

Hero metric card (overview bento):
```
bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-lg
```
- Label: `text-slate-500 text-xs uppercase tracking-wide`
- Value: `text-2xl font-bold text-white`
- Value (up): `text-2xl font-bold text-emerald-400`
- Value (down): `text-2xl font-bold text-red-500`
- Foot: `text-slate-500 text-sm`

## Badge Variants

All badges share base: `inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold uppercase tracking-wide border`

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
Header row: text-slate-500 text-xs uppercase tracking-wide font-semibold
Body text:  text-sm text-slate-300
Row border: border-b border-slate-800
Cell pad:   px-2 py-2
```

Full table: `w-full text-sm text-left`
- `<th>`: `text-slate-500 text-xs uppercase tracking-wide font-semibold px-2 py-2 border-b border-slate-800`
- `<td>`: `text-slate-300 px-2 py-2 border-b border-slate-800`

## Form Inputs

```
bg-slate-800 border border-slate-700 text-slate-200 rounded-lg px-3 py-1.5
placeholder-slate-500 focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500
```

## Buttons

| Variant | Classes |
|---------|---------|
| Primary | `bg-blue-600 hover:bg-blue-500 text-white font-semibold px-4 py-1.5 rounded-lg border border-blue-500 transition-colors` |
| Danger | `bg-red-600 hover:bg-red-500 text-white font-semibold px-4 py-1.5 rounded-lg border border-red-500 transition-colors` |
| Secondary | `bg-slate-700 hover:bg-slate-600 text-slate-200 font-semibold px-4 py-1.5 rounded-lg border border-slate-600 transition-colors` |
| Ghost | `bg-transparent hover:bg-slate-800 text-slate-300 font-semibold px-4 py-1.5 rounded-lg border border-slate-700 transition-colors` |

## Row (Key-Value Pair)

```html
<div class="grid grid-cols-[160px_1fr] gap-2 items-start border-b border-slate-800 py-1.5">
  <span class="text-slate-500 text-sm">Label</span>
  <span class="text-slate-300 text-sm">Value</span>
</div>
```

## Chip (Status Strip)

```
inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border
```
- Default: `bg-slate-800/50 text-slate-300 border-slate-700`
- OK: `bg-emerald-500/20 text-emerald-300 border-emerald-500/40`
- Warn: `bg-amber-500/20 text-amber-300 border-amber-500/40`
- Danger: `bg-red-500/20 text-red-300 border-red-500/40`

## Event List

```html
<ul class="space-y-0 max-h-[460px] overflow-auto">
  <li class="border-b border-slate-800 py-2">
    <span class="inline-block min-w-[165px] text-slate-500 font-mono text-xs">timestamp</span>
    <span class="font-semibold text-slate-200">headline</span>
    <p class="ml-[165px] mt-0.5 text-slate-500 text-sm">detail</p>
  </li>
</ul>
```

## Log Viewer / JSON View

```
bg-slate-950 text-slate-400 font-mono text-xs rounded-lg p-3 overflow-auto max-h-80
leading-tight whitespace-pre-wrap break-words
```

## Timestamp

```
font-mono text-xs text-slate-500
```

## Section Header

```html
<div class="flex justify-between items-baseline gap-4 mb-3">
  <h2 class="text-lg font-semibold text-slate-200">Title</h2>
  <p class="text-slate-500 text-sm">Description</p>
</div>
```

## Action Message (toast)

- OK: `bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 rounded-lg px-3 py-2 text-sm`
- Error: `bg-red-500/10 text-red-400 border border-red-500/30 rounded-lg px-3 py-2 text-sm`

## Details / Summary

```html
<details class="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-lg">
  <summary class="font-semibold text-slate-200 cursor-pointer">Title</summary>
  <!-- content -->
</details>
```

## HTMX Loading States

Applied globally via CSS (in base.html `<style>` block):

```css
.htmx-request {
  opacity: 0.5;
  cursor: wait;
  transition: opacity 200ms ease;
}
.htmx-request button {
  pointer-events: none;
}
```

This makes every HTMX action feel immediate — elements fade to 50% opacity during requests and revert on response. Buttons become non-interactive during loading.

## Layout Tokens

| Pattern | Classes |
|---------|---------|
| Bento grid (overview) | `grid grid-cols-1 md:grid-cols-12 gap-4` |
| Two-column | `grid grid-cols-1 lg:grid-cols-2 gap-4` |
| Three-column | `grid grid-cols-1 md:grid-cols-3 gap-4` |
| Action row | `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3` |
| Action grid (forms) | `grid grid-cols-1 lg:grid-cols-2 gap-3` |

## Sidebar Nav

```
Fixed left sidebar: w-16 hover:w-48 bg-slate-900 border-r border-slate-800
Nav item: flex items-center gap-3 px-4 py-3 text-slate-400 hover:text-white hover:bg-slate-800/50
Active: text-white bg-slate-800/50 border-l-2 border-blue-500
```

## Command Palette

```
Backdrop: fixed inset-0 bg-black/50 backdrop-blur-sm z-50
Dialog: bg-slate-900 border border-slate-800 rounded-xl shadow-2xl w-[min(760px,calc(100%-2rem))] mt-[10vh] mx-auto
Input: bg-slate-800 border-slate-700 text-slate-200
Item: px-3 py-2 rounded-lg cursor-pointer text-slate-300
Item active: bg-slate-800 text-white
Help: text-slate-500 text-xs border-t border-slate-800 px-3 py-2
```

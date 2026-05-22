# Dashboard restyle to match multiscalar.ai — design

Date: 2026-05-07
Scope: dashboard frontend (React + Vite + Tailwind v4 under `dashboard/`)

## Problem

The dashboard at `strategic-agent-games-production.up.railway.app` uses a dark, purple-accented theme. The brand landing page at `multiscalar.ai` uses a light, minimalist theme with grayscale palette, IBM Plex Mono labels, hairline borders, and an animated dot-grid canvas background. The two products feel unrelated.

## Goal

Restyle the dashboard to share the visual identity of `multiscalar.ai` while keeping the dashboard's information density and a single accent color (purple) for "Live" / active states.

## Reference design tokens (from multiscalar.ai/style.css)

- `--bg: #fafafa`
- `--bg-elevated: #ffffff`
- `--border: #e4e4e4`
- `--text: #1a1a1a`
- `--text-secondary: #555555`
- `--text-dim: #999999`
- Sans: `Inter` (300/400/500/600), Mono: `IBM Plex Mono` (300/400/500/600)
- Body weight 300, line-height 1.7
- Hairline 1px borders, minimal radius (≤2px), no shadows
- Animated dot-grid canvas at fixed `z-index: -1`, opacity 0.35

## Solution

Tailwind v4 keeps design tokens in `dashboard/src/index.css` via `@theme`. Most of the rebrand is a single-file edit; component-level Tailwind classes referencing tokens (`bg-surface`, `text-accent`, `border-border`, etc.) cascade automatically.

### 1. Token rewrite — `dashboard/src/index.css`

Replace the current `@theme` block:

```css
@theme {
  --color-bg: #fafafa;
  --color-surface: #ffffff;
  --color-surface-hover: #f3f3f3;
  --color-border: #e4e4e4;
  --color-border-light: #d4d4d4;
  --color-text: #1a1a1a;
  --color-text-muted: #777777;
  --color-accent: #6c5ce7;
  --color-accent-light: #8b7ed9;
  --color-success: #0a8f8a;
  --color-warning: #b07f00;
  --color-danger: #c43838;

  --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
  --font-mono: 'IBM Plex Mono', monospace;
}

body {
  margin: 0;
  min-height: 100vh;
  background-color: var(--color-bg);
  color: var(--color-text);
  font-family: var(--font-sans);
  font-weight: 300;
  line-height: 1.7;
}
```

Notes:
- `accent` and `accent-light` darken slightly so they remain legible on white. Pure landing palette has no accent at all, but the user explicitly chose to retain a single accent for "Live" / active state.
- `success`, `warning`, `danger` darken so they pass contrast on white.
- `text-muted` lands between `--text-secondary` (#555) and `--text-dim` (#999) as a single muted token; existing components use one `text-muted` class so we cannot easily split into two.
- Scrollbar rules in the rest of the file stay; the existing thumb tokens remap to gray on white automatically.

### 2. Fonts — `dashboard/index.html`

The file currently has:

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
```

Replace that single line with:

```html
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet" />
```

The two `preconnect` lines above stay as-is.

### 3. Animated dot-grid background

New file `dashboard/src/components/GridBackground.tsx`. Ports the canvas script at `multiscalar.ai/script.js`:
- 40px-spacing dot grid covering the viewport
- Each dot has a small random base alpha (0.08–0.12)
- On mousemove, dots within 200px of the pointer brighten and grow (alpha + radius)
- Resize listener rebuilds the dot grid
- Animation runs via `requestAnimationFrame`; cleanup cancels frame and removes listeners on unmount
- The wrapping `<canvas>` is `position: fixed; inset: 0; z-index: -1; pointer-events: none; opacity: 0.35`

Mount once near the top of `Layout.tsx` (so it persists across route changes).

Out of scope: scroll-triggered fade-in of sections (the `IntersectionObserver` block in landing's `script.js`). The dashboard is interactive, not scrollable marketing content; that animation doesn't fit.

### 4. Card component — `dashboard/src/components/Card.tsx`

- `rounded-xl` → `rounded-sm` (4px; landing uses ≤2px, but Tailwind's `rounded-sm` is the closest token without arbitrary values)
- Remove `backdrop-blur-sm bg-surface/80` → just `bg-surface`
- The `glow` prop currently applies a purple shadow. On white that reads as a faint purple halo. Keep the prop but use a tighter, lower-opacity shadow (`shadow-[0_0_12px_rgba(108,92,231,0.10)]`) so it's still a usable "active" cue without dominating.
- `border` stays as `border-border`

### 5. Header / nav — `dashboard/src/components/Layout.tsx`

- Mount `<GridBackground />` once at the top of the layout's outer container.
- Logo: change from sans semibold to mono uppercase with `tracking-[0.15em]` to match landing's logo treatment. Keep the `Swords` icon for continuity.
- Nav links: switch text to mono with `tracking-[0.05em]` and `lowercase`. Active state stays as purple accent on bg.
- Background of `<header>`: change from `bg-surface/60` to `bg-bg/80` so the blur reads against the off-white page bg, matching landing's translucent nav.

## Out of scope

- Per-page restyles (`LeaderboardPage`, `GamesPage`, `AgentsPage`, `HistoryPage`, `PlayPage`). Tokens cascade; if specific elements look off after the global swap, we revisit individually.
- Section labels in landing-style (`tiny uppercase mono`). Current pages use `h1`/`h2` headings which still work in light mode.
- Body text rewrite for tone/voice.
- Dark-mode toggle.

## Files touched

- `dashboard/src/index.css` — token rewrite + body styles
- `dashboard/index.html` — Google Fonts link
- `dashboard/src/components/GridBackground.tsx` — new
- `dashboard/src/components/Card.tsx` — radius, glow, blur
- `dashboard/src/components/Layout.tsx` — mount GridBackground, restyle header logo + nav

## Testing

UI-only change. Verification:
- `npm run build` succeeds with no TS errors
- Local build preview (`npm run preview`) shows light theme with grid dots, mono uppercase logo, mono lowercase nav
- After deploy: spot-check `/`, `/leaderboard`, `/games`, `/agents`, `/history` for legibility — flag any element that reads poorly on white for a follow-up tweak

## Risks

- Existing pages have classes like `bg-accent/20 text-accent-light` for game-id chips. On white these become light purple chips — should look fine, but eyeball after deploy.
- Status badges (`Badge` component) use `variant="success" | "warning" | "danger"`; on white the darkened tokens may need a small bg tint. If they read as plain colored text, we tweak in a follow-up.
- Canvas grid + mousemove listener add a small constant cost. Negligible at 40px spacing on typical viewports.

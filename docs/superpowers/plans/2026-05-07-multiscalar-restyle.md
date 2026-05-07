# Multiscalar Restyle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the React + Tailwind dashboard to match the visual identity of `multiscalar.ai` — light theme, IBM Plex Mono labels, hairline borders, animated dot-grid canvas background — while preserving the dashboard's purple accent for active states.

**Architecture:** Tailwind v4 stores design tokens in `dashboard/src/index.css` via `@theme`. Most of the change is a single file token swap; the rest is a new background canvas component, a tightening of the `Card` component, and a header/nav restyle in `Layout`. Per-page Tailwind classes (`bg-surface`, `text-accent`, etc.) cascade automatically.

**Tech Stack:** React 19, Vite, Tailwind CSS v4 (token-based via `@theme`), lucide-react, react-router-dom v7. No test framework — verification is `npm run build` plus visual smoke after deploy.

**Working directory for all commands:** `/Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games`

**Spec reference:** `docs/superpowers/specs/2026-05-07-multiscalar-restyle-design.md`

---

## Task 1: Token + font foundation

Rewrite design tokens to landing-style light theme and swap font imports. Everything downstream depends on this.

**Files:**
- Modify: `dashboard/src/index.css`
- Modify: `dashboard/index.html`

- [ ] **Step 1: Replace tokens in `dashboard/src/index.css`**

Replace the entire current content of `dashboard/src/index.css` with:

```css
@import "tailwindcss";

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
  -webkit-font-smoothing: antialiased;
}

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--color-border-light); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--color-text-muted); }

@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

@keyframes slide-in {
  from { opacity: 0; transform: translateX(1rem); }
  to { opacity: 1; transform: translateX(0); }
}
```

- [ ] **Step 2: Swap Google Fonts in `dashboard/index.html`**

In `dashboard/index.html`, replace this single line:

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
```

With:

```html
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet" />
```

Leave the two `<link rel="preconnect">` lines above it untouched.

- [ ] **Step 3: Type-check and build**

```bash
cd /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games/dashboard && npm run build
```

Expected: build completes with no TypeScript errors and writes `dist/`.

- [ ] **Step 4: Commit**

```bash
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games add dashboard/src/index.css dashboard/index.html
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games commit -m "style(dashboard): switch tokens and fonts to multiscalar.ai light theme"
```

---

## Task 2: Card component restyle

**Files:**
- Modify: `dashboard/src/components/Card.tsx`

- [ ] **Step 1: Replace the file content**

Replace the entire content of `dashboard/src/components/Card.tsx` with:

```tsx
import type { ReactNode } from 'react';

interface CardProps {
  children: ReactNode;
  className?: string;
  glow?: boolean;
}

export default function Card({ children, className = '', glow }: CardProps) {
  return (
    <div
      className={`rounded-sm border border-border bg-surface ${
        glow ? 'shadow-[0_0_12px_rgba(108,92,231,0.10)]' : ''
      } ${className}`}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, className = '', onClick }: { children: ReactNode; className?: string; onClick?: () => void }) {
  return <div className={`px-3 sm:px-5 py-3 sm:py-4 border-b border-border ${className}`} onClick={onClick}>{children}</div>;
}

export function CardBody({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`px-3 sm:px-5 py-3 sm:py-4 ${className}`}>{children}</div>;
}
```

Changes from original:
- `rounded-xl` → `rounded-sm`
- removed `backdrop-blur-sm` and `bg-surface/80` → just `bg-surface`
- glow shadow tightened from `0 0 20px rgba(108,92,231,0.12)` to `0 0 12px rgba(108,92,231,0.10)`
- All other code (`CardHeader`, `CardBody`, props, types) is identical

- [ ] **Step 2: Type-check and build**

```bash
cd /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games/dashboard && npm run build
```

Expected: build completes with no TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games add dashboard/src/components/Card.tsx
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games commit -m "style(dashboard): tighten Card radius/blur/glow for light theme"
```

---

## Task 3: GridBackground component (canvas dot-grid)

Port the dot-grid canvas animation from `multiscalar.ai/script.js` to a React component.

**Files:**
- Create: `dashboard/src/components/GridBackground.tsx`

- [ ] **Step 1: Create the file**

Create `dashboard/src/components/GridBackground.tsx` with this exact content:

```tsx
import { useEffect, useRef } from 'react';

const SPACING = 40;
const INFLUENCE_RADIUS = 200;

interface Dot {
  x: number;
  y: number;
  baseAlpha: number;
}

export default function GridBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let width = 0;
    let height = 0;
    let dots: Dot[] = [];
    const mouse = { x: -1000, y: -1000 };
    let frame = 0;

    const buildDots = () => {
      dots = [];
      for (let x = SPACING; x < width; x += SPACING) {
        for (let y = SPACING; y < height; y += SPACING) {
          dots.push({ x, y, baseAlpha: 0.08 + Math.random() * 0.04 });
        }
      }
    };

    const resize = () => {
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
      buildDots();
    };

    const draw = () => {
      ctx.clearRect(0, 0, width, height);
      for (const dot of dots) {
        const dx = mouse.x - dot.x;
        const dy = mouse.y - dot.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const influence = Math.max(0, 1 - dist / INFLUENCE_RADIUS);
        const alpha = dot.baseAlpha + influence * 0.25;
        const radius = 0.6 + influence * 1.2;

        ctx.beginPath();
        ctx.arc(dot.x, dot.y, radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(0, 0, 0, ${alpha})`;
        ctx.fill();
      }
      frame = requestAnimationFrame(draw);
    };

    const onResize = () => {
      cancelAnimationFrame(frame);
      resize();
      draw();
    };

    const onMouseMove = (e: MouseEvent) => {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
    };

    const onMouseLeave = () => {
      mouse.x = -1000;
      mouse.y = -1000;
    };

    resize();
    draw();

    window.addEventListener('resize', onResize);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseleave', onMouseLeave);

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseleave', onMouseLeave);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{
        position: 'fixed',
        inset: 0,
        width: '100%',
        height: '100%',
        zIndex: -1,
        pointerEvents: 'none',
        opacity: 0.35,
      }}
    />
  );
}
```

- [ ] **Step 2: Type-check and build**

```bash
cd /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games/dashboard && npm run build
```

Expected: build completes with no TypeScript errors. The component is unused at this point — that's expected; Task 4 mounts it.

- [ ] **Step 3: Commit**

```bash
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games add dashboard/src/components/GridBackground.tsx
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games commit -m "feat(dashboard): add GridBackground canvas component"
```

---

## Task 4: Layout — mount GridBackground + restyle header & nav

**Files:**
- Modify: `dashboard/src/components/Layout.tsx`

- [ ] **Step 1: Replace the file content**

Replace the entire content of `dashboard/src/components/Layout.tsx` with:

```tsx
import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { Swords, Trophy, History, Radio, Bot, BookOpen, Menu, X } from 'lucide-react';
import GridBackground from './GridBackground';

const NAV = [
  { to: '/', icon: Radio, label: 'Arena' },
  { to: '/leaderboard', icon: Trophy, label: 'Leaderboard' },
  { to: '/history', icon: History, label: 'History' },
  { to: '/agents', icon: Bot, label: 'Agents' },
  { to: '/games', icon: BookOpen, label: 'Games' },
];

export default function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const handleNav = () => setMobileOpen(false);

  return (
    <div className="min-h-screen flex flex-col">
      <GridBackground />

      {/* Header */}
      <header className="border-b border-border px-3 sm:px-6 py-3 bg-bg/80 backdrop-blur-md sticky top-0 z-50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 sm:gap-3">
            <Swords className="w-5 h-5 text-text flex-shrink-0" />
            <span className="font-mono text-xs sm:text-sm font-semibold uppercase tracking-[0.15em]">
              Strategic Agent Games
            </span>
          </div>

          {/* Desktop nav */}
          <nav className="hidden sm:flex gap-1">
            {NAV.map(({ to, icon: Icon, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  `flex items-center gap-2 px-3 py-2 rounded-sm font-mono text-xs lowercase tracking-[0.05em] transition-colors ${
                    isActive
                      ? 'bg-accent/15 text-accent'
                      : 'text-text-muted hover:text-text hover:bg-surface-hover'
                  }`
                }
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
              </NavLink>
            ))}
          </nav>

          {/* Hamburger button */}
          <button
            onClick={() => setMobileOpen(!mobileOpen)}
            className="sm:hidden p-2 rounded-sm text-text-muted hover:text-text hover:bg-surface-hover transition-colors"
            aria-label="Toggle menu"
          >
            {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
        </div>

        {/* Mobile nav dropdown */}
        {mobileOpen && (
          <nav className="sm:hidden mt-3 pt-3 border-t border-border flex flex-col gap-1">
            {NAV.map(({ to, icon: Icon, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                onClick={handleNav}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-sm font-mono text-xs lowercase tracking-[0.05em] transition-colors ${
                    isActive
                      ? 'bg-accent/15 text-accent'
                      : 'text-text-muted hover:text-text hover:bg-surface-hover'
                  }`
                }
              >
                <Icon className="w-4 h-4" />
                {label}
              </NavLink>
            ))}
          </nav>
        )}
      </header>

      {/* Content */}
      <main className="flex-1 p-3 sm:p-6 max-w-7xl mx-auto w-full">
        <Outlet />
      </main>
    </div>
  );
}
```

Changes from original:
- Import `GridBackground` from `./GridBackground`
- Mount `<GridBackground />` as the first child of the root `<div>`
- Logo: switched from `text-base sm:text-lg font-semibold tracking-tight` to `font-mono text-xs sm:text-sm font-semibold uppercase tracking-[0.15em]`
- Logo icon `Swords` color changed from `text-accent` to `text-text` (grayscale match)
- Desktop nav links: switched from `px-4 py-2 rounded-lg text-sm font-medium` to `px-3 py-2 rounded-sm font-mono text-xs lowercase tracking-[0.05em]`
- Active nav state: `text-accent-light` → `text-accent` (slightly more saturated for white bg)
- Mobile nav: same mono lowercase treatment
- Hamburger / mobile toggle uses `rounded-sm` instead of `rounded-lg`
- Header bg: `bg-surface/60` → `bg-bg/80` so it reads against the off-white page bg
- `BookOpen` icon import is preserved (already added when Games page was introduced)

- [ ] **Step 2: Type-check and build**

```bash
cd /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games/dashboard && npm run build
```

Expected: build completes with no TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games add dashboard/src/components/Layout.tsx
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games commit -m "style(dashboard): mount GridBackground and restyle header for light theme"
```

---

## Task 5: Deploy and verify

**No file changes.** Deploys current `main` HEAD via Railway and spot-checks the production site.

- [ ] **Step 1: Confirm Railway link**

```bash
cd /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games && railway status
```

Expected:
```
Project: strategic-agent-games
Environment: production
Service: strategic-agent-games
```

If "No linked project found", run:
```bash
railway link -p 553ddcfd-3456-4e71-bce1-ce4f09ff2dea -e production -s strategic-agent-games
```

- [ ] **Step 2: Push to Railway**

```bash
cd /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games && railway up --ci --detach
```

Expected: a `Build Logs:` URL is printed.

- [ ] **Step 3: Wait for build to complete**

Run with `run_in_background: true`:
```bash
until railway logs --build 2>&1 | tail -5 | grep -qE "Build time:|Build succeeded|FAILED|ERROR"; do sleep 5; done
```

Expected: loop exits when "Build time: ..." appears.

- [ ] **Step 4: Verify the new bundle is live**

```bash
curl -sf https://strategic-agent-games-production.up.railway.app/ | grep -oE 'index-[A-Za-z0-9_]+\.(js|css)'
```

Expected: a `js` and `css` filename pair. Confirm against the local build output from Task 4 — if hashes match, the new code is live.

- [ ] **Step 5: Spot-check the page**

```bash
curl -sf https://strategic-agent-games-production.up.railway.app/api/games | python3 -c "import sys,json; d=json.load(sys.stdin); print('games count:', len(d['games']))"
```

Expected: `games count: 7`. Confirms the SPA still works end-to-end.

A human visual check of `/`, `/leaderboard`, `/games` is expected — flag any element that reads poorly on white as a follow-up tweak.

- [ ] **Step 6: Push commits to GitHub**

```bash
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games push origin main
```

Expected: push succeeds.

---

## Self-review notes

- **Spec coverage:**
  - Spec §1 (token rewrite) → Task 1 Step 1
  - Spec §2 (font swap) → Task 1 Step 2
  - Spec §3 (GridBackground) → Task 3 + Task 4 Step 1 (mount)
  - Spec §4 (Card) → Task 2
  - Spec §5 (header / nav) → Task 4
- **Placeholder scan:** No TBDs. Every code block is the literal content to write. Every command is exact and runnable from the documented working directory. The "human visual check" in Task 5 Step 5 is intentional — there is no automated way to verify a visual restyle.
- **Type consistency:** Card props (`children`, `className`, `glow`) unchanged. NAV array entries unchanged in shape. GridBackground takes no props. No type churn.
- **Adaptation note:** No test framework in `dashboard/`. Per the spec, verification is `npm run build` plus production smoke checks rather than unit tests.

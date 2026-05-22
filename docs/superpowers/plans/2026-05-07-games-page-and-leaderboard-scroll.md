# Games Page + Leaderboard Scroll Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/games` page that lists all registered games with rules, and make per-game leaderboard cards uniformly sized with internal scrolling.

**Architecture:** Pure frontend changes in `dashboard/` (Vite + React 19 + react-router-dom v7 + Tailwind). One new page component + nav entry + route. One layout tweak in `LeaderboardPage`. No backend changes — `/api/games` already returns `{game_id, description, min_agents}`.

**Tech Stack:** React 19, react-router-dom v7, Tailwind CSS v4, lucide-react icons, Vite. No test framework is installed in `dashboard/`; verification is `npm run build` (TypeScript + Vite production build) plus a manual dev-server smoke check.

**Working directory for all commands:** `/Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games`

---

## Spec reference

`docs/superpowers/specs/2026-05-07-games-page-and-leaderboard-scroll-design.md`

---

## Task 1: Add Games page, route, and nav entry

**Files:**
- Create: `dashboard/src/pages/GamesPage.tsx`
- Modify: `dashboard/src/App.tsx`
- Modify: `dashboard/src/components/Layout.tsx`

- [ ] **Step 1: Create `GamesPage.tsx`**

Create `dashboard/src/pages/GamesPage.tsx` with this exact content:

```tsx
import { useEffect, useState } from 'react';
import { api, type GameInfo } from '../api/client';
import Card, { CardBody, CardHeader } from '../components/Card';
import { useToast } from '../components/Toast';
import { Users } from 'lucide-react';

export default function GamesPage() {
  const [games, setGames] = useState<GameInfo[]>([]);
  const { toast } = useToast();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.games();
        if (!cancelled) setGames(res.games);
      } catch {
        if (!cancelled) toast('Failed to load games');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [toast]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Games</h1>

      {games.length === 0 ? (
        <Card>
          <CardBody className="text-center py-12 text-text-muted text-sm">
            No games registered.
          </CardBody>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {games.map((g) => (
            <Card key={g.game_id}>
              <CardHeader>
                <span className="text-xs font-mono bg-accent/20 text-accent-light px-2 py-0.5 rounded">
                  {g.game_id}
                </span>
              </CardHeader>
              <CardBody className="space-y-3">
                <p className="text-sm text-text leading-relaxed whitespace-pre-line">
                  {g.description}
                </p>
                <div className="flex items-center gap-1.5 text-xs text-text-muted">
                  <Users className="w-3.5 h-3.5" />
                  <span>Min agents: {g.min_agents}</span>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Register route in `App.tsx`**

Edit `dashboard/src/App.tsx`. Add the import and the new `<Route>`. The full file should look like this:

```tsx
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import PlayPage from './pages/PlayPage';
import LeaderboardPage from './pages/LeaderboardPage';
import HistoryPage from './pages/HistoryPage';
import AgentsPage from './pages/AgentsPage';
import GamesPage from './pages/GamesPage';
import { ToastProvider } from './components/Toast';

export default function App() {
  return (
    <ToastProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<PlayPage />} />
            <Route path="leaderboard" element={<LeaderboardPage />} />
            <Route path="history" element={<HistoryPage />} />
            <Route path="agents" element={<AgentsPage />} />
            <Route path="games" element={<GamesPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ToastProvider>
  );
}
```

- [ ] **Step 3: Add Games nav entry in `Layout.tsx`**

Edit `dashboard/src/components/Layout.tsx`. Modify only the imports line and the `NAV` array. Result:

```tsx
import { Swords, Trophy, History, Radio, Bot, BookOpen, Menu, X } from 'lucide-react';

const NAV = [
  { to: '/', icon: Radio, label: 'Arena' },
  { to: '/leaderboard', icon: Trophy, label: 'Leaderboard' },
  { to: '/history', icon: History, label: 'History' },
  { to: '/agents', icon: Bot, label: 'Agents' },
  { to: '/games', icon: BookOpen, label: 'Games' },
];
```

Leave the rest of `Layout.tsx` untouched.

- [ ] **Step 4: Type-check and build**

Run from `/Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games/dashboard`:

```bash
npm run build
```

Expected: build completes with no TypeScript errors and writes `dist/`.

- [ ] **Step 5: Commit**

```bash
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games add \
  dashboard/src/pages/GamesPage.tsx \
  dashboard/src/App.tsx \
  dashboard/src/components/Layout.tsx
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games commit -m "feat(dashboard): add Games page with rules per game"
```

---

## Task 2: Cap leaderboard card height with internal scroll

**Files:**
- Modify: `dashboard/src/pages/LeaderboardPage.tsx` (the `<div className="overflow-x-auto">` wrapping the `<table>`, around line 61)

- [ ] **Step 1: Wrap table in fixed-height scroll container**

In `dashboard/src/pages/LeaderboardPage.tsx`, find this block (around lines 60–106):

```tsx
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
```

Replace the wrapping `<div>` so both axes can scroll inside a capped-height box:

```tsx
              ) : (
                <div className="max-h-[220px] overflow-y-auto overflow-x-auto">
                  <table className="w-full">
```

Leave everything else in the file unchanged. The table, rows, and `</div>` close already exist — only the className on this one wrapper changes.

- [ ] **Step 2: Type-check and build**

```bash
cd /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games/dashboard && npm run build
```

Expected: build completes with no TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games add dashboard/src/pages/LeaderboardPage.tsx
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games commit -m "fix(dashboard): cap per-game leaderboard cards at ~5 rows with scroll"
```

---

## Task 3: Local smoke check (dev server)

**No file changes. Visual verification before deploying.**

- [ ] **Step 1: Start dev server in background**

```bash
cd /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games/dashboard && npm run dev
```

Run with `run_in_background: true`. Expected output includes a line like `Local:   http://localhost:5173/`. Note the URL.

- [ ] **Step 2: Verify Games page renders**

In a browser (or with `curl`), visit `http://localhost:5173/games`. Expected: page title "Games"; one card per game; ultimatum/trust/dictator/public-project/bilateral-trade/first-price-auction/provision-point all visible with their description text.

Note: Vite dev server will proxy `/api/*` only if configured. If `/api/games` returns 404 in dev, that's expected — verification of data wiring happens after deploy on production. The build passing in Task 1 Step 4 already proves the types and code path compile.

- [ ] **Step 3: Verify Leaderboard cards are uniform height**

Visit `http://localhost:5173/leaderboard`. With production data this is best checked post-deploy; in dev with no data the empty-state path is exercised. Confirm at least that the page renders with no console errors.

- [ ] **Step 4: Stop the dev server**

Kill the background process started in Step 1.

---

## Task 4: Deploy to Railway production

**No file changes. Deploys current `main` HEAD via the linked Railway project.**

- [ ] **Step 1: Confirm Railway is linked to the moved repo**

```bash
cd /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games && railway status
```

Expected output:
```
Project: strategic-agent-games
Environment: production
Service: strategic-agent-games
```

If "No linked project found", run:
```bash
railway link -p 553ddcfd-3456-4e71-bce1-ce4f09ff2dea -e production -s strategic-agent-games
```

- [ ] **Step 2: Trigger fresh build & deploy**

```bash
cd /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games && railway up --ci --detach
```

Expected output: a `Build Logs:` URL is printed. Note the deployment id from that URL (the `id=...` query param).

- [ ] **Step 3: Wait for build to complete**

Use a polling loop with run_in_background to wait for build success:

```bash
until railway logs --build 2>&1 | tail -5 | grep -qE "Build time:|Build succeeded|FAILED|ERROR"; do sleep 5; done
```

Expected: loop exits when "Build time: ..." appears in build logs.

- [ ] **Step 4: Smoke-check production**

```bash
curl -sf https://strategic-agent-games-production.up.railway.app/games -o /dev/null && echo "games page OK"
curl -s https://strategic-agent-games-production.up.railway.app/api/games | python3 -c "import sys, json; data=json.load(sys.stdin); print('Games returned:', len(data['games']))"
```

Expected:
- `games page OK` (the SPA returns index.html for any route)
- `Games returned: 7`

- [ ] **Step 5: Push commits to GitHub**

```bash
git -C /Users/marcellopoliti/Coding/multiscalar/repositories/strategic-agent-games push origin main
```

Expected: push succeeds (will include the spec commit, both feature commits, and this plan if it was committed).

---

## Self-review notes

- **Spec coverage:** Task 1 covers the new Games page (spec §1). Task 2 covers the leaderboard equal-height scroll (spec §2). Tasks 3–4 cover the spec's "Testing" section (build, dev smoke, post-deploy check).
- **Placeholder scan:** No TBDs. Every code block is the literal content to write. Every command is exact and runnable from the documented working directory.
- **Type consistency:** `GameInfo` and `api.games()` already exist in `dashboard/src/api/client.ts` (verified). `Card`, `CardHeader`, `CardBody` are imported from the same path used by every other page. The `Users` and `BookOpen` icons exist in lucide-react.
- **Adaptation note:** `dashboard/` has no test framework. Per the spec, verification is `npm run build` plus dev-server and post-deploy smoke checks rather than unit tests. TDD does not apply here — there is no harness to write tests in.

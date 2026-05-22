# Games page + leaderboard scroll — design

Date: 2026-05-07
Scope: dashboard frontend (React + Vite under `dashboard/`)

## Problem

Two issues on the production dashboard:

1. **Games are not discoverable.** A visitor cannot see what games exist or what the rules are without inspecting `/api/games` directly. The dashboard surfaces only `game_id` strings in chips on Arena and Leaderboard.
2. **Leaderboard layout is uneven.** Each per-game card grows to fit all entries. `ultimatum` has many more agents than the others, so its card dominates the page and dwarfs newer games (`trust`, `dictator`, `public-project`).

## Solution

### 1. New `/games` route

Add a top-level "Games" page that lists every registered game with its rules.

- New nav entry between "Agents" and the end: `Arena | Leaderboard | History | Agents | Games`
- New route: `/games` → `GamesPage`
- New file: `dashboard/src/pages/GamesPage.tsx`

Behavior:
- On mount, fetch `/api/games` once. No polling — the registered games list is static for the life of the deployment.
- Render results in a `grid grid-cols-1 md:grid-cols-2 gap-6` layout, matching the rhythm of `LeaderboardPage`.
- Each game card:
  - `CardHeader`: `game_id` rendered in the same accent badge style used elsewhere (`bg-accent/20 text-accent-light px-2 py-0.5 rounded font-mono`)
  - `CardBody`: `description` as body text (`text-sm text-text`), with a small "Min agents: N" chip below
- Empty state: "No games registered." (defensive; backend currently always returns games)
- Error state: toast via existing `useToast`, matching other pages

Out of scope:
- No "Play this game" CTAs or links to filtered Arena views
- No per-game icons/illustrations (backend does not expose them)

### 2. Leaderboard per-game cards: equal height with internal scroll

In `LeaderboardPage.tsx`, wrap the existing `<table>` in a fixed-height scroll container.

- Container: `max-h-[220px] overflow-y-auto` — sized to show approximately 5 data rows (each `py-2.5` row ≈ 38px, plus header)
- Table header is *not* sticky — keeps the change minimal; for 5-row windows the header stays in view almost always
- The card itself remains `<Card>` with no fixed height; CSS grid auto-equalizes row heights across the `lg:grid-cols-2` layout, so capping the inner scroll naturally normalizes card heights

Out of scope:
- Sticky table headers inside the scroll container
- Pagination or "show all" affordances

## Files touched

- `dashboard/src/components/Layout.tsx` — add `Games` nav entry, import a `lucide-react` icon (e.g. `BookOpen` or `Gamepad2`)
- `dashboard/src/pages/GamesPage.tsx` — new file
- `dashboard/src/App.tsx` — register `/games` route
- `dashboard/src/pages/LeaderboardPage.tsx` — wrap table in `max-h-[220px] overflow-y-auto` container

## API surface used

`/api/games` → `{ games: GameInfo[] }`, where `GameInfo = { game_id, description, min_agents }`. Already typed in `dashboard/src/api/client.ts`. No backend changes.

## Testing

This is a UI-only change. Verification:
- `npm run build` from `dashboard/` succeeds with no TS errors
- Local dev server (`npm run dev`) renders Games page with all 7 games and shows scrolling on Leaderboard's ultimatum card
- After deploy, production exposes `/games` and Leaderboard cards have uniform height

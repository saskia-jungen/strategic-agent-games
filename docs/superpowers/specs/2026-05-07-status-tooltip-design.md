# Match status tooltip — design

Date: 2026-05-07
Scope: dashboard frontend (Badge component + HistoryPage)

## Problem

The `Match History` page renders raw backend `MatchStatus` strings (`waiting`, `running`, `finished`, `abandoned`) on a `Badge`. The label `running` reads as "currently in progress" but in the history list it actually marks matches that started and never reached `finished` — they are stalled or abandoned mid-game. Users cannot tell why a past match shows `running`.

## Solution

Surface a per-status description on hover.

### 1. `Badge` component (`dashboard/src/components/Badge.tsx`)

Add an optional `tooltip?: string` prop. When set, wrap the badge in a `relative group` span and render a CSS-hover tooltip child. Pure CSS — no JS state, no library.

Visual: dark pill (using `bg-text` + `text-bg` so it inverts the page palette), positioned above the badge, fades in on hover.

### 2. `HistoryPage` (`dashboard/src/pages/HistoryPage.tsx`)

Define a `STATUS_DESCRIPTIONS` map at module scope and pass the lookup as the `tooltip` prop on the status badge (only the status badge — not the game-id badge).

Descriptions:
- `finished` — "Match completed normally — outcome and payoffs were recorded."
- `running` — "Match started but never reached a finished state — likely abandoned or stalled mid-game."
- `waiting` — "Waiting for agents to join before the match can start."
- `abandoned` — "Match was explicitly abandoned before completion."

Falls back to the raw status string if a future status appears.

## Out of scope

- Filter dropdown in HistoryPage offers `Timeout` and `Error` values that don't exist in the backend `MatchStatus` enum. The filter never matches those rows. Tracked separately.
- Tooltips on other badges (game-id, Live, finished/error in `LiveSessionCard`) — not requested.

## Files touched

- `dashboard/src/components/Badge.tsx` — add `tooltip` prop + CSS-only hover layer
- `dashboard/src/pages/HistoryPage.tsx` — add `STATUS_DESCRIPTIONS` and pass tooltip on the status badge

## Testing

`npm run build` passes. After deploy, hover over `running` and `finished` badges in `/history` and confirm tooltip appears with the matching description.

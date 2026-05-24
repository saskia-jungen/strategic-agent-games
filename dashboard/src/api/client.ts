const BASE = '';

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, init);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function post<T>(url: string, body: unknown): Promise<T> {
  return json<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// Types
export interface AgentInfo {
  agent_id: string;
  display_name: string;
  agent_type: string;
  endpoint: string;
  supported_games: string[];
}

export interface GameInfo {
  game_id: string;
  description: string;
  min_agents: number;
  max_agents: number | null;
}

export interface MatchEvent {
  timestamp_ns: number;
  event_type: string;
  agent_id: string | null;
  data: Record<string, unknown>;
}

export interface MatchRecord {
  match_id: string;
  game_id: string;
  agent_ids: string[];
  outcome: Record<string, unknown> | null;
  status: string;
  num_turns: number;
  duration_seconds: number;
  game_params: Record<string, unknown>;
  timestamp: string;
  log?: {
    events: MatchEvent[];
    [key: string]: unknown;
  } | null;
}

export interface LeaderboardEntry {
  agent_id: string;
  display_name: string;
  agent_type: string;
  matches: number;
  avg_utility: number;
  deals?: number;
  auction_wins?: number;
}

export interface MatchSession {
  status: string;
  game_id: string;
  agent_ids: string[];
  events: MatchEvent[];
  events_total: number;
  results?: unknown[];
  error?: string;
}

export interface SessionInfo {
  session_id: string;
  player_id: string;
  token: string;
  game_id: string;
  invite_codes: string[];
  status: string;
}

export interface SessionPlayer {
  player_id: string;
  display_name: string;
}

export interface SessionListItem {
  session_id: string;
  game_id: string;
  status: string;
  num_players: number;
  slots_remaining: number;
  created_at: number;
  players: SessionPlayer[];
  invite_codes?: string[];
  events_total?: number;
}

export interface SessionEvents {
  session_id: string;
  status: string;
  game_id: string;
  players: SessionPlayer[];
  events: MatchEvent[];
  events_total: number;
}

export interface SessionState {
  status: string;
  is_my_turn?: boolean;
  waiting?: boolean;
  game_over?: boolean;
  match_id?: string;
  game_id?: string;
  agent_id?: string;
  phase?: string;
  current_turn_agent_id?: string;
  game_state?: Record<string, unknown>;
  messages?: Array<Record<string, unknown>>;
  allowed_actions?: Array<{
    action_type: string;
    description: string;
    payload_schema: Record<string, unknown>;
  }>;
  outcome?: Record<string, unknown> | null;
  error?: string | null;
  players_joined?: number;
  slots_remaining?: number;
}

// API
export const api = {
  agents: () => json<{ agents: AgentInfo[] }>('/api/agents'),
  games: () => json<{ games: GameInfo[] }>('/api/games'),
  leaderboard: (gameId?: string) =>
    json<{ leaderboard: LeaderboardEntry[] }>(`/api/leaderboard${gameId ? `?game_id=${gameId}` : ''}`),
  history: (gameId?: string, limit = 50) =>
    json<{ matches: MatchRecord[] }>(`/api/history?limit=${limit}${gameId ? `&game_id=${gameId}` : ''}`),
  dashboard: () =>
    json<{
      per_game_leaderboard: Record<string, LeaderboardEntry[]>;
      recent_matches: MatchRecord[];
      games: string[];
      registered_agents: AgentInfo[];
    }>('/api/dashboard'),
  register: (agentId: string, endpoint: string, displayName: string, supportedGames: string[]) =>
    post<{ ok: boolean }>('/api/register', {
      agent_id: agentId,
      endpoint,
      display_name: displayName,
      supported_games: supportedGames,
    }),
  unregister: (agentId: string) => post<{ ok: boolean }>('/api/unregister', { agent_id: agentId }),
  startMatch: (agentIds: string[], gameId: string, gameParams: Record<string, unknown>, maxTurns = 10) =>
    post<{ ok: boolean; session_id: string }>('/api/match', {
      agent_ids: agentIds,
      game_id: gameId,
      game_params: gameParams,
      max_turns: maxTurns,
    }),
  matchStatus: (sessionId: string, since = 0) =>
    json<MatchSession>(`/api/match/${sessionId}?since=${since}`),

  // Session / polling API
  createSession: (gameId: string, playerName: string, gameParams?: Record<string, unknown>, maxTurns = 10) =>
    post<SessionInfo & { claim_token?: string }>('/api/sessions/create', {
      game_id: gameId,
      player_name: playerName,
      claim_token: localStorage.getItem(`claim:${playerName}`) || undefined,
      game_params: gameParams,
      max_turns: maxTurns,
    }),
  joinSession: (inviteCode: string, playerName: string) =>
    post<SessionInfo & { claim_token?: string }>('/api/sessions/join', {
      invite_code: inviteCode,
      player_name: playerName,
      claim_token: localStorage.getItem(`claim:${playerName}`) || undefined,
    }),
  sessionState: (token: string) =>
    json<SessionState>(`/api/sessions/state?token=${encodeURIComponent(token)}`),
  sessionAct: (token: string, actionType: string, payload: Record<string, unknown>, messages?: Array<Record<string, unknown>>) =>
    post<{ ok: boolean }>('/api/sessions/act', {
      token,
      action_type: actionType,
      payload,
      messages,
    }),
  sessionChatSend: (token: string, content: string) =>
    post<{ ok: boolean }>('/api/sessions/chat', { token, content }),
  sessionChatSync: (token: string, index = 0) =>
    json<{ messages: Array<Record<string, unknown>>; total: number }>(
      `/api/sessions/chat/sync?token=${encodeURIComponent(token)}&index=${index}`,
    ),
  sessionList: (status?: string, gameId?: string) =>
    json<{ sessions: SessionListItem[] }>(
      `/api/sessions${status || gameId ? '?' : ''}${status ? `status=${status}` : ''}${status && gameId ? '&' : ''}${gameId ? `game_id=${gameId}` : ''}`,
    ),
  sessionEvents: (sessionId: string, since = 0) =>
    json<SessionEvents>(`/api/sessions/${sessionId}/events?since=${since}`),
};

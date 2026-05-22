import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, type MatchRecord, type MatchEvent } from '../api/client';
import Card, { CardBody } from '../components/Card';
import Badge from '../components/Badge';
import EventRow from '../components/EventRow';
import { Clock, ChevronDown, ChevronRight, MessageSquare, Zap, Filter, Users, RotateCcw, X, Search } from 'lucide-react';

const STATUS_DESCRIPTIONS: Record<string, string> = {
  finished: 'Match completed normally — outcome and payoffs were recorded.',
  running: 'Match started but never reached a finished state — likely abandoned or stalled mid-game.',
  waiting: 'Waiting for agents to join before the match can start.',
  abandoned: 'Match was explicitly abandoned before completion.',
};

export default function HistoryPage() {
  const [matches, setMatches] = useState<MatchRecord[]>([]);
  const [games, setGames] = useState<string[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [tab, setTab] = useState<'conversation' | 'outcome'>('conversation');
  const [showFilters, setShowFilters] = useState(false);

  // Filters
  const [filterGame, setFilterGame] = useState('');
  const [filterAgent, setFilterAgent] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [filterMinTurns, setFilterMinTurns] = useState('');
  const [filterMaxTurns, setFilterMaxTurns] = useState('');
  const [filterMinAgents, setFilterMinAgents] = useState('');
  const [filterSearch, setFilterSearch] = useState('');

  const refresh = useCallback(async () => {
    try {
      const [dash, hist] = await Promise.all([
        api.dashboard(),
        api.history(undefined, 200),
      ]);
      setGames(dash.games);
      setMatches(hist.matches);
    } catch {
      // handled silently — data just won't update
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Derive unique agents from all matches
  const allAgents = useMemo(() => {
    const set = new Set<string>();
    matches.forEach((m) => m.agent_ids.forEach((a) => set.add(a)));
    return Array.from(set).sort();
  }, [matches]);

  // Client-side filtering
  const filtered = useMemo(() => {
    const q = filterSearch.toLowerCase().trim();
    return matches.filter((m) => {
      if (filterGame && m.game_id !== filterGame) return false;
      if (filterAgent && !m.agent_ids.includes(filterAgent)) return false;
      if (filterStatus && m.status !== filterStatus) return false;
      if (filterMinTurns && m.num_turns < Number(filterMinTurns)) return false;
      if (filterMaxTurns && m.num_turns > Number(filterMaxTurns)) return false;
      if (filterMinAgents && m.agent_ids.length < Number(filterMinAgents)) return false;
      if (q) {
        const events: MatchEvent[] = m.log?.events ?? [];
        const hasMatch = events.some(
          (e) => e.event_type === 'message' && typeof e.data.content === 'string' &&
            e.data.content.toLowerCase().includes(q)
        );
        if (!hasMatch) return false;
      }
      return true;
    });
  }, [matches, filterGame, filterAgent, filterStatus, filterMinTurns, filterMaxTurns, filterMinAgents, filterSearch]);

  const activeFilterCount = [filterGame, filterAgent, filterStatus, filterMinTurns, filterMaxTurns, filterMinAgents, filterSearch]
    .filter(Boolean).length;

  const clearFilters = () => {
    setFilterGame('');
    setFilterAgent('');
    setFilterStatus('');
    setFilterMinTurns('');
    setFilterMaxTurns('');
    setFilterMinAgents('');
    setFilterSearch('');
  };

  const toggle = (id: string) => {
    setExpandedId(expandedId === id ? null : id);
    setTab('conversation');
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Match History</h1>
        <button
          onClick={() => setShowFilters((s) => !s)}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors border ${
            showFilters || activeFilterCount > 0
              ? 'border-accent bg-accent/10 text-accent-light'
              : 'border-border bg-surface text-text-muted hover:text-text hover:border-border-light'
          }`}
        >
          <Filter className="w-4 h-4" />
          Filters
          {activeFilterCount > 0 && (
            <span className="bg-accent text-white text-[10px] font-bold w-5 h-5 rounded-full flex items-center justify-center">
              {activeFilterCount}
            </span>
          )}
        </button>
      </div>

      {/* Filter panel */}
      {showFilters && (
        <Card>
          <CardBody className="space-y-4">
            {/* Search */}
            <div>
              <label className="block text-[11px] text-text-muted mb-1.5 uppercase tracking-wider flex items-center gap-1">
                <Search className="w-3 h-3" /> Search conversations
              </label>
              <div className="relative">
                <input
                  type="text"
                  placeholder="Search message content..."
                  value={filterSearch}
                  onChange={(e) => setFilterSearch(e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-accent placeholder:text-text-muted/50"
                />
                <Search className="w-4 h-4 text-text-muted absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                {filterSearch && (
                  <button
                    onClick={() => setFilterSearch('')}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text transition-colors"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
              {/* Game */}
              <div>
                <label className="block text-[11px] text-text-muted mb-1.5 uppercase tracking-wider">Game</label>
                <div className="relative">
                  <select
                    value={filterGame}
                    onChange={(e) => setFilterGame(e.target.value)}
                    className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm appearance-none cursor-pointer focus:outline-none focus:border-accent"
                  >
                    <option value="">All</option>
                    {games.map((g) => (
                      <option key={g} value={g}>{g}</option>
                    ))}
                  </select>
                  <ChevronDown className="w-3.5 h-3.5 text-text-muted absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                </div>
              </div>

              {/* Agent */}
              <div>
                <label className="block text-[11px] text-text-muted mb-1.5 uppercase tracking-wider">Agent</label>
                <div className="relative">
                  <select
                    value={filterAgent}
                    onChange={(e) => setFilterAgent(e.target.value)}
                    className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm appearance-none cursor-pointer focus:outline-none focus:border-accent"
                  >
                    <option value="">All</option>
                    {allAgents.map((a) => (
                      <option key={a} value={a}>{a}</option>
                    ))}
                  </select>
                  <ChevronDown className="w-3.5 h-3.5 text-text-muted absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                </div>
              </div>

              {/* Status */}
              <div>
                <label className="block text-[11px] text-text-muted mb-1.5 uppercase tracking-wider">Status</label>
                <div className="relative">
                  <select
                    value={filterStatus}
                    onChange={(e) => setFilterStatus(e.target.value)}
                    className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm appearance-none cursor-pointer focus:outline-none focus:border-accent"
                  >
                    <option value="">All</option>
                    <option value="finished">Finished</option>
                    <option value="timeout">Timeout</option>
                    <option value="error">Error</option>
                  </select>
                  <ChevronDown className="w-3.5 h-3.5 text-text-muted absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                </div>
              </div>

              {/* Min turns */}
              <div>
                <label className="block text-[11px] text-text-muted mb-1.5 uppercase tracking-wider">Min Turns</label>
                <input
                  type="number"
                  min={0}
                  placeholder="—"
                  value={filterMinTurns}
                  onChange={(e) => setFilterMinTurns(e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
                />
              </div>

              {/* Max turns */}
              <div>
                <label className="block text-[11px] text-text-muted mb-1.5 uppercase tracking-wider">Max Turns</label>
                <input
                  type="number"
                  min={0}
                  placeholder="—"
                  value={filterMaxTurns}
                  onChange={(e) => setFilterMaxTurns(e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
                />
              </div>

              {/* Min agents */}
              <div>
                <label className="block text-[11px] text-text-muted mb-1.5 uppercase tracking-wider flex items-center gap-1">
                  <Users className="w-3 h-3" /> Min Agents
                </label>
                <input
                  type="number"
                  min={2}
                  placeholder="—"
                  value={filterMinAgents}
                  onChange={(e) => setFilterMinAgents(e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
                />
              </div>
            </div>

            {/* Active filters summary + clear */}
            <div className="flex items-center justify-between pt-1">
              <span className="text-xs text-text-muted">
                {filtered.length} of {matches.length} matches
              </span>
              {activeFilterCount > 0 && (
                <button
                  onClick={clearFilters}
                  className="flex items-center gap-1.5 text-xs text-accent-light hover:text-accent transition-colors"
                >
                  <RotateCcw className="w-3 h-3" />
                  Clear all
                </button>
              )}
            </div>
          </CardBody>
        </Card>
      )}

      {/* Active filter pills (shown when panel is closed) */}
      {!showFilters && activeFilterCount > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          {filterGame && (
            <FilterPill label={`Game: ${filterGame}`} onRemove={() => setFilterGame('')} />
          )}
          {filterAgent && (
            <FilterPill label={`Agent: ${filterAgent}`} onRemove={() => setFilterAgent('')} />
          )}
          {filterStatus && (
            <FilterPill label={`Status: ${filterStatus}`} onRemove={() => setFilterStatus('')} />
          )}
          {filterMinTurns && (
            <FilterPill label={`Turns ≥ ${filterMinTurns}`} onRemove={() => setFilterMinTurns('')} />
          )}
          {filterMaxTurns && (
            <FilterPill label={`Turns ≤ ${filterMaxTurns}`} onRemove={() => setFilterMaxTurns('')} />
          )}
          {filterMinAgents && (
            <FilterPill label={`Agents ≥ ${filterMinAgents}`} onRemove={() => setFilterMinAgents('')} />
          )}
          {filterSearch && (
            <FilterPill label={`"${filterSearch}"`} onRemove={() => setFilterSearch('')} />
          )}
          <button
            onClick={clearFilters}
            className="text-[11px] text-text-muted hover:text-accent-light transition-colors ml-1"
          >
            Clear all
          </button>
        </div>
      )}

      {/* Match list */}
      {filtered.length === 0 ? (
        <Card>
          <CardBody className="text-center py-12 text-text-muted text-sm">
            {matches.length === 0 ? 'No matches played yet.' : 'No matches match the current filters.'}
          </CardBody>
        </Card>
      ) : (
        <div className="space-y-2">
          {filtered.map((m) => {
            const isExpanded = expandedId === m.match_id;
            const logEvents: MatchEvent[] = m.log?.events ?? [];
            const conversationEvents = logEvents.filter(
              (e) => e.event_type === 'message' || e.event_type === 'action' || e.event_type === 'match_end'
            );
            const msgCount = logEvents.filter((e) => e.event_type === 'message').length;
            const actionCount = logEvents.filter((e) => e.event_type === 'action').length;

            return (
              <Card key={m.match_id}>
                <button
                  onClick={() => toggle(m.match_id)}
                  className="w-full text-left px-3 sm:px-5 py-3 sm:py-3.5 flex items-center gap-2 sm:gap-4 hover:bg-surface-hover transition-colors rounded-xl"
                >
                  <ChevronRight
                    className={`w-4 h-4 text-text-muted flex-shrink-0 transition-transform ${
                      isExpanded ? 'rotate-90' : ''
                    }`}
                  />
                  <div className="flex-1 min-w-0 flex items-center gap-2 sm:gap-3 flex-wrap">
                    <Badge variant="accent">{m.game_id}</Badge>
                    <span className="text-sm font-medium truncate max-w-[120px] sm:max-w-none">{m.agent_ids.join(' vs ')}</span>
                    <Badge
                      variant={m.status === 'finished' ? 'success' : 'warning'}
                      tooltip={STATUS_DESCRIPTIONS[m.status] ?? m.status}
                    >
                      {m.status}
                    </Badge>
                  </div>
                  <div className="hidden sm:flex items-center gap-4 text-xs text-text-muted flex-shrink-0">
                    {msgCount > 0 && (
                      <span className="flex items-center gap-1">
                        <MessageSquare className="w-3 h-3" />
                        {msgCount}
                      </span>
                    )}
                    {actionCount > 0 && (
                      <span className="flex items-center gap-1">
                        <Zap className="w-3 h-3" />
                        {actionCount}
                      </span>
                    )}
                    <span>{m.num_turns} turns</span>
                    <span className="flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      {m.duration_seconds.toFixed(1)}s
                    </span>
                  </div>
                </button>

                {isExpanded && (
                  <div className="border-t border-border">
                    <div className="flex gap-0 border-b border-border">
                      <button
                        onClick={() => setTab('conversation')}
                        className={`px-5 py-2.5 text-xs font-medium transition-colors ${
                          tab === 'conversation'
                            ? 'text-accent-light border-b-2 border-accent'
                            : 'text-text-muted hover:text-text'
                        }`}
                      >
                        Conversation
                      </button>
                      <button
                        onClick={() => setTab('outcome')}
                        className={`px-5 py-2.5 text-xs font-medium transition-colors ${
                          tab === 'outcome'
                            ? 'text-accent-light border-b-2 border-accent'
                            : 'text-text-muted hover:text-text'
                        }`}
                      >
                        Outcome & Params
                      </button>
                    </div>

                    {tab === 'conversation' ? (
                      <div className="max-h-[400px] overflow-y-auto p-4 space-y-2">
                        {conversationEvents.length === 0 ? (
                          <p className="text-xs text-text-muted text-center py-6">No conversation log available.</p>
                        ) : (
                          conversationEvents.map((ev, i) => (
                            <EventRow key={i} event={ev} />
                          ))
                        )}
                      </div>
                    ) : (
                      <div className="p-5 space-y-3">
                        <p className="text-xs text-text-muted font-mono">ID: {m.match_id}</p>
                        {m.outcome && (
                          <div className="bg-bg rounded-lg p-3">
                            <p className="text-xs text-text-muted mb-1 uppercase tracking-wider">Outcome</p>
                            <pre className="text-xs font-mono text-text overflow-x-auto whitespace-pre-wrap">
                              {JSON.stringify(m.outcome, null, 2)}
                            </pre>
                          </div>
                        )}
                        {m.game_params && Object.keys(m.game_params).length > 0 && (
                          <div className="bg-bg rounded-lg p-3">
                            <p className="text-xs text-text-muted mb-1 uppercase tracking-wider">Game Parameters</p>
                            <pre className="text-xs font-mono text-text">
                              {JSON.stringify(m.game_params, null, 2)}
                            </pre>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

function FilterPill({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1.5 bg-accent/10 border border-accent/30 text-accent-light text-[11px] font-medium px-2.5 py-1 rounded-full">
      {label}
      <button onClick={onRemove} className="hover:text-white transition-colors">
        <X className="w-3 h-3" />
      </button>
    </span>
  );
}

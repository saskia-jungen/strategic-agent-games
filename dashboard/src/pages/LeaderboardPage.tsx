import { useCallback, useEffect, useState } from 'react';
import { api, type LeaderboardEntry } from '../api/client';
import Card, { CardBody, CardHeader } from '../components/Card';
import { useToast } from '../components/Toast';
import { Trophy } from 'lucide-react';

export default function LeaderboardPage() {
  const [games, setGames] = useState<string[]>([]);
  const [boards, setBoards] = useState<Record<string, LeaderboardEntry[]>>({});
  const { toast } = useToast();

  const fetchAll = useCallback(async () => {
    try {
      const dash = await api.dashboard();
      setGames(dash.games);
      setBoards(dash.per_game_leaderboard);
    } catch {
      toast('Failed to load leaderboard');
    }
  }, [toast]);

  useEffect(() => {
    fetchAll();
    const timer = setInterval(fetchAll, 5000);
    return () => clearInterval(timer);
  }, [fetchAll]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Leaderboard</h1>

      {games.length === 0 && (
        <Card>
          <CardBody className="text-center py-12 text-text-muted text-sm">
            No games available.
          </CardBody>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {games.map((gameId) => {
          const entries = boards[gameId] || [];
          const isAuction = gameId === 'first-price-auction';
          return (
            <Card key={gameId}>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono bg-accent/20 text-accent-light px-2 py-0.5 rounded">
                    {gameId}
                  </span>
                  {entries.length > 0 && (
                    <span className="text-xs text-text-muted">{entries.length} agent{entries.length !== 1 ? 's' : ''}</span>
                  )}
                </div>
              </CardHeader>
              {entries.length === 0 ? (
                <CardBody className="text-center py-8 text-text-muted text-sm">
                  No matches played yet.
                </CardBody>
              ) : (
                <div className="max-h-[220px] overflow-y-auto overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-border text-xs text-text-muted uppercase tracking-wider">
                        <th className="text-left px-2 sm:px-4 py-2.5 w-10">#</th>
                        <th className="text-left px-2 sm:px-4 py-2.5">Agent</th>
                        <th className="text-right px-2 sm:px-4 py-2.5">Matches</th>
                        <th className="text-right px-2 sm:px-4 py-2.5">
                          {isAuction ? 'Wins' : 'Deals'}
                        </th>
                        <th className="text-right px-2 sm:px-4 py-2.5">Avg Utility</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entries.map((e, i) => (
                        <tr
                          key={e.agent_id}
                          className="border-b border-border/50 hover:bg-surface-hover transition-colors"
                        >
                          <td className="px-2 sm:px-4 py-2.5">
                            {i === 0 ? (
                              <Trophy className="w-4 h-4 text-warning" />
                            ) : (
                              <span className="text-sm text-text-muted">{i + 1}</span>
                            )}
                          </td>
                          <td className="px-4 py-2.5 font-medium text-sm">
                            {e.display_name || e.agent_id}
                            {e.agent_type && e.agent_type !== 'player' && (
                              <span className="ml-2 text-xs text-text-muted">({e.agent_type})</span>
                            )}
                          </td>
                          <td className="px-4 py-2.5 text-right text-sm">{e.matches}</td>
                          <td className="px-4 py-2.5 text-right text-sm text-success">
                            {isAuction ? (e.auction_wins ?? 0) : (e.deals ?? 0)}
                          </td>
                          <td className="px-4 py-2.5 text-right text-sm font-mono">
                            <span className={e.avg_utility >= 0 ? 'text-success' : 'text-danger'}>
                              {e.avg_utility.toFixed(2)}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}

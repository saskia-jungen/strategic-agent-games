import { useEffect, useState } from 'react';
import { api, type GameInfo } from '../api/client';
import Card, { CardBody, CardHeader } from '../components/Card';
import { useToast } from '../components/Toast';
import { Users } from 'lucide-react';

function formatPlayerCount(min: number, max: number | null): string {
  if (max === null) return `${min}+ players`;
  if (max === min) return `${min} players`;
  return `${min}–${max} players`;
}

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
                  <span>{formatPlayerCount(g.min_agents, g.max_agents)}</span>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

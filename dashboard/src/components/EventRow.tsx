import type { MatchEvent } from '../api/client';
import { MessageSquare, Zap, CheckCircle, XCircle } from 'lucide-react';

export default function EventRow({ event }: { event: MatchEvent }) {
  const { event_type, agent_id, data } = event;

  if (event_type === 'message') {
    return (
      <div className="flex gap-3 items-start">
        <MessageSquare className="w-4 h-4 mt-0.5 text-accent-light flex-shrink-0" />
        <div className="min-w-0">
          <span className="text-xs font-semibold text-accent-light">{agent_id}</span>
          <p className="text-sm text-text mt-0.5 break-words">{data.content as string}</p>
          {data.scope === 'private' && (
            <span className="text-[10px] text-text-muted">
              (private to {(data.to_agent_ids as string[])?.join(', ')})
            </span>
          )}
        </div>
      </div>
    );
  }

  if (event_type === 'action') {
    const ok = data.ok as boolean;
    const payload = (data.payload as Record<string, unknown>) || {};
    const redacted = Array.isArray(payload._redacted) ? (payload._redacted as string[]) : [];
    const payloadEntries = Object.entries(payload).filter(([key]) => key !== '_redacted');
    const prettyPayload = payloadEntries
      .map(([key, value]) => {
        if (redacted.includes(key)) return `${key}: [private]`;
        if (typeof value === 'string') return `${key}: ${value}`;
        if (typeof value === 'number' || typeof value === 'boolean') return `${key}: ${value}`;
        return `${key}: ${JSON.stringify(value)}`;
      })
      .join(', ');
    return (
      <div className="flex gap-3 items-start">
        <Zap className={`w-4 h-4 mt-0.5 flex-shrink-0 ${ok ? 'text-success' : 'text-danger'}`} />
        <div className="min-w-0">
          <span className="text-xs font-semibold text-text-muted">{agent_id}</span>
          <span className="text-xs text-text-muted mx-1.5">&rarr;</span>
          <span className={`text-xs font-mono ${ok ? 'text-success' : 'text-danger'}`}>
            {data.action_type as string}
          </span>
          {payloadEntries.length > 0 ? (
            <span className="text-[11px] text-text-muted ml-2 font-mono break-words">
              {prettyPayload}
            </span>
          ) : null}
          {!ok && data.error_detail ? (
            <p className="text-xs text-danger mt-0.5">{String(data.error_detail)}</p>
          ) : null}
        </div>
      </div>
    );
  }

  if (event_type === 'match_end') {
    const outcome = data.outcome as Record<string, unknown> | undefined;
    const stateRealized = typeof outcome?.state_realized === 'string' ? outcome.state_realized : null;
    const transfer = typeof outcome?.transfer === 'number' ? outcome.transfer : null;
    const premium = typeof outcome?.premium === 'number' ? outcome.premium : null;
    const effort = typeof outcome?.effort === 'string' ? outcome.effort : null;
    const detailParts: string[] = [];
    if (stateRealized) detailParts.push(`state: ${stateRealized}`);
    if (effort) detailParts.push(`effort: ${effort}`);
    if (transfer !== null) detailParts.push(`transfer: ${transfer}`);
    if (premium !== null) detailParts.push(`premium: ${premium}`);
    const detail = detailParts.join(' | ');

    return (
      <div className="flex gap-3 items-start py-2 border-t border-border mt-2">
        {data.status === 'finished' ? (
          <CheckCircle className="w-4 h-4 text-success" />
        ) : (
          <XCircle className="w-4 h-4 text-danger" />
        )}
        <div>
          <span className="text-sm font-medium">
            Match {data.status as string}
            {data.trigger ? <span className="text-text-muted ml-2">({String(data.trigger)})</span> : null}
          </span>
          {detail ? <div className="text-xs text-text-muted mt-0.5">{detail}</div> : null}
        </div>
      </div>
    );
  }

  return null;
}

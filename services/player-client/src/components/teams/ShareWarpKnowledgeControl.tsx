import React, { useState } from 'react';
import { teamAPI } from '../../services/api';

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

export function formatShareWarpKnowledgeError(error: unknown, fallback: string): string {
  if (error instanceof TypeError) return fallback;
  const status = httpStatus(error);
  const message = error instanceof Error ? error.message : undefined;
  const hasServerDetail =
    !(error instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to share warp knowledge with this team.';
  }
  if (status === 429) {
    return 'Warp knowledge share rate limit exceeded — wait a moment and try again.';
  }
  if (error instanceof Error && error.message) {
    if (isNetworkCollapseMessage(error.message)) return fallback;
    return error.message;
  }
  return fallback;
}

function formatShareSummary(result: {
  shared_warp_count: number;
  recipient_count: number;
  rows_created: number;
}): string {
  const warps = result.shared_warp_count;
  const recipients = result.recipient_count;
  const created = result.rows_created;
  if (warps === 0) {
    return 'No known warps to share yet — scan or traverse corridors first.';
  }
  if (recipients === 0) {
    return `You know ${warps} warp${warps === 1 ? '' : 's'}, but there are no other teammates online to receive them.`;
  }
  if (created === 0) {
    return `Shared with ${recipients} teammate${recipients === 1 ? '' : 's'} — they already knew every corridor you offered (${warps} known).`;
  }
  return `Shared ${warps} warp${warps === 1 ? '' : 's'} with ${recipients} teammate${recipients === 1 ? '' : 's'} (${created} new knowledge row${created === 1 ? '' : 's'}).`;
}

export interface ShareWarpKnowledgeControlProps {
  teamId: string;
}

/**
 * Deliberate one-time team warp-knowledge catch-up (LEG-4118).
 * invent=0: no ongoing sync, no ARIA/LLM surface.
 */
const ShareWarpKnowledgeControl: React.FC<ShareWarpKnowledgeControlProps> = ({ teamId }) => {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const handleShare = async () => {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      const result = await teamAPI.shareWarpKnowledge(teamId);
      setMsg({ ok: true, text: formatShareSummary(result) });
    } catch (e: unknown) {
      setMsg({
        ok: false,
        text: formatShareWarpKnowledgeError(e, 'Warp knowledge share failed'),
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="team-warp-share" data-testid="share-warp-knowledge-control">
      <h3>Warp Knowledge</h3>
      <p className="section-description">
        One-time catch-up share of warps you already know with current teammates. Later joiners
        get nothing retroactively — share again after they join if needed.
      </p>
      <button
        type="button"
        className="action-button primary"
        data-testid="share-warp-knowledge-btn"
        disabled={busy}
        aria-busy={busy}
        onClick={() => void handleShare()}
      >
        {busy ? 'Sharing…' : 'Share my warp knowledge with team'}
      </button>
      {msg && (
        <div
          className={msg.ok ? 'form-success' : 'form-error'}
          role="status"
          data-testid="share-warp-knowledge-msg"
        >
          {msg.text}
        </div>
      )}
    </div>
  );
};

export default ShareWarpKnowledgeControl;

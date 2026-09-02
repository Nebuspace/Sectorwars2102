import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import {
  syndicateFenceAPI,
  type SyndicateFenceInfo,
} from '../../services/api';
import { formatCredits } from '../../utils/formatters';
import './spacedock.css';

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

function serverDetail(err: unknown): string | undefined {
  if (err instanceof TypeError) return undefined;
  const message = err instanceof Error ? err.message : undefined;
  if (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message)
  ) {
    return message;
  }
  return undefined;
}

const REASON_COPY: Record<string, string> = {
  not_docked: 'You must be docked at this station to fence cargo.',
  invalid_quantity: 'Enter a valid quantity to fence.',
  unknown_commodity: 'That commodity cannot be fenced here.',
  insufficient_flagged_origin: 'Not enough flagged-origin cargo for that quantity.',
  insufficient_cargo: 'Not enough cargo in the hold for that quantity.',
};

function humanizeFenceReason(detail: string | undefined): string | undefined {
  if (!detail) return undefined;
  const key = detail.trim().toLowerCase();
  if (REASON_COPY[key]) return REASON_COPY[key];
  // Avoid leaking raw snake_case reason tokens when we have no mapping.
  if (/^[a-z][a-z0-9_]*$/.test(key)) {
    return 'The fence refused that cargo — check your flagged hold and try again.';
  }
  return detail;
}

/**
 * Human-readable fence errors — no raw status codes in UI (LEG-4112 densify).
 */
export function formatSyndicateFenceError(error: unknown, fallback: string): string {
  if (error instanceof TypeError) return fallback;

  const status = httpStatus(error);
  const detail = serverDetail(error);

  if (status === 403) {
    if (detail) return humanizeFenceReason(detail) ?? detail;
    return 'You do not have permission to use the syndicate fence right now.';
  }

  if (status === 429) {
    return 'Syndicate fence rate limit exceeded — wait a moment and try again.';
  }

  if (status === 409) {
    return humanizeFenceReason(detail) ?? 'You must be docked at this station to fence cargo.';
  }

  if (status === 400) {
    return (
      humanizeFenceReason(detail) ??
      'The fence refused that cargo — check your flagged hold and try again.'
    );
  }

  if (status === 404) {
    return 'The syndicate fence is not available here.';
  }

  if (error instanceof Error && error.message) {
    if (isNetworkCollapseMessage(error.message)) return fallback;
    return humanizeFenceReason(error.message) ?? error.message;
  }
  return fallback;
}

/** Probe GET — 404 hides the tab; other errors leave the tab hidden too (silent gate). */
export async function probeSyndicateFence(
  stationId: string,
): Promise<SyndicateFenceInfo | null> {
  try {
    return await syndicateFenceAPI.getFence(stationId);
  } catch (err) {
    // Gate unmet / missing fence both 404 — do not advertise existence.
    return null;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function qtyMap(value: unknown): Record<string, number> {
  const rec = asRecord(value);
  if (!rec) return {};
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(rec)) {
    if (typeof v === 'number' && Number.isFinite(v) && v > 0) out[k] = v;
  }
  return out;
}

const prettyCommodity = (value: string): string =>
  value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

export interface SyndicateFencePanelProps {
  stationId: string;
  stationName?: string;
  fenceInfo?: SyndicateFenceInfo | null;
  credits?: number;
  onCreditsSet?: (credits: number) => void;
  onBack?: () => void;
}

/**
 * Shadow Syndicate cargo fencing desk (LEG-4112 invent=0).
 * Parent mounts only after GET succeeds; this panel never invents UUID entry.
 */
const SyndicateFencePanel: React.FC<SyndicateFencePanelProps> = ({
  stationId,
  stationName,
  fenceInfo,
  credits,
  onCreditsSet,
  onBack,
}) => {
  const { currentShip, refreshPlayerState, updatePlayerCredits, playerState } =
    useGame();
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [qtyByCommodity, setQtyByCommodity] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const payoutPercent = fenceInfo?.payout_percent ?? 70;
  const displayCredits =
    typeof credits === 'number' ? credits : (playerState?.credits ?? 0);

  const fenceable = useMemo(() => {
    const cargo = asRecord(currentShip?.cargo);
    const flagged = qtyMap(cargo?.flagged_origin);
    const contents = qtyMap(cargo?.contents);
    return Object.keys(flagged)
      .map((commodity) => {
        const flaggedQty = flagged[commodity] ?? 0;
        const holdQty = contents[commodity] ?? 0;
        const max = Math.min(flaggedQty, holdQty);
        return { commodity, flaggedQty, holdQty, max };
      })
      .filter((row) => row.max > 0)
      .sort((a, b) => a.commodity.localeCompare(b.commodity));
  }, [currentShip?.cargo]);

  useEffect(() => {
    setQtyByCommodity((prev) => {
      const next: Record<string, number> = {};
      for (const row of fenceable) {
        const existing = prev[row.commodity];
        next[row.commodity] =
          typeof existing === 'number' && existing >= 1
            ? Math.min(existing, row.max)
            : row.max;
      }
      return next;
    });
  }, [fenceable]);

  const handleFence = useCallback(
    async (commodity: string, max: number) => {
      if (busyKey) return;
      const rawQty = qtyByCommodity[commodity] ?? max;
      const quantity = Math.max(1, Math.min(Math.floor(rawQty), max));
      setBusyKey(commodity);
      setMsg(null);
      try {
        const result = await syndicateFenceAPI.fenceCargo({
          station_id: stationId,
          commodity,
          quantity,
        });
        if (typeof result.credits === 'number') {
          onCreditsSet?.(result.credits);
          updatePlayerCredits?.(result.credits);
        }
        await refreshPlayerState();
        setMsg({
          ok: true,
          text: `Fenced ${quantity} ${prettyCommodity(commodity)} for ${formatCredits(result.payout)} (${result.payout_percent ?? payoutPercent}% of market). Balance: ${formatCredits(result.credits)}.`,
        });
      } catch (err) {
        setMsg({
          ok: false,
          text: formatSyndicateFenceError(err, 'Could not fence that cargo — try again.'),
        });
      } finally {
        setBusyKey(null);
      }
    },
    [
      busyKey,
      qtyByCommodity,
      stationId,
      onCreditsSet,
      updatePlayerCredits,
      refreshPlayerState,
      payoutPercent,
    ],
  );

  return (
    <div className="syndicate-fence-panel" data-testid="syndicate-fence-panel">
      <div className="syndicate-fence-header">
        {onBack && (
          <button type="button" className="venue-back-btn" onClick={onBack}>
            ← Back
          </button>
        )}
        <div>
          <h3>Syndicate Fence</h3>
          <p className="syndicate-fence-sub">
            {stationName ? `${stationName} · ` : ''}
            Flagged-origin cargo · {payoutPercent}% payout
          </p>
        </div>
        <div className="syndicate-fence-credits" aria-live="polite">
          {formatCredits(displayCredits)}
        </div>
      </div>

      {msg && (
        <div
          className={msg.ok ? 'genesis-success-message' : 'genesis-error-message'}
          role="status"
          data-testid="syndicate-fence-message"
        >
          <span className={msg.ok ? 'success-icon' : 'error-icon'}>
            {msg.ok ? '✅' : '❌'}
          </span>
          {msg.text}
        </div>
      )}

      {fenceable.length === 0 ? (
        <div className="syndicate-fence-empty" data-testid="syndicate-fence-empty">
          No flagged-origin cargo in the hold to fence.
        </div>
      ) : (
        <ul className="syndicate-fence-list" data-testid="syndicate-fence-list">
          {fenceable.map((row) => {
            const qty = qtyByCommodity[row.commodity] ?? row.max;
            const busy = busyKey === row.commodity;
            return (
              <li key={row.commodity} className="syndicate-fence-row">
                <div className="syndicate-fence-row-meta">
                  <strong>{prettyCommodity(row.commodity)}</strong>
                  <span>
                    Flagged {row.flaggedQty} · Hold {row.holdQty}
                  </span>
                </div>
                <div className="syndicate-fence-row-actions">
                  <input
                    type="number"
                    className="syndicate-fence-qty"
                    min={1}
                    max={row.max}
                    value={qty}
                    disabled={!!busyKey}
                    aria-label={`Quantity of ${prettyCommodity(row.commodity)} to fence`}
                    onChange={(e) => {
                      const n = Number(e.target.value);
                      setQtyByCommodity((prev) => ({
                        ...prev,
                        [row.commodity]: Number.isFinite(n) ? n : 1,
                      }));
                    }}
                    data-testid={`syndicate-fence-qty-${row.commodity}`}
                  />
                  <button
                    type="button"
                    className="syndicate-fence-cta"
                    disabled={!!busyKey || qty < 1 || qty > row.max}
                    onClick={() => void handleFence(row.commodity, row.max)}
                    data-testid={`syndicate-fence-cta-${row.commodity}`}
                  >
                    {busy ? 'Fencing…' : 'Fence'}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

export default SyndicateFencePanel;

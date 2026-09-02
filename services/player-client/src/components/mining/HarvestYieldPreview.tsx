/**
 * Asteroid HARVEST row — yield band before committing 5 turns (mining.md:254).
 * Calls tip GET /mining/yield-preview only. Not a depletion overlay; HARVEST
 * POST path stays on GameDashboard.handleHarvest.
 */
import React, { useEffect, useState } from 'react';
import { miningAPI } from '../../services/api';
import './harvest-yield-preview.css';

/** Same keys/copy as the post-click HARVEST FAILED banner — do not invent new gates. */
export const HARVEST_GATE_COPY: Record<string, string> = {
  no_mining_laser: 'No mining laser equipped — fit one at a TradeDock to extract ore.',
  must_be_undocked: 'You must be undocked and in open space to deploy the mining laser.',
  cargo_full: 'Cargo hold is full — no room for ore. Sell or jettison before mining.',
  insufficient_turns: 'Not enough turns to run a harvest cycle.',
  not_an_asteroid_field: 'No asteroids here — harvesting requires an asteroid field.',
  ship_not_found: 'Active ship not found — re-select a ship and try again.',
  already_mining: 'Mining laser already deployed — wait for the current harvest to finish.',
};

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

export function harvestGateMessage(
  reason: unknown,
  fallback = 'Yield preview failed. Please try again.',
): string {
  if (reason instanceof TypeError) return fallback;
  const status = httpStatus(reason);
  if (status === 403) {
    if (reason instanceof Error && reason.message.trim() && !/^API Error: \d+$/.test(reason.message.trim())) {
      return HARVEST_GATE_COPY[reason.message] || reason.message;
    }
    return 'Access denied — you cannot preview harvest yield right now.';
  }
  if (status === 429) {
    return 'Harvest yield preview rate limit exceeded — wait a moment and try again.';
  }
  if (reason instanceof Error && reason.message.length > 0) {
    if (isNetworkCollapseMessage(reason.message)) return fallback;
    const key = reason.message;
    return HARVEST_GATE_COPY[key] || key;
  }
  if (typeof reason === 'string' && reason.length > 0) {
    if (isNetworkCollapseMessage(reason)) return fallback;
    return HARVEST_GATE_COPY[reason] || reason;
  }
  return fallback;
}

type PreviewPayload = {
  success?: boolean;
  reason?: string | null;
  ore_lo?: number;
  ore_hi?: number;
  richness_tier?: number | null;
  laser_level?: number | null;
  turns_cost?: number;
};

export type HarvestGateState = {
  blocked: boolean;
  message: string | null;
  reasonKey: string | null;
};

type Props = {
  shipId: string | undefined;
  onGateChange?: (state: HarvestGateState) => void;
};

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

const NO_SHIP_GATE_MESSAGE = 'No active ship to preview yield.';

export const HarvestYieldPreview: React.FC<Props> = ({ shipId, onGateChange }) => {
  const [payload, setPayload] = useState<PreviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!shipId) {
      setPayload(null);
      setError(null);
      setLoading(false);
      onGateChange?.({
        blocked: true,
        message: NO_SHIP_GATE_MESSAGE,
        reasonKey: null,
      });
      return;
    }
    let cancelled = false;
    setLoading(true);
    onGateChange?.({ blocked: true, message: null, reasonKey: null });
    miningAPI
      .getYieldPreview(shipId)
      .then((raw) => {
        if (cancelled) return;
        const preview = asRecord(raw) as PreviewPayload;
        const reason =
          typeof preview?.reason === 'string' && preview.reason.length > 0
            ? preview.reason
            : null;
        const failed = preview?.success === false || reason != null;
        if (failed) {
          setPayload(null);
          const gateMessage = harvestGateMessage(reason);
          setError(gateMessage);
          onGateChange?.({
            blocked: true,
            message: gateMessage,
            reasonKey: reason,
          });
          return;
        }
        setError(null);
        setPayload(preview);
        onGateChange?.({ blocked: false, message: null, reasonKey: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setPayload(null);
        const reasonKey =
          err instanceof TypeError
            ? null
            : err instanceof Error && err.message.length > 0
              ? err.message
              : null;
        const gateMessage = harvestGateMessage(err);
        setError(gateMessage);
        onGateChange?.({
          blocked: true,
          message: gateMessage,
          reasonKey,
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [shipId, onGateChange]);

  return (
    <div className="harvest-yield-preview" data-testid="harvest-yield-preview">
      {!shipId && (
        <div className="harvest-yield-preview-line" role="status">
          {NO_SHIP_GATE_MESSAGE}
        </div>
      )}
      {shipId && loading && (
        <div className="harvest-yield-preview-line" role="status">
          Reading yield band…
        </div>
      )}
      {shipId && error && (
        <div className="harvest-yield-preview-line harvest-yield-preview-err" role="alert">
          {error}
        </div>
      )}
      {shipId && !loading && !error && payload && (
        <div
          className="harvest-yield-preview-line"
          data-testid="harvest-yield-band"
          role="status"
        >
          Expected ore {payload.ore_lo ?? 0}–{payload.ore_hi ?? 0}
          {payload.laser_level != null ? ` · L${payload.laser_level} laser` : ''}
          {payload.richness_tier != null ? ` · tier ${payload.richness_tier}` : ''}
          {payload.turns_cost != null ? ` · ${payload.turns_cost} turns` : ''}
          {' — before harvest'}
        </div>
      )}
    </div>
  );
};

export default HarvestYieldPreview;

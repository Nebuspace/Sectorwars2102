import React, { useCallback, useEffect, useState } from 'react';
import { refiningAPI } from '../../services/api';
import { useGame } from '../../contexts/GameContext';

/**
 * Crystal refining (LEG-42) — DISTINCT from quantumAPI.refineCharge (1:1
 * Shard→jump Charge). This panel calls /api/v1/refining/*:
 *   - Instant: 5 Shards + 10,000 cr → 1 Quantum Crystal (Class-3+/SpaceDock)
 *   - Timed:   100 Shards + 10,000 cr → 1 Lumen Crystal (12h start/collect)
 */

export interface CrystalRefiningPanelProps {
  /** Shard count from quantum status / inventory strip */
  shards: number;
  /** Crystal count after refine feedback */
  crystals: number;
  /** Same dock gate as charge refine (server enforces Class-3+/SpaceDock) */
  isDocked: boolean;
  onBalancesChanged?: () => void | Promise<void>;
}

const formatCountdown = (ms: number): string => {
  const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const mm = String(minutes).padStart(2, '0');
  const ss = String(seconds).padStart(2, '0');
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
};

/** RefiningVenue surfaces errors via this panel — exported for TypeError densify tests. */
export function formatCrystalRefiningError(e: unknown, fallback: string): string {
  if (e instanceof TypeError) return fallback;
  if (e && typeof e === 'object') {
    const resp = (e as { response?: { data?: unknown } }).response;
    const data = resp?.data ?? (e as { data?: unknown }).data;
    if (data && typeof data === 'object') {
      const detail = (data as Record<string, unknown>).detail;
      if (typeof detail === 'string' && detail) return detail;
    }
    const msg = (e as { message?: string }).message;
    if (typeof msg === 'string' && msg) return msg;
  }
  return fallback;
}

const CrystalRefiningPanel: React.FC<CrystalRefiningPanelProps> = ({
  shards,
  crystals,
  isDocked,
  onBalancesChanged,
}) => {
  const { refreshPlayerState } = useGame();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [lumenPending, setLumenPending] = useState(false);
  const [lumenReadyAt, setLumenReadyAt] = useState<string | null>(null);
  const [lumenCollectible, setLumenCollectible] = useState(false);
  const [nowMs, setNowMs] = useState(Date.now());

  const refreshLumenStatus = useCallback(async () => {
    try {
      const status = await refiningAPI.lumenStatus();
      setLumenPending(!!status.pending);
      setLumenReadyAt(status.ready_at);
      setLumenCollectible(!!status.collectible);
    } catch {
      // Non-fatal — panel still usable for instant refine
    }
  }, []);

  useEffect(() => {
    refreshLumenStatus();
  }, [refreshLumenStatus]);

  useEffect(() => {
    if (!lumenPending || !lumenReadyAt) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [lumenPending, lumenReadyAt]);

  useEffect(() => {
    if (!lumenReadyAt) return;
    if (Date.parse(lumenReadyAt) <= nowMs) {
      setLumenCollectible(true);
    }
  }, [lumenReadyAt, nowMs]);

  const afterSuccess = async (msg: string) => {
    setNotice(msg);
    setError(null);
    await Promise.allSettled([
      refreshPlayerState(),
      onBalancesChanged?.(),
      refreshLumenStatus(),
    ]);
  };

  const handleCrystalRefine = async () => {
    if (busy) return;
    setBusy('crystal');
    setError(null);
    try {
      const result = (await refiningAPI.refine()) as {
        quantum_crystals?: number;
        message?: string;
      };
      await afterSuccess(
        result.message ||
          `Refined 1 Quantum Crystal (balance ${result.quantum_crystals ?? crystals + 1}).`,
      );
    } catch (e) {
      setError(formatCrystalRefiningError(e, 'Crystal refine rejected.'));
    } finally {
      setBusy(null);
    }
  };

  const handleLumenStart = async () => {
    if (busy) return;
    setBusy('lumen-start');
    setError(null);
    try {
      const result = (await refiningAPI.startLumen()) as {
        lumen_refine_ready_at?: string;
        message?: string;
      };
      if (result.lumen_refine_ready_at) {
        setLumenReadyAt(result.lumen_refine_ready_at);
        setLumenPending(true);
        setLumenCollectible(false);
      }
      await afterSuccess(result.message || 'Lumen refine started — 12h wall clock.');
    } catch (e) {
      setError(formatCrystalRefiningError(e, 'Lumen refine start rejected.'));
    } finally {
      setBusy(null);
    }
  };

  const handleLumenCollect = async () => {
    if (busy) return;
    setBusy('lumen-collect');
    setError(null);
    try {
      const result = (await refiningAPI.collectLumen()) as { message?: string };
      await afterSuccess(result.message || 'Lumen Crystal collected.');
    } catch (e) {
      setError(formatCrystalRefiningError(e, 'Lumen collect rejected.'));
    } finally {
      setBusy(null);
    }
  };

  const remainingMs =
    lumenReadyAt && lumenPending && !lumenCollectible
      ? Date.parse(lumenReadyAt) - nowMs
      : 0;

  return (
    <div className="qd-crystal-refinery" data-testid="crystal-refining-panel">
      <div className="qd-section-label">CRYSTAL REFINERY</div>
      <p className="qd-hint">
        Distinct from drive charges: 5 shards + 10k → 1 Quantum Crystal; 100 shards + 10k →
        1 Lumen Crystal (12h). Requires dock at Class-3+ / SpaceDock (Lumen: Class-5+).
      </p>
      <div className="qd-crystal-row">
        <span data-testid="crystal-balance">Crystals: {crystals}</span>
        <span data-testid="shard-balance">Shards: {shards}</span>
      </div>
      <div className="qd-crystal-actions">
        <button
          type="button"
          className="qd-refine-btn"
          data-testid="refine-crystal-btn"
          disabled={!!busy || !isDocked || shards < 5}
          title={
            !isDocked
              ? 'Dock at a Class-3+ station or SpaceDock'
              : shards < 5
                ? 'Need 5 shards'
                : 'Refine 5 shards + 10,000 cr → 1 Quantum Crystal'
          }
          onClick={handleCrystalRefine}
        >
          {busy === 'crystal' ? 'REFINING…' : 'REFINE CRYSTAL (5⟶1)'}
        </button>
        {!lumenPending ? (
          <button
            type="button"
            className="qd-refine-btn"
            data-testid="lumen-start-btn"
            disabled={!!busy || !isDocked || shards < 100}
            title={
              !isDocked
                ? 'Dock at a Class-5+ station or SpaceDock'
                : shards < 100
                  ? 'Need 100 shards'
                  : 'Start 12h Lumen Crystal refine'
            }
            onClick={handleLumenStart}
          >
            {busy === 'lumen-start' ? 'STARTING…' : 'START LUMEN (100⟶1 / 12h)'}
          </button>
        ) : lumenCollectible ? (
          <button
            type="button"
            className="qd-refine-btn"
            data-testid="lumen-collect-btn"
            disabled={!!busy}
            onClick={handleLumenCollect}
          >
            {busy === 'lumen-collect' ? 'COLLECTING…' : 'COLLECT LUMEN CRYSTAL'}
          </button>
        ) : (
          <div className="qd-lumen-countdown" data-testid="lumen-countdown" role="status">
            Lumen ready in {formatCountdown(remainingMs)}
          </div>
        )}
      </div>
      {error && (
        <div className="qd-inline-error" data-testid="crystal-refine-error">
          {error}
        </div>
      )}
      {notice && (
        <div className="qd-hint" data-testid="crystal-refine-notice">
          {notice}
        </div>
      )}
    </div>
  );
};

export default CrystalRefiningPanel;

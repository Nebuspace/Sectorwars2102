import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { expeditionAPI } from '../../services/api';
import {
  EXPEDITION_DELAY_MINUTES,
  expeditionLaunchedAt,
  siteEnergyBaseline,
  siteNativeLife,
  siteShapeClass,
  siteUsableSlots,
  type Expedition,
} from './expeditionTypes';
import OrbitalScanView from './OrbitalScanView';
import SiteGridPreview from './SiteGridPreview';
import './survey-expedition-panel.css';

/**
 * Survey & site-discovery panel (ADR-0091). Orchestrates: orbital scan →
 * dispatch expedition → status/countdown while PENDING → compare
 * current-held vs re-rolled result → settle (claim) with contest-window
 * feedback for both the win and CAS-loss case.
 *
 * settle() calls the existing POST /planets/{id}/claim route (rewritten
 * in place by lane3-settle-cas to run the ADR §8 CAS resolver). The
 * settle failure branch below treats ANY 4xx as a possible CAS-loss (a
 * distinct, non-generic message) — lane3's route distinguishes several
 * failure reasons via HTTPException detail strings; this UI does not yet
 * parse that detail into a specific message.
 */

interface SurveyExpeditionPanelProps {
  planetId: string;
  planetName?: string;
  planetType?: string | null;
  shipId?: string | null;
}

type SettleOutcome = { kind: 'won' } | { kind: 'lost'; message: string } | null;

const formatCountdown = (secondsLeft: number): string => {
  const s = Math.max(0, Math.ceil(secondsLeft));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, '0')}`;
};

/** TypeError (fetch network collapse) → fallback; preserve gameserver Error.message otherwise. */
/** Transport collapse copy is not gameserver detail (LEG-3288 densify). */
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

export function formatSurveyExpeditionError(err: unknown, fallback: string): string {
  const status = httpStatus(err);
  const detail =
    err instanceof Error &&
    err.message &&
    !isNetworkCollapseMessage(err.message) &&
    !/^API Error: \d+$/.test(err.message.trim())
      ? err.message.trim()
      : undefined;

  if (status === 403) {
    if (detail) return detail;
    return 'You do not have permission to manage this survey expedition.';
  }

  if (status === 429) {
    return 'Survey expedition rate limit exceeded — wait a moment and try again.';
  }

  if (err instanceof TypeError) return fallback;
  if (detail) return detail;
  return fallback;
}

const SurveyExpeditionPanel: React.FC<SurveyExpeditionPanelProps> = ({
  planetId,
  planetName,
  planetType,
  shipId,
}) => {
  const [current, setCurrent] = useState<Expedition | null>(null);
  const [rerolled, setRerolled] = useState<Expedition | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [listRestoreChecked, setListRestoreChecked] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [settleOutcome, setSettleOutcome] = useState<SettleOutcome>(null);
  const [settling, setSettling] = useState(false);

  // Restore the latest expedition on THIS planet from the player's list, so
  // a page revisit doesn't lose an in-flight PENDING expedition.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await expeditionAPI.list();
        const list: Expedition[] = data?.expeditions ?? [];
        const forPlanet = list
          .filter((e) => (e.planet_id ?? e.planetId) === planetId)
          .sort((a, b) => {
            const at = expeditionLaunchedAt(a) ?? '';
            const bt = expeditionLaunchedAt(b) ?? '';
            return bt.localeCompare(at);
          });
        if (!cancelled) {
          setListError(null);
          if (forPlanet.length > 0) {
            setCurrent(forPlanet[0]);
          }
          setListRestoreChecked(true);
        }
      } catch (err) {
        if (!cancelled) {
          setListError(
            formatSurveyExpeditionError(err, 'Could not restore your expedition status for this world.'),
          );
          setListRestoreChecked(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [planetId]);

  // Poll a PENDING expedition's status every 5s until it resolves.
  useEffect(() => {
    if (!current || current.status !== 'PENDING') return;
    const interval = setInterval(async () => {
      try {
        const fresh = await expeditionAPI.getStatus(current.id);
        setCurrent(fresh);
      } catch {
        // transient — next tick retries
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [current]);

  // Countdown clock tick (only matters while PENDING).
  useEffect(() => {
    if (!current || current.status !== 'PENDING') return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [current]);

  const secondsRemaining = useMemo(() => {
    if (!current || current.status !== 'PENDING') return null;
    const launchedAt = expeditionLaunchedAt(current);
    if (!launchedAt) return null;
    const launchedMs = new Date(launchedAt).getTime();
    if (Number.isNaN(launchedMs)) return null;
    const targetMs = launchedMs + EXPEDITION_DELAY_MINUTES * 60_000;
    return (targetMs - now) / 1000;
  }, [current, now]);

  const handleLaunch = useCallback(async () => {
    if (loading) return;
    try {
      setLoading(true);
      setError(null);
      setSettleOutcome(null);
      const expedition = await expeditionAPI.launch(planetId, shipId ?? undefined);
      setCurrent(expedition);
      setRerolled(null);
    } catch (err: unknown) {
      setError(formatSurveyExpeditionError(err, 'Failed to launch expedition'));
    } finally {
      setLoading(false);
    }
  }, [loading, planetId, shipId]);

  const handleReroll = useCallback(async () => {
    if (!current || loading) return;
    try {
      setLoading(true);
      setError(null);
      const expedition = await expeditionAPI.reroll(current.id);
      setRerolled(expedition);
    } catch (err: unknown) {
      setError(formatSurveyExpeditionError(err, 'Failed to re-roll expedition'));
    } finally {
      setLoading(false);
    }
  }, [current, loading]);

  /** ACCEPT the re-rolled result: it becomes the held candidate for settle. */
  const handleAcceptReroll = useCallback(() => {
    if (!rerolled) return;
    setCurrent(rerolled);
    setRerolled(null);
  }, [rerolled]);

  const handleKeepCurrent = useCallback(() => {
    setRerolled(null);
  }, []);

  const handleSettle = useCallback(async () => {
    if (!current || settling) return;
    try {
      setSettling(true);
      setError(null);
      await expeditionAPI.settle(planetId);
      setSettleOutcome({ kind: 'won' });
    } catch (err: unknown) {
      // Any settle rejection is treated as a possible CAS-loss (someone else
      // settled first) rather than a generic error — the real distinguishing
      // signal from lane3's route is unknown at build time.
      setSettleOutcome({
        kind: 'lost',
        message: formatSurveyExpeditionError(
          err,
          'Another expedition settled this site first.',
        ),
      });
    } finally {
      setSettling(false);
    }
  }, [current, settling]);

  const resolved = current && current.status !== 'PENDING';
  const canSettle = resolved && (current!.status === 'SUCCESS' || current!.status === 'PARTIAL');

  return (
    <div className="survey-expedition-panel">
      <OrbitalScanView planetId={planetId} planetName={planetName} planetType={planetType} />

      <div className="survey-dispatch">
        <div className="survey-dispatch-header">
          <h3>Ground Expedition</h3>
        </div>

        {listError && (
          <div className="survey-error" role="alert" data-testid="survey-list-error">
            {listError}
          </div>
        )}

        {listRestoreChecked && !listError && !current && (
          <p className="survey-dispatch-empty" data-testid="survey-no-expedition">
            No expedition in progress on this world.
          </p>
        )}

        {!current && (
          <button className="survey-btn survey-btn-primary" disabled={loading} onClick={handleLaunch}>
            {loading ? 'Launching…' : 'Launch Expedition'}
          </button>
        )}

        {current && current.status === 'PENDING' && (
          <div className="survey-status survey-status-pending">
            <span className="survey-status-label">Expedition in progress…</span>
            {secondsRemaining !== null && (
              <span className="survey-countdown">{formatCountdown(secondsRemaining)}</span>
            )}
          </div>
        )}

        {current && resolved && (
          <div className={`survey-status survey-status-${current.status.toLowerCase()}`}>
            <span className="survey-status-label">
              {current.status === 'SUCCESS' && 'Expedition succeeded'}
              {current.status === 'PARTIAL' && 'Partial intel recovered'}
              {current.status === 'FAILURE' && 'Expedition failed — no site found'}
            </span>
          </div>
        )}

        {error && (
          <div className="survey-error" role="alert">
            {error}
          </div>
        )}
      </div>

      {current && resolved && current.status !== 'FAILURE' && (
        <SiteGridPreview result={current.result} fogged={false} />
      )}
      {current && !resolved && <SiteGridPreview result={null} fogged />}

      {current && resolved && current.status !== 'FAILURE' && !rerolled && (
        <div className="survey-actions">
          <button className="survey-btn" disabled={loading} onClick={handleReroll}>
            {loading ? 'Re-rolling…' : 'Re-roll (new expedition)'}
          </button>
        </div>
      )}

      {rerolled && rerolled.status === 'PENDING' && (
        <div className="survey-status survey-status-pending">
          <span className="survey-status-label">Re-roll expedition in progress…</span>
        </div>
      )}

      {rerolled && rerolled.status !== 'PENDING' && (
        <div className="survey-compare">
          <h4>Compare Results</h4>
          <div className="survey-compare-grid">
            <SiteCompareCard title="Current" expedition={current} />
            <SiteCompareCard title="Re-rolled" expedition={rerolled} />
          </div>
          <div className="survey-compare-actions">
            <button className="survey-btn" onClick={handleKeepCurrent}>
              Keep Current
            </button>
            <button
              className="survey-btn survey-btn-primary"
              disabled={rerolled.status === 'FAILURE'}
              onClick={handleAcceptReroll}
              title={rerolled.status === 'FAILURE' ? 'A failed re-roll cannot be accepted' : undefined}
            >
              Accept Re-roll
            </button>
          </div>
        </div>
      )}

      {canSettle && !settleOutcome && (
        <div className="survey-settle">
          <button className="survey-btn survey-btn-settle" disabled={settling} onClick={handleSettle}>
            {settling ? 'Settling…' : 'Settle This Site'}
          </button>
          <span className="survey-settle-hint">
            First valid settle on a contestable world claims it — rivals may be racing you.
          </span>
        </div>
      )}

      {settleOutcome?.kind === 'won' && (
        <div className="survey-settle-result survey-settle-won" role="status">
          <span className="survey-settle-result-icon" aria-hidden="true">🏳️</span>
          <span>You settled it — the colony is yours.</span>
        </div>
      )}

      {settleOutcome?.kind === 'lost' && (
        <div className="survey-settle-result survey-settle-lost" role="alert">
          <span className="survey-settle-result-icon" aria-hidden="true">⚡</span>
          <span>{settleOutcome.message}</span>
        </div>
      )}
    </div>
  );
};

const SiteCompareCard: React.FC<{ title: string; expedition: Expedition | null }> = ({
  title,
  expedition,
}) => {
  if (!expedition) return null;
  const result = expedition.result;
  return (
    <div className={`survey-compare-card survey-compare-${expedition.status.toLowerCase()}`}>
      <div className="survey-compare-card-title">{title}</div>
      <div className="survey-compare-card-status">{expedition.status}</div>
      {result ? (
        <ul className="survey-compare-card-facts">
          <li>Shape: {siteShapeClass(result) ?? '—'}</li>
          <li>Slots: {siteUsableSlots(result) ?? '—'}</li>
          {siteEnergyBaseline(result) && <li>Energy: {siteEnergyBaseline(result)}</li>}
          {siteNativeLife(result) != null && (
            <li>Native life: {siteNativeLife(result) ? 'Present' : 'None'}</li>
          )}
          {result.banded && <li className="survey-compare-banded">Banded intel — energy/resources unknown</li>}
        </ul>
      ) : (
        <div className="survey-compare-card-empty">No site data.</div>
      )}
    </div>
  );
};

export default SurveyExpeditionPanel;

import React, { useEffect, useState } from 'react';
import { gcLapseAPI } from '../../services/api';
import { useGame } from '../../contexts/GameContext';
import './gc-lapse-panel.css';

type Holding = {
  asset_type: string;
  asset_id: string;
  name: string;
  region_id: string | null;
  sector_id: number;
};

/**
 * GcLapsePanel — WO-WIRE-GC-LAPSE-SELF-SERVICE.
 *
 * One-shot GET /players/me/gc-lapse-status on mount. When lapsed with a
 * remaining emergency relocation grant, offers teleport targets from
 * foreign_holdings via POST /gc-emergency-relocation.
 */
const GcLapsePanel: React.FC = () => {
  const { refreshPlayerState } = useGame();
  const [holdings, setHoldings] = useState<Holding[] | null>(null);
  const [available, setAvailable] = useState(false);
  const [lapsedAt, setLapsedAt] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    gcLapseAPI
      .getStatus()
      .then((status) => {
        if (cancelled) return;
        if (!status?.lapsed || !status.relocation_available) {
          setHoldings(null);
          setAvailable(false);
          return;
        }
        setLapsedAt(status.gc_lapsed_at);
        setAvailable(true);
        setHoldings(Array.isArray(status.foreign_holdings) ? status.foreign_holdings : []);
      })
      .catch(() => {
        if (!cancelled) {
          setHoldings(null);
          setAvailable(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (dismissed || !available || holdings === null) return null;

  const relocate = async (h: Holding) => {
    if (busyId) return;
    const ok = window.confirm(
      `Emergency relocate to ${h.name} (sector ${h.sector_id})? This one-time GC-lapse grant cannot be reused until you re-subscribe.`
    );
    if (!ok) return;
    setBusyId(h.asset_id);
    setFeedback(null);
    try {
      await gcLapseAPI.emergencyRelocate(h.asset_type, h.asset_id);
      setFeedback(`Relocated to ${h.name}.`);
      setAvailable(false);
      await refreshPlayerState();
    } catch (err: unknown) {
      setFeedback(err instanceof Error ? err.message : 'Relocation failed');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div
      className="gc-lapse-panel"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="gc-lapse-title"
      data-testid="gc-lapse-panel"
    >
      <div className="gc-lapse-card">
        <h3 id="gc-lapse-title">Galactic Citizen lapsed</h3>
        <p className="gc-lapse-body">
          You have a 7-day window to withdraw foreign-region assets
          {lapsedAt ? ` (lapsed ${new Date(lapsedAt).toLocaleDateString()})` : ''}. One free
          emergency relocation remains.
        </p>
        {holdings.length === 0 ? (
          <p className="gc-lapse-empty">No foreign holdings listed for relocation.</p>
        ) : (
          <ul className="gc-lapse-holdings">
            {holdings.map((h) => (
              <li key={`${h.asset_type}-${h.asset_id}`}>
                <span>
                  {h.name} · {h.asset_type} · sec {h.sector_id}
                </span>
                <button
                  type="button"
                  data-testid={`gc-lapse-relocate-${h.asset_id}`}
                  disabled={Boolean(busyId)}
                  onClick={() => relocate(h)}
                >
                  {busyId === h.asset_id ? 'Relocating…' : 'Relocate'}
                </button>
              </li>
            ))}
          </ul>
        )}
        <button
          type="button"
          className="gc-lapse-dismiss"
          data-testid="gc-lapse-dismiss"
          onClick={() => setDismissed(true)}
        >
          Dismiss
        </button>
        {feedback && (
          <p className="gc-lapse-feedback" role="status" data-testid="gc-lapse-feedback">
            {feedback}
          </p>
        )}
      </div>
    </div>
  );
};

export default GcLapsePanel;

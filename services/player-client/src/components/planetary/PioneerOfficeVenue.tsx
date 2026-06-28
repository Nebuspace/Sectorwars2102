import React, { useCallback, useEffect, useState } from 'react';
import { useGame, type Planet, type MigrationContract, type PioneerOffice } from '../../contexts/GameContext';

// =====================================================================
// Pioneer Office — broker and ferry pioneer migration contracts at a
// capital population hub (FEATURES/planets/colonization.md).
//
// Backend: /api/v1/pioneer/* via GameContext helpers. Renders only what
// the API returns; 400/403 gameplay refusals are shown inline. No mock data.
// =====================================================================

const COHORT_PRESETS = [100, 500, 1000, 5000, 10000];
const MAX_COHORT = 10000;

const axiosErrorMessage = (error: unknown, fallback: string): string => {
  const e = error as { response?: { data?: { detail?: unknown; message?: unknown } }; message?: string };
  const raw = e?.response?.data?.detail ?? e?.response?.data?.message;
  if (typeof raw === 'string' && raw) return raw;
  if (!e?.response && typeof e?.message === 'string' && e.message) return e.message;
  return fallback;
};

interface Props {
  planet: Planet;
  onBack: () => void;
}

const PioneerOfficeVenue: React.FC<Props> = ({ planet, onBack }) => {
  const { getPioneerOffice, brokerMigrationContract, loadPioneerBatch, cancelMigrationContract } = useGame();

  const [office, setOffice] = useState<PioneerOffice | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Broker form
  const [cohort, setCohort] = useState(1000);

  // Per-contract load form: contractId currently expanded for a batch load
  const [loadFor, setLoadFor] = useState<string | null>(null);
  const [loadQty, setLoadQty] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const data = await getPioneerOffice();
      setOffice(data);
      setError(null);
    } catch (e) {
      setError(axiosErrorMessage(e, 'Could not reach the Pioneer Office.'));
    } finally {
      setLoading(false);
    }
  }, [getPioneerOffice]);

  // Fetch once on mount; subsequent refreshes are explicit (after broker /
  // load / cancel). Depending on `refresh` here would refetch on every
  // provider poll tick, since the context helper's identity changes.
  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fee = office?.fee_per_pioneer ?? 0;
  const cargoFree = office?.cargo_free ?? 0;
  const cargoColonists = office?.cargo_colonists ?? 0;
  const contracts = office?.contracts ?? [];

  const brokerTotal = cohort * fee;

  const onBroker = async () => {
    setBusy(true);
    setError(null);
    try {
      await brokerMigrationContract(cohort);
      await refresh();
    } catch (e) {
      setError(axiosErrorMessage(e, 'Could not broker the contract.'));
    } finally {
      setBusy(false);
    }
  };

  const openLoad = (c: MigrationContract) => {
    // Cap on cargo + remaining client-side; the credits cap is enforced
    // (and surfaced) server-side on load.
    const maxBatch = Math.max(0, Math.min(c.remaining_to_load, cargoFree));
    setLoadFor(c.id);
    setLoadQty(maxBatch);
    setError(null);
  };

  const onLoad = async (c: MigrationContract) => {
    if (loadQty <= 0) return;
    setBusy(true);
    setError(null);
    try {
      await loadPioneerBatch(c.id, loadQty);
      setLoadFor(null);
      await refresh();
    } catch (e) {
      setError(axiosErrorMessage(e, 'Could not load the batch.'));
    } finally {
      setBusy(false);
    }
  };

  const onCancel = async (c: MigrationContract) => {
    setBusy(true);
    setError(null);
    try {
      await cancelMigrationContract(c.id);
      await refresh();
    } catch (e) {
      setError(axiosErrorMessage(e, 'Could not void the contract.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pioneer-office">
      <div className="po-topbar">
        <button className="po-back" type="button" onClick={onBack}>◀ HUB</button>
        <div className="po-title">PIONEER OFFICE — {planet.name}</div>
      </div>

      <p className="po-blurb">
        The Migration Authority registers volunteer pioneers for resettlement on frontier
        worlds. Broker a cohort, ferry them in cryosleep transit pods (one cargo unit each),
        and settle them on an uncolonized world to fulfill the contract.
      </p>

      {error && <div className="po-error" role="alert">{error}</div>}

      {loading ? (
        <div className="po-loading">Contacting the Migration Authority…</div>
      ) : (
        <>
          <div className="po-cargo-line">
            <span>Pioneers aboard: <strong>{cargoColonists.toLocaleString()}</strong></span>
            <span>Cargo free: <strong>{cargoFree.toLocaleString()}</strong></span>
            <span>Fee: <strong>{fee} cr</strong> / pioneer</span>
          </div>

          {/* Broker a new cohort */}
          <div className="po-panel">
            <div className="po-panel-title">SECURE A MIGRATION CONTRACT</div>
            <div className="po-presets">
              {COHORT_PRESETS.map(p => (
                <button
                  key={p}
                  type="button"
                  className={`po-preset${cohort === p ? ' active' : ''}`}
                  onClick={() => setCohort(p)}
                >
                  {p.toLocaleString()}
                </button>
              ))}
            </div>
            <input
              className="po-slider"
              type="range"
              min={100}
              max={MAX_COHORT}
              step={100}
              value={cohort}
              onChange={e => setCohort(Number(e.target.value))}
            />
            <div className="po-summary">
              <span>Cohort: <strong>{cohort.toLocaleString()}</strong> pioneers</span>
              <span>Locked fee: <strong>{fee} cr</strong> each</span>
              <span>Total (paid as you load): <strong>{brokerTotal.toLocaleString()} cr</strong></span>
            </div>
            <button className="po-action" type="button" disabled={busy} onClick={onBroker}>
              BROKER COHORT
            </button>
          </div>

          {/* Active contracts */}
          <div className="po-panel">
            <div className="po-panel-title">ACTIVE CONTRACTS</div>
            {contracts.length === 0 ? (
              <div className="po-empty">No active migration contracts.</div>
            ) : (
              <ul className="po-contracts">
                {contracts.map(c => {
                  const pct = c.cohort_total > 0 ? Math.round((c.delivered / c.cohort_total) * 100) : 0;
                  const atThisHub = c.source_planet_id === planet.id;
                  const maxBatch = Math.max(0, Math.min(c.remaining_to_load, cargoFree));
                  return (
                    <li key={c.id} className="po-contract">
                      <div className="po-contract-head">
                        <span className="po-contract-source">
                          {c.source_planet_name || `Sector ${c.source_sector_id}`}
                        </span>
                        <span className={`po-status po-status-${c.status.toLowerCase()}`}>{c.status}</span>
                      </div>
                      <div className="po-progress">
                        <div className="po-progress-bar" style={{ width: `${pct}%` }} />
                      </div>
                      <div className="po-contract-stats">
                        <span>Delivered {c.delivered.toLocaleString()} / {c.cohort_total.toLocaleString()}</span>
                        <span>In transit {c.loaded.toLocaleString()}</span>
                        <span>Remaining {c.remaining_to_load.toLocaleString()}</span>
                      </div>

                      {loadFor === c.id ? (
                        <div className="po-load">
                          <input
                            className="po-slider"
                            type="range"
                            min={0}
                            max={maxBatch}
                            step={1}
                            value={Math.min(loadQty, maxBatch)}
                            onChange={e => setLoadQty(Number(e.target.value))}
                          />
                          <div className="po-load-row">
                            <span>Load <strong>{Math.min(loadQty, maxBatch).toLocaleString()}</strong> — {(Math.min(loadQty, maxBatch) * fee).toLocaleString()} cr</span>
                            <span className="po-load-cap">max now: {maxBatch.toLocaleString()} (cargo {cargoFree.toLocaleString()})</span>
                          </div>
                          <div className="po-load-actions">
                            <button className="po-action" type="button" disabled={busy || loadQty <= 0} onClick={() => onLoad(c)}>
                              LOAD PODS
                            </button>
                            <button className="po-action po-action-ghost" type="button" onClick={() => setLoadFor(null)}>
                              CANCEL
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="po-contract-actions">
                          <button
                            className="po-action"
                            type="button"
                            disabled={busy || !atThisHub || c.remaining_to_load <= 0 || cargoFree <= 0}
                            title={!atThisHub ? `Return to Sector ${c.source_sector_id} to load` : undefined}
                            onClick={() => openLoad(c)}
                          >
                            LOAD BATCH
                          </button>
                          <button
                            className="po-action po-action-ghost"
                            type="button"
                            disabled={busy || c.loaded > 0}
                            title={c.loaded > 0 ? 'Settle or disembark loaded pioneers first' : undefined}
                            onClick={() => onCancel(c)}
                          >
                            VOID
                          </button>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default PioneerOfficeVenue;

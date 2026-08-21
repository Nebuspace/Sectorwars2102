import React, { useCallback, useEffect, useMemo, useState } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import { formatAdminApiError } from '../../utils/adminApiError';
import './trade-dock-admin.css';

/**
 * LEG-58 / LEG-1487 — TradeDockAdmin construction admin surface.
 * Canon: FEATURES/economy/tradedock-shipyard.md — 12 slips = standard + specialized pools.
 *
 * Live shapes:
 * GET  /api/v1/admin/construction/tradedocks
 * GET  /api/v1/admin/construction/tradedocks/{station_id}
 * GET  /api/v1/admin/construction/reservations/{reservation_id}
 * POST /api/v1/admin/construction/reservations/{reservation_id}/force-cancel (LEG-339 / LEG-1487)
 */

interface TradeDockSummary {
  station_id: string;
  name: string;
  tradedock_tier: string | null;
  sector_id?: number | null;
}

interface SlipPool {
  capacity: number;
  in_use: number;
}

interface QueueEntry {
  position: number;
  reservation_id: string;
  player_id: string;
  ship_type: string;
  priority_bumps_count?: number;
}

interface MilestoneInfo {
  amount: number;
  paid: boolean;
}

interface RentInfo {
  daily_rent?: number;
  paid_until?: string | null;
  overdue_canonical_days?: number;
  owed?: number;
  forfeit_after_days?: number;
}

/** LEG-40 status_payload (+ overview reservation rows). */
interface ReservationStatus {
  id: string;
  station_id?: string;
  ship_type: string;
  ship_name?: string | null;
  state: string;
  total_cost?: number;
  deposit_paid?: number;
  credits_paid?: number;
  priority_bumps_count?: number;
  uses_specialized_slip?: boolean;
  milestones?: Record<string, MilestoneInfo>;
  resources_required?: Record<string, number>;
  resources_delivered?: Record<string, number>;
  phase_deadline?: string | null;
  hold_expires_at?: string | null;
  claim_expires_at?: string | null;
  created_at?: string | null;
  phase_progress_percent?: number;
  overall_progress_percent?: number;
  paused?: boolean;
  needs?: string[];
  rent?: RentInfo;
  queue_position?: number | null;
  estimated_refund?: number;
}

interface TradeDockOverview {
  station_id: string;
  station_name: string;
  tradedock_tier: string | null;
  slips: {
    standard: SlipPool;
    specialized: SlipPool;
  };
  queue_length: number;
  queue: QueueEntry[];
  reservations: ReservationStatus[];
  reservation_count_active?: number;
  reservation_count_total?: number;
}

function detailFromErr(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

function poolLabel(pool: SlipPool | undefined): string {
  if (!pool) return '—';
  return `${pool.in_use} / ${pool.capacity}`;
}

const TradeDockAdmin: React.FC = () => {
  const toast = useToast();
  const confirm = useConfirm();
  const [docks, setDocks] = useState<TradeDockSummary[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [overview, setOverview] = useState<TradeDockOverview | null>(null);
  const [reservation, setReservation] = useState<ReservationStatus | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [loadingReservation, setLoadingReservation] = useState(false);
  const [forceCancelling, setForceCancelling] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const loadDocks = useCallback(async () => {
    setLoadingList(true);
    setListError(null);
    try {
      const { data } = await api.get<{ tradedocks?: TradeDockSummary[] }>(
        '/api/v1/admin/construction/tradedocks'
      );
      const items = Array.isArray(data?.tradedocks) ? data.tradedocks : [];
      setDocks(items);
      setSelectedId((prev) => {
        if (prev && items.some((d) => d.station_id === prev)) return prev;
        return items[0]?.station_id || '';
      });
    } catch (err: unknown) {
      setDocks([]);
      setListError(detailFromErr(err, 'Failed to load TradeDocks'));
    } finally {
      setLoadingList(false);
    }
  }, []);

  const loadOverview = useCallback(async (stationId: string) => {
    if (!stationId) {
      setOverview(null);
      return;
    }
    setLoadingDetail(true);
    setDetailError(null);
    setReservation(null);
    try {
      const { data } = await api.get<TradeDockOverview>(
        `/api/v1/admin/construction/tradedocks/${encodeURIComponent(stationId)}`
      );
      setOverview({
        station_id: data.station_id,
        station_name: data.station_name,
        tradedock_tier: data.tradedock_tier ?? null,
        slips: {
          standard: data.slips?.standard ?? { capacity: 0, in_use: 0 },
          specialized: data.slips?.specialized ?? { capacity: 0, in_use: 0 },
        },
        queue_length: data.queue_length ?? (data.queue?.length ?? 0),
        queue: Array.isArray(data.queue) ? data.queue : [],
        reservations: Array.isArray(data.reservations) ? data.reservations : [],
        reservation_count_active: data.reservation_count_active,
        reservation_count_total: data.reservation_count_total,
      });
    } catch (err: unknown) {
      setOverview(null);
      setDetailError(detailFromErr(err, 'Failed to load TradeDock overview'));
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    void loadDocks();
  }, [loadDocks]);

  useEffect(() => {
    if (selectedId) {
      void loadOverview(selectedId);
    }
  }, [selectedId, loadOverview]);

  const openReservation = async (reservationId: string) => {
    setLoadingReservation(true);
    try {
      const { data } = await api.get<ReservationStatus>(
        `/api/v1/admin/construction/reservations/${encodeURIComponent(reservationId)}`
      );
      setReservation(data);
    } catch (err: unknown) {
      setReservation(null);
      toast.error(detailFromErr(err, 'Failed to load reservation detail'));
    } finally {
      setLoadingReservation(false);
    }
  };

  const forceCancelReservation = async (reservationId: string) => {
    const ok = await confirm({
      title: 'Force-cancel reservation',
      message:
        `Force-cancel reservation ${reservationId}? Credits refund via cancel_refund ` +
        '(resources never returned). This cannot be undone from Admin UI.',
      confirmLabel: 'Force-cancel',
      danger: true,
    });
    if (!ok) return;

    setForceCancelling(true);
    try {
      const { data } = await api.post<{
        message?: string;
        refund?: number;
      }>(
        `/api/v1/admin/construction/reservations/${encodeURIComponent(reservationId)}/force-cancel`
      );
      const refund =
        typeof data?.refund === 'number' ? data.refund.toLocaleString() : '—';
      toast.success(
        data?.message?.trim()
          ? data.message
          : `Reservation force-cancelled — ${refund} credits refunded`
      );
      setReservation(null);
      if (selectedId) {
        await loadOverview(selectedId);
      }
    } catch (err: unknown) {
      toast.error(
        formatAdminApiError(err, {
          fallback: 'Force-cancel failed',
          scopeHint: 'PLAYERS_VIEW scope required for construction force-cancel',
        })
      );
    } finally {
      setForceCancelling(false);
    }
  };

  const selectedDock = useMemo(
    () => docks.find((d) => d.station_id === selectedId) ?? null,
    [docks, selectedId]
  );

  const slipTotal = useMemo(() => {
    if (!overview) return null;
    const std = overview.slips.standard;
    const spec = overview.slips.specialized;
    return {
      capacity: (std?.capacity ?? 0) + (spec?.capacity ?? 0),
      in_use: (std?.in_use ?? 0) + (spec?.in_use ?? 0),
    };
  }, [overview]);

  const displayName = overview?.station_name || selectedDock?.name || 'TradeDock';
  const displayTier =
    overview?.tradedock_tier || selectedDock?.tradedock_tier || '—';

  return (
    <div className="trade-dock-admin" data-testid="trade-dock-admin">
      <PageHeader
        title="TradeDock management"
        subtitle="Shipyard slip pools · active builds · queue · reservation detail · force-cancel"
      />

      <section className="section">
        <div className="section-header">
          <div>
            <h3 className="section-title">Station picker</h3>
            <p className="section-subtitle">
              Stations with tradedock_tier (GET /admin/construction/tradedocks)
            </p>
          </div>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => void loadDocks()}>
            Refresh list
          </button>
        </div>

        {listError && (
          <div className="alert alert-error" role="alert">
            {listError}
          </div>
        )}

        {loadingList ? (
          <p className="text-muted">Loading TradeDocks…</p>
        ) : docks.length === 0 ? (
          <p className="text-muted">No TradeDocks returned.</p>
        ) : (
          <label className="trade-dock-picker" htmlFor="tradedock-select">
            TradeDock
            <select
              id="tradedock-select"
              aria-label="TradeDock station"
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
            >
              {docks.map((d) => (
                <option key={d.station_id} value={d.station_id}>
                  {d.name}
                  {d.tradedock_tier ? ` (Tier ${d.tradedock_tier})` : ''}
                  {typeof d.sector_id === 'number' ? ` · sector ${d.sector_id}` : ''}
                </option>
              ))}
            </select>
          </label>
        )}
      </section>

      {selectedId && (
        <section className="section">
          <div className="section-header">
            <div>
              <h3 className="section-title">{displayName}</h3>
              <p className="section-subtitle">
                Tier {displayTier} · queue {overview?.queue_length ?? '—'}
                {typeof overview?.reservation_count_active === 'number'
                  ? ` · ${overview.reservation_count_active} active`
                  : ''}
              </p>
            </div>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              disabled={!selectedId || loadingDetail}
              onClick={() => void loadOverview(selectedId)}
            >
              Refresh overview
            </button>
          </div>

          {detailError && (
            <div className="alert alert-error" role="alert">
              {detailError}
            </div>
          )}

          {loadingDetail ? (
            <p className="text-muted">Loading overview…</p>
          ) : overview ? (
            <>
              <div
                className="trade-dock-pool-grid"
                role="list"
                aria-label="Slip pool capacity"
              >
                <div
                  className="trade-dock-pool"
                  role="listitem"
                  aria-label={`Standard slips ${poolLabel(overview.slips.standard)}`}
                >
                  <span className="trade-dock-pool-label">Standard</span>
                  <span className="trade-dock-pool-value">
                    {poolLabel(overview.slips.standard)}
                  </span>
                  <span className="trade-dock-pool-meta">in use / capacity</span>
                </div>
                <div
                  className="trade-dock-pool"
                  role="listitem"
                  aria-label={`Specialized slips ${poolLabel(overview.slips.specialized)}`}
                >
                  <span className="trade-dock-pool-label">Specialized</span>
                  <span className="trade-dock-pool-value">
                    {poolLabel(overview.slips.specialized)}
                  </span>
                  <span className="trade-dock-pool-meta">in use / capacity</span>
                </div>
                {slipTotal && (
                  <div
                    className="trade-dock-pool total"
                    role="listitem"
                    aria-label={`Total slips ${slipTotal.in_use} / ${slipTotal.capacity}`}
                  >
                    <span className="trade-dock-pool-label">Total</span>
                    <span className="trade-dock-pool-value">
                      {slipTotal.in_use} / {slipTotal.capacity}
                    </span>
                    <span className="trade-dock-pool-meta">canon 12-slip yard</span>
                  </div>
                )}
              </div>

              <h4 className="trade-dock-queue-title">Active reservations</h4>
              {(overview.reservations?.length ?? 0) === 0 ? (
                <p className="text-muted">No active reservations.</p>
              ) : (
                <div className="levers-table-wrap">
                  <table className="levers-table">
                    <thead>
                      <tr>
                        <th>Ship</th>
                        <th>State</th>
                        <th>Slip</th>
                        <th>Progress</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {overview.reservations.map((r) => (
                        <tr key={r.id}>
                          <td>
                            {r.ship_type}
                            {r.ship_name ? ` · ${r.ship_name}` : ''}
                          </td>
                          <td>{r.state}</td>
                          <td>{r.uses_specialized_slip ? 'specialized' : 'standard'}</td>
                          <td>
                            {typeof r.overall_progress_percent === 'number'
                              ? `${r.overall_progress_percent}%`
                              : '—'}
                          </td>
                          <td>
                            <button
                              type="button"
                              className="btn btn-sm"
                              aria-label={`Open reservation ${r.id}`}
                              onClick={() => void openReservation(r.id)}
                            >
                              Detail
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <h4 className="trade-dock-queue-title">Waiting list</h4>
              {(overview.queue?.length ?? 0) === 0 ? (
                <p className="text-muted">Queue empty.</p>
              ) : (
                <div className="levers-table-wrap">
                  <table className="levers-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Player</th>
                        <th>Ship</th>
                        <th>Priority bumps</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {overview.queue.map((q) => (
                        <tr key={q.reservation_id}>
                          <td>{q.position}</td>
                          <td className="font-mono text-xs">{q.player_id}</td>
                          <td>{q.ship_type}</td>
                          <td>{q.priority_bumps_count ?? 0}</td>
                          <td>
                            <button
                              type="button"
                              className="btn btn-sm"
                              aria-label={`Open queued reservation ${q.reservation_id}`}
                              onClick={() => void openReservation(q.reservation_id)}
                            >
                              Detail
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          ) : null}
        </section>
      )}

      {(reservation || loadingReservation) && (
        <section className="section trade-dock-detail" aria-label="Reservation detail">
          <div className="section-header">
            <div>
              <h3 className="section-title">Reservation detail</h3>
              <p className="section-subtitle">
                Detail view · force-cancel refunds credits via tip cancel_refund
              </p>
            </div>
            <div className="section-actions">
              {reservation && (
                <button
                  type="button"
                  className="btn btn-sm btn-danger"
                  disabled={forceCancelling}
                  aria-label={`Force-cancel reservation ${reservation.id}`}
                  onClick={() => void forceCancelReservation(reservation.id)}
                >
                  {forceCancelling ? 'Force-cancelling…' : 'Force-cancel'}
                </button>
              )}
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setReservation(null)}>
                Close
              </button>
            </div>
          </div>
          {loadingReservation ? (
            <p className="text-muted">Loading reservation…</p>
          ) : reservation ? (
            <dl className="trade-dock-detail-grid">
              <div>
                <dt>ID</dt>
                <dd className="font-mono text-xs">{reservation.id}</dd>
              </div>
              <div>
                <dt>Ship</dt>
                <dd>
                  {reservation.ship_type}
                  {reservation.ship_name ? ` · ${reservation.ship_name}` : ''}
                </dd>
              </div>
              <div>
                <dt>State</dt>
                <dd>
                  {reservation.state}
                  {reservation.paused ? ' (paused)' : ''}
                </dd>
              </div>
              <div>
                <dt>Progress</dt>
                <dd>
                  overall{' '}
                  {typeof reservation.overall_progress_percent === 'number'
                    ? `${reservation.overall_progress_percent}%`
                    : '—'}
                  {typeof reservation.phase_progress_percent === 'number'
                    ? ` · phase ${reservation.phase_progress_percent}%`
                    : ''}
                </dd>
              </div>
              <div>
                <dt>Cost</dt>
                <dd>
                  total {reservation.total_cost?.toLocaleString?.() ?? '—'} · deposit{' '}
                  {reservation.deposit_paid?.toLocaleString?.() ?? '—'} · paid{' '}
                  {reservation.credits_paid?.toLocaleString?.() ?? '—'}
                </dd>
              </div>
              <div>
                <dt>Rent</dt>
                <dd>
                  {reservation.rent
                    ? `owed ${reservation.rent.owed?.toLocaleString?.() ?? '—'} · paid until ${
                        reservation.rent.paid_until || '—'
                      } · overdue ${reservation.rent.overdue_canonical_days ?? 0}d`
                    : 'n/a'}
                </dd>
              </div>
              <div>
                <dt>Phase deadline</dt>
                <dd>{reservation.phase_deadline || 'paused / n/a'}</dd>
              </div>
              <div>
                <dt>Specialized slip</dt>
                <dd>{reservation.uses_specialized_slip ? 'yes' : 'no'}</dd>
              </div>
              {reservation.needs && reservation.needs.length > 0 && (
                <div>
                  <dt>Needs</dt>
                  <dd>{reservation.needs.join('; ')}</dd>
                </div>
              )}
            </dl>
          ) : null}
        </section>
      )}
    </div>
  );
};

export default TradeDockAdmin;

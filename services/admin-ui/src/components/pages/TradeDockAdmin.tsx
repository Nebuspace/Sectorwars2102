import React, { useCallback, useEffect, useMemo, useState } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import { useToast } from '../../contexts/ToastContext';
import './trade-dock-admin.css';

/**
 * LEG-41 — TradeDockAdmin (LEG-14 sub-part B).
 * Canon: FEATURES/economy/tradedock-shipyard.md — 12 construction slips.
 *
 * Expected LEG-40 (gameserver) response shapes — scaffold may 404 until
 * that WO lands; UI remains usable against the contract below.
 *
 * GET  /api/v1/admin/construction/tradedocks
 * GET  /api/v1/admin/construction/tradedocks/{station_id}
 * GET  /api/v1/admin/construction/reservations/{reservation_id}
 */

const SLIP_COUNT = 12;

interface TradeDockSummary {
  id: string;
  name: string;
  tradedock_tier: string | null;
  occupied_slips?: number;
  queue_depth?: number;
}

interface SlipReservationSummary {
  id: string;
  player_id: string;
  player_nickname?: string | null;
  ship_type: string;
  ship_name?: string | null;
  state: string;
  uses_specialized_slip?: boolean;
  rent_owed_since?: string | null;
  phase_deadline?: string | null;
}

interface SlipSlot {
  index: number;
  reservation: SlipReservationSummary | null;
}

interface QueueEntry {
  id: string;
  player_id: string;
  player_nickname?: string | null;
  ship_type: string;
  state: string;
  queue_position?: number;
  priority_bumps_count?: number;
}

interface TradeDockDetail {
  station: TradeDockSummary;
  slips: SlipSlot[];
  queue: QueueEntry[];
  queue_depth: number;
}

interface ReservationDetail extends SlipReservationSummary {
  station_id: string;
  total_cost?: number;
  deposit_paid?: number;
  credits_paid?: number;
  milestones?: Record<string, boolean>;
  resources_required?: Record<string, number>;
  resources_delivered?: Record<string, number>;
  hold_expires_at?: string | null;
  claim_expires_at?: string | null;
  rent_paid_until?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

function detailFromErr(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

function normalizeSlips(raw: SlipSlot[] | undefined): SlipSlot[] {
  const byIndex = new Map<number, SlipSlot>();
  for (const slot of raw ?? []) {
    if (slot && typeof slot.index === 'number') {
      byIndex.set(slot.index, slot);
    }
  }
  return Array.from({ length: SLIP_COUNT }, (_, i) => {
    const index = i + 1;
    return byIndex.get(index) ?? { index, reservation: null };
  });
}

const TradeDockAdmin: React.FC = () => {
  const toast = useToast();
  const [docks, setDocks] = useState<TradeDockSummary[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState<TradeDockDetail | null>(null);
  const [reservation, setReservation] = useState<ReservationDetail | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [loadingReservation, setLoadingReservation] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const loadDocks = useCallback(async () => {
    setLoadingList(true);
    setListError(null);
    try {
      const { data } = await api.get<{ items?: TradeDockSummary[] }>(
        '/api/v1/admin/construction/tradedocks'
      );
      const items = Array.isArray(data?.items) ? data.items : [];
      setDocks(items);
      setSelectedId((prev) => prev || items[0]?.id || '');
    } catch (err: unknown) {
      setDocks([]);
      setListError(
        detailFromErr(
          err,
          'Failed to load TradeDocks (LEG-40 admin construction API may not be live yet)'
        )
      );
    } finally {
      setLoadingList(false);
    }
  }, []);

  const loadDetail = useCallback(async (stationId: string) => {
    if (!stationId) {
      setDetail(null);
      return;
    }
    setLoadingDetail(true);
    setDetailError(null);
    setReservation(null);
    try {
      const { data } = await api.get<TradeDockDetail>(
        `/api/v1/admin/construction/tradedocks/${encodeURIComponent(stationId)}`
      );
      setDetail({
        station: data.station,
        slips: normalizeSlips(data.slips),
        queue: Array.isArray(data.queue) ? data.queue : [],
        queue_depth: data.queue_depth ?? (data.queue?.length ?? 0),
      });
    } catch (err: unknown) {
      setDetail(null);
      setDetailError(detailFromErr(err, 'Failed to load TradeDock slips'));
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    void loadDocks();
  }, [loadDocks]);

  useEffect(() => {
    if (selectedId) {
      void loadDetail(selectedId);
    }
  }, [selectedId, loadDetail]);

  const openReservation = async (reservationId: string) => {
    setLoadingReservation(true);
    try {
      const { data } = await api.get<ReservationDetail>(
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

  const selectedDock = useMemo(
    () => docks.find((d) => d.id === selectedId) ?? detail?.station ?? null,
    [docks, selectedId, detail]
  );

  return (
    <div className="trade-dock-admin" data-testid="trade-dock-admin">
      <PageHeader
        title="TradeDock management"
        subtitle="12-slip shipyard occupancy · queue depth · reservation detail (read-only v1)"
      />

      <section className="section">
        <div className="section-header">
          <div>
            <h3 className="section-title">Station picker</h3>
            <p className="section-subtitle">
              TradeDocks seeded with tradedock_tier (LEG-40 list endpoint)
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
                <option key={d.id} value={d.id}>
                  {d.name}
                  {d.tradedock_tier ? ` (Tier ${d.tradedock_tier})` : ''}
                  {typeof d.queue_depth === 'number' ? ` · queue ${d.queue_depth}` : ''}
                </option>
              ))}
            </select>
          </label>
        )}
      </section>

      {selectedDock && (
        <section className="section">
          <div className="section-header">
            <div>
              <h3 className="section-title">{selectedDock.name}</h3>
              <p className="section-subtitle">
                Tier {selectedDock.tradedock_tier || '—'} · queue{' '}
                {detail?.queue_depth ?? selectedDock.queue_depth ?? '—'}
              </p>
            </div>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              disabled={!selectedId || loadingDetail}
              onClick={() => void loadDetail(selectedId)}
            >
              Refresh slips
            </button>
          </div>

          {detailError && (
            <div className="alert alert-error" role="alert">
              {detailError}
            </div>
          )}

          {loadingDetail ? (
            <p className="text-muted">Loading slips…</p>
          ) : (
            <>
              <div className="trade-dock-slip-grid" role="list" aria-label="Construction slips">
                {(detail?.slips ?? normalizeSlips(undefined)).map((slot) => {
                  const occ = slot.reservation;
                  return (
                    <button
                      key={slot.index}
                      type="button"
                      role="listitem"
                      className={`trade-dock-slip${occ ? ' occupied' : ' empty'}${
                        occ?.rent_owed_since ? ' arrears' : ''
                      }`}
                      disabled={!occ}
                      onClick={() => occ && void openReservation(occ.id)}
                      aria-label={
                        occ
                          ? `Slip ${slot.index}: ${occ.ship_type} (${occ.state})`
                          : `Slip ${slot.index}: empty`
                      }
                    >
                      <span className="trade-dock-slip-index">Slip {slot.index}</span>
                      {occ ? (
                        <>
                          <span className="trade-dock-slip-ship">{occ.ship_type}</span>
                          <span className="trade-dock-slip-meta">
                            {occ.player_nickname || occ.player_id.slice(0, 8)}
                          </span>
                          <span className="trade-dock-slip-state">{occ.state}</span>
                        </>
                      ) : (
                        <span className="trade-dock-slip-meta">Empty</span>
                      )}
                    </button>
                  );
                })}
              </div>

              <h4 className="trade-dock-queue-title">Waiting list</h4>
              {(detail?.queue?.length ?? 0) === 0 ? (
                <p className="text-muted">Queue empty.</p>
              ) : (
                <div className="levers-table-wrap">
                  <table className="levers-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Player</th>
                        <th>Ship</th>
                        <th>State</th>
                        <th>Priority bumps</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {(detail?.queue ?? []).map((q, i) => (
                        <tr key={q.id}>
                          <td>{q.queue_position ?? i + 1}</td>
                          <td>{q.player_nickname || q.player_id}</td>
                          <td>{q.ship_type}</td>
                          <td>{q.state}</td>
                          <td>{q.priority_bumps_count ?? 0}</td>
                          <td>
                            <button
                              type="button"
                              className="btn btn-sm"
                              onClick={() => void openReservation(q.id)}
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
          )}
        </section>
      )}

      {(reservation || loadingReservation) && (
        <section className="section trade-dock-detail" aria-label="Reservation detail">
          <div className="section-header">
            <div>
              <h3 className="section-title">Reservation detail</h3>
              <p className="section-subtitle">Read-only · force-cancel is a follow-up</p>
            </div>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setReservation(null)}>
              Close
            </button>
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
                <dt>Player</dt>
                <dd>
                  {reservation.player_nickname || '—'}
                  <div className="font-mono text-xs text-muted">{reservation.player_id}</div>
                </dd>
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
                <dd>{reservation.state}</dd>
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
                  paid until {reservation.rent_paid_until || '—'}
                  {reservation.rent_owed_since
                    ? ` · arrears since ${reservation.rent_owed_since}`
                    : ''}
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
            </dl>
          ) : null}
        </section>
      )}
    </div>
  );
};

export default TradeDockAdmin;

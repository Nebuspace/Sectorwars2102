import React, { useCallback, useEffect, useState } from 'react';
import CockpitInstrument from '../cockpit/CockpitInstrument';
import EmptyState from '../common/EmptyState';
import LoadingState from '../common/LoadingState';
import {
  regionTakeoverAPI,
  type TakeoverEligibleRegion,
} from '../../services/api';
import './region-takeover-eligible-panel.css';

/**
 * RegionTakeoverEligiblePanel — list suspended/grace regions and begin
 * GC-subscription takeover via PayPal (LEG-3957 Phase 5).
 *
 * Discovery: GET /api/v1/regions/takeover-eligible (LEG-3956).
 * Init: POST /api/v1/regions/{id}/takeover → approval_url redirect (LEG-3764).
 */

export const REGION_TAKEOVER_LOAD_FALLBACK =
  'Failed to load takeover-eligible regions.';

const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
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

function hasTakeoverServerDetail(err: unknown, message: string | undefined): boolean {
  if (err instanceof TypeError) return false;
  if (typeof message === 'string' && isNetworkCollapseMessage(message)) return false;
  return (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  );
}

/** Load-path error formatter (403/429/typeErrorHonesty — LEG-3957). */
export function formatRegionTakeoverLoadError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail = hasTakeoverServerDetail(err, message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'Galactic Citizen subscription required to view takeover-eligible regions.';
  }

  if (status === 429) {
    return 'Region takeover lookup rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return REGION_TAKEOVER_LOAD_FALLBACK;
}

const formatStatusLabel = (status: string): string => {
  const normalized = status.replace(/_/g, ' ');
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
};

interface RegionTakeoverEligiblePanelProps {
  onClose?: () => void;
}

const RegionTakeoverEligiblePanel: React.FC<RegionTakeoverEligiblePanelProps> = ({
  onClose,
}) => {
  const [regions, setRegions] = useState<TakeoverEligibleRegion[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyRegionId, setBusyRegionId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await regionTakeoverAPI.listTakeoverEligible();
      setRegions(Array.isArray(data) ? data : []);
    } catch (err) {
      setLoadError(formatRegionTakeoverLoadError(err));
      setRegions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleBeginTakeover = async (region: TakeoverEligibleRegion) => {
    setBusyRegionId(region.id);
    setActionError(null);
    try {
      const origin = window.location.origin;
      const intent = await regionTakeoverAPI.beginTakeover(region.id, {
        return_url: `${origin}/regions/takeover/success`,
        cancel_url: `${origin}/regions/takeover/cancel`,
      });
      if (intent.approval_url) {
        window.location.href = intent.approval_url;
        return;
      }
      setActionError('PayPal approval URL unavailable — try again shortly.');
    } catch (err) {
      setActionError(formatRegionTakeoverLoadError(err));
    } finally {
      setBusyRegionId(null);
    }
  };

  return (
    <CockpitInstrument
      title="REGION TAKEOVER"
      accent="#FFB020"
      subtitle="GC SUBSCRIPTION ASSUMPTION"
      className="region-takeover-eligible-panel"
    >
      <div className="rte-panel-toolbar">
        {onClose && (
          <button type="button" className="rte-close-btn" onClick={onClose}>
            Close
          </button>
        )}
        <button
          type="button"
          className="rte-refresh-btn"
          onClick={() => void refresh()}
          disabled={loading}
        >
          Refresh
        </button>
      </div>

      {loading && <LoadingState message="Scanning for eligible regions…" />}

      {!loading && loadError && (
        <div className="rte-error" role="alert" data-testid="rte-load-error">
          {loadError}
        </div>
      )}

      {!loading && !loadError && regions.length === 0 && (
        <EmptyState
          icon="◇"
          title="No regions available"
          message="No suspended or grace-period regions are open for takeover right now."
        />
      )}

      {!loading && !loadError && regions.length > 0 && (
        <div className="rte-table-wrap">
          <table className="rte-table">
            <thead>
              <tr>
                <th scope="col">Region</th>
                <th scope="col">Status</th>
                <th scope="col">Action</th>
              </tr>
            </thead>
            <tbody>
              {regions.map((region) => (
                <tr key={region.id} data-testid={`rte-row-${region.id}`}>
                  <td>{region.display_name || region.name}</td>
                  <td>
                    <span className={`rte-status rte-status--${region.status}`}>
                      {formatStatusLabel(region.status)}
                    </span>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="rte-takeover-btn"
                      disabled={busyRegionId === region.id}
                      onClick={() => void handleBeginTakeover(region)}
                    >
                      {busyRegionId === region.id ? 'Redirecting…' : 'Begin Takeover'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {actionError && (
        <div className="rte-error rte-action-error" role="alert">
          {actionError}
        </div>
      )}

      <p className="rte-footnote">
        Assumption requires an active Galactic Citizen subscription. You will be
        redirected to PayPal to authorize the regional owner subscription — no
        price is shown here; billing follows the live PayPal plan.
      </p>
    </CockpitInstrument>
  );
};

export default RegionTakeoverEligiblePanel;

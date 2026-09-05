import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import { PlayerModel } from '../../types/playerManagement';
import './player-asset-manager.css';

/** Non-empty outlaw_base_id for deep-link; null when absent/blank (LEG-4227). */
function outlawBaseIdForLink(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const s = String(value).trim();
  return s === '' ? null : s;
}

interface PlayerAssetManagerProps {
  player: PlayerModel;
  onClose: () => void;
  onUpdate: (updatedPlayer: PlayerModel) => void;
}

interface OwnedAssets {
  ships: any[];
  planets: any[];
  ports: any[];
}

function asIntegerSectorId(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n) || !Number.isInteger(n)) return null;
  return n;
}

/** Collect unique integer sector ids from owned planets/ports (LEG-4193). */
function collectAssetSectorIds(planets: any[], ports: any[]): number[] {
  const ids = new Set<number>();
  for (const asset of [...planets, ...ports]) {
    const sid = asIntegerSectorId(asset?.sector_id);
    if (sid !== null) ids.add(sid);
  }
  return [...ids];
}

/**
 * One paginated GET /admin/sectors scan — uses list-level has_pirate_holding.
 * Never N+1 GET /admin/sectors/{id}/pirate-holdings per asset row.
 */
async function resolveHoldingSectorIds(sectorIds: number[]): Promise<{
  held: Set<number>;
  error: string | null;
}> {
  const pending = new Set(sectorIds);
  const held = new Set<number>();
  if (pending.size === 0) {
    return { held, error: null };
  }

  const limit = 100;
  try {
    for (let page = 1; page <= 50 && pending.size > 0; page += 1) {
      const response = await api.get('/api/v1/admin/sectors', {
        params: { page, limit },
      });
      const data = response.data as {
        sectors?: Array<{ sector_id?: unknown; has_pirate_holding?: unknown }>;
        total?: number;
        total_count?: number;
      };
      const sectors = data.sectors || [];
      for (const sector of sectors) {
        const sid = asIntegerSectorId(sector.sector_id);
        if (sid === null || !pending.has(sid)) continue;
        pending.delete(sid);
        if (sector.has_pirate_holding === true) {
          held.add(sid);
        }
      }
      const total = data.total ?? data.total_count;
      if (sectors.length < limit) break;
      if (typeof total === 'number' && page * limit >= total) break;
    }
    return { held, error: null };
  } catch (err: unknown) {
    return {
      held,
      error: formatAdminApiError(err, {
        fallback: 'Failed to load pirate-holding sector flags',
        scopeHint: 'admin.galaxy.manage scope required to list sectors',
      }),
    };
  }
}

/**
 * Honesty: assign/remove backend routes do not exist.
 * Keep owned-asset reads; do not invent selection / assign / remove chrome
 * or fetch unowned pools that only exist to feed dead write actions.
 */
const PlayerAssetManager: React.FC<PlayerAssetManagerProps> = ({
  player,
  onClose,
  onUpdate: _onUpdate
}) => {
  const [assets, setAssets] = useState<OwnedAssets>({
    ships: [],
    planets: [],
    ports: []
  });

  const [activeTab, setActiveTab] = useState<'ships' | 'planets' | 'ports' | 'holdings'>('ships');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [holdingSectorIds, setHoldingSectorIds] = useState<Set<number>>(new Set());
  const [holdingsError, setHoldingsError] = useState<string | null>(null);
  const [ownedHoldings, setOwnedHoldings] = useState<any[]>([]);
  const [ownedHoldingsLoading, setOwnedHoldingsLoading] = useState(false);
  const [ownedHoldingsError, setOwnedHoldingsError] = useState<string | null>(null);
  const [ownedHoldingsLoadedFor, setOwnedHoldingsLoadedFor] = useState<string | null>(null);

  const ASSET_ASSIGN_ENDPOINT = 'POST /api/v1/admin/players/{id}/assets/assign';
  const ASSET_REMOVE_ENDPOINT = 'POST /api/v1/admin/players/{id}/assets/remove';

  useEffect(() => {
    loadPlayerAssets();
  }, [player.id]);

  useEffect(() => {
    if (activeTab !== 'holdings') return;
    if (ownedHoldingsLoadedFor === player.id) return;

    let cancelled = false;
    const loadOwnedHoldings = async () => {
      setOwnedHoldingsLoading(true);
      setOwnedHoldingsError(null);
      try {
        const response = await api.get('/api/v1/admin/pirate-holdings', {
          params: { owner_player_id: player.id },
        });
        if (cancelled) return;
        const holdings = (response.data as { holdings?: unknown[] })?.holdings;
        setOwnedHoldings(Array.isArray(holdings) ? holdings : []);
        setOwnedHoldingsLoadedFor(player.id);
      } catch (err: unknown) {
        if (cancelled) return;
        setOwnedHoldings([]);
        setOwnedHoldingsLoadedFor(null);
        setOwnedHoldingsError(
          formatAdminApiError(err, {
            fallback: 'Failed to load owned pirate holdings',
            scopeHint: 'PLAYERS_VIEW scope required to list pirate holdings by owner',
          }),
        );
      } finally {
        if (!cancelled) setOwnedHoldingsLoading(false);
      }
    };
    void loadOwnedHoldings();
    return () => {
      cancelled = true;
    };
  }, [activeTab, player.id, ownedHoldingsLoadedFor]);

  const loadPlayerAssets = async () => {
    setLoading(true);
    setError(null);
    setHoldingsError(null);
    setOwnedHoldings([]);
    setOwnedHoldingsError(null);
    setOwnedHoldingsLoadedFor(null);
    try {
      const [shipsRes, planetsRes, portsRes] = await Promise.all([
        api.get(`/api/v1/admin/ships?ownerId=${player.id}`),
        api.get(`/api/v1/admin/planets?owner_id=${player.id}`),
        api.get(`/api/v1/admin/ports?owner_id=${player.id}`)
      ]);

      const planets = (planetsRes.data as any)?.planets || [];
      const ports = (portsRes.data as any)?.ports || [];
      setAssets({
        ships: (shipsRes.data as any)?.ships || [],
        planets,
        ports
      });

      const sectorIds = collectAssetSectorIds(planets, ports);
      const { held, error: holdingsLoadError } = await resolveHoldingSectorIds(sectorIds);
      setHoldingSectorIds(held);
      setHoldingsError(holdingsLoadError);
    } catch (err: unknown) {
      console.error('Failed to load player assets:', err);
      setError(
        formatAdminApiError(err, {
          fallback: 'Gameserver unreachable — network error loading player assets',
          scopeHint:
            'loading player assets requires the admin players view scope (PLAYERS_VIEW).',
        })
      );
      setHoldingSectorIds(new Set());
    } finally {
      setLoading(false);
    }
  };

  const renderAssetList = (assetList: any[]) => {
    if (assetList.length === 0) {
      return (
        <div className="empty-state">
          <p>No {activeTab} owned</p>
        </div>
      );
    }

    return (
      <div className="asset-list">
        {assetList.map((asset) => {
          const sectorId = asIntegerSectorId(asset.sector_id);
          const showHolding =
            (activeTab === 'planets' || activeTab === 'ports') &&
            sectorId !== null &&
            holdingSectorIds.has(sectorId);

          return (
            <div key={asset.id} className="asset-item" data-testid={`asset-row-${asset.id}`}>
              <div className="asset-info">
                <div className="asset-header">
                  <h4>{asset.name}</h4>
                  <span className="asset-type">
                    {activeTab === 'ships' && asset.ship_type}
                    {activeTab === 'planets' && asset.planet_type}
                    {activeTab === 'ports' && `Class ${asset.port_class}`}
                  </span>
                  {showHolding ? (
                    <span
                      className="badge badge-warning"
                      data-testid={`pirate-holding-badge-${asset.id}`}
                      title="Pirate holding in this sector"
                    >
                      Holding
                    </span>
                  ) : null}
                </div>

                <div className="asset-details">
                  {activeTab === 'ships' && (
                    <>
                      <span>Location: Sector {asset.current_sector_id || 'Unknown'}</span>
                      <span>Condition: {asset.condition || 100}%</span>
                      <span>Cargo: {asset.cargo_used || 0}/{asset.cargo_capacity || 0}</span>
                    </>
                  )}

                  {activeTab === 'planets' && (
                    <>
                      <span>Sector: {asset.sector_id || 'Unknown'}</span>
                      <span>Citadel: Level {asset.citadel_level || 0}</span>
                      <span>Population: {(asset.total_colonists || 0).toLocaleString()}</span>
                    </>
                  )}

                  {activeTab === 'ports' && (
                    <>
                      <span>Sector: {asset.sector_id || 'Unknown'}</span>
                      <span>Tax Rate: {asset.tax_rate || 0}%</span>
                      <span>Drones: {asset.defense_fighters || 0}</span>
                    </>
                  )}
                </div>
              </div>

              <div className="asset-value">
                {asset.estimated_value && (
                  <span className="value">{asset.estimated_value.toLocaleString()} credits</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  const renderOwnedHoldings = () => {
    if (ownedHoldingsLoading) {
      return (
        <div className="empty-state" data-testid="owned-pirate-holdings-loading">
          <p>Loading owned pirate holdings…</p>
        </div>
      );
    }
    if (ownedHoldingsError) {
      return (
        <div
          role="alert"
          className="error-banner"
          data-testid="owned-pirate-holdings-error"
        >
          {ownedHoldingsError}
        </div>
      );
    }
    if (ownedHoldings.length === 0) {
      return (
        <div className="empty-state" data-testid="owned-pirate-holdings-empty">
          <p>No pirate holdings owned by this player.</p>
        </div>
      );
    }
    return (
      <div className="asset-list" data-testid="owned-pirate-holdings-list">
        {ownedHoldings.map((holding) => {
          const outlawBaseId = outlawBaseIdForLink(holding.outlaw_base_id);
          return (
          <div
            key={holding.id}
            className="asset-item"
            data-testid={`owned-pirate-holding-row-${holding.id}`}
          >
            <div className="asset-info">
              <div className="asset-details">
                <span>id: {holding.id}</span>
                <span>sector_id: {holding.sector_id ?? '—'}</span>
                <span>tier: {holding.tier ?? '—'}</span>
                <span>
                  outlaw_base_id:{' '}
                  {outlawBaseId ? (
                    <Link
                      to={`/outlaw-bases/${outlawBaseId}`}
                      data-testid={`owned-pirate-holding-outlaw-base-link-${holding.id}`}
                    >
                      {outlawBaseId}
                    </Link>
                  ) : (
                    '—'
                  )}
                </span>
                <span>current_strength: {holding.current_strength ?? '—'}</span>
              </div>
            </div>
          </div>
          );
        })}
      </div>
    );
  };

  if (loading) {
    return (
      <div className="player-asset-manager loading">
        <div className="loading-spinner">
          <div className="spinner"></div>
          <span>Loading player assets...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="player-asset-manager" onClick={(e) => e.stopPropagation()}>
        <div className="manager-header">
          <h3>Asset Manager: {player.username}</h3>
          <button onClick={onClose} className="close-btn">×</button>
        </div>
        <div role="alert" className="error-banner" style={{ margin: '16px' }}>
          {error}
        </div>
        <div style={{ margin: '0 16px 16px' }}>
          <button type="button" onClick={() => void loadPlayerAssets()}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  const currentAssets = activeTab === 'holdings' ? [] : assets[activeTab];

  return (
    <div className="player-asset-manager" onClick={(e) => e.stopPropagation()}>
      <div className="manager-header">
        <h3>Asset Manager: {player.username}</h3>
        <button onClick={onClose} className="close-btn">×</button>
      </div>

      <div
        role="note"
        style={{
          margin: '12px 16px 0', padding: '10px 12px',
          background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
          borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
        }}
      >
        Asset assign/remove is unavailable: the backend endpoints{' '}
        <code style={{ color: '#fde68a' }}>{ASSET_ASSIGN_ENDPOINT}</code> and{' '}
        <code style={{ color: '#fde68a' }}>{ASSET_REMOVE_ENDPOINT}</code> are not implemented.
        Owned listings below are read-only — this panel does not invent selection or assign/remove controls.
      </div>

      {holdingsError ? (
        <div
          role="alert"
          className="error-banner"
          data-testid="pirate-holdings-flag-error"
          style={{ margin: '12px 16px 0' }}
        >
          {holdingsError}
        </div>
      ) : null}

      <div className="asset-tabs">
        <button
          className={`tab ${activeTab === 'ships' ? 'active' : ''}`}
          onClick={() => setActiveTab('ships')}
        >
          🚀 Ships ({assets.ships.length})
        </button>
        <button
          className={`tab ${activeTab === 'planets' ? 'active' : ''}`}
          onClick={() => setActiveTab('planets')}
        >
          🌍 Planets ({assets.planets.length})
        </button>
        <button
          className={`tab ${activeTab === 'ports' ? 'active' : ''}`}
          onClick={() => setActiveTab('ports')}
        >
          🏪 Ports ({assets.ports.length})
        </button>
        <button
          type="button"
          className={`tab ${activeTab === 'holdings' ? 'active' : ''}`}
          onClick={() => setActiveTab('holdings')}
          data-testid="owned-pirate-holdings-tab"
        >
          🏴 Pirate Holdings
          {ownedHoldingsLoadedFor === player.id ? ` (${ownedHoldings.length})` : ''}
        </button>
      </div>

      <div className="asset-sections">
        <div className="owned-section">
          <div className="section-header">
            <h4>
              {activeTab === 'holdings'
                ? 'Owned Pirate Holdings'
                : `Owned ${activeTab.charAt(0).toUpperCase() + activeTab.slice(1)}`}
            </h4>
          </div>
          {activeTab === 'holdings' ? renderOwnedHoldings() : renderAssetList(currentAssets)}
        </div>
      </div>

      <div className="manager-footer">
        <div className="asset-summary">
          <div className="summary-item">
            <span className="label">Total Assets:</span>
            <span className="value">
              {assets.ships.length + assets.planets.length + assets.ports.length}
            </span>
          </div>
          <div className="summary-item">
            <span className="label">Estimated Value:</span>
            <span className="value">
              {(
                [...assets.ships, ...assets.planets, ...assets.ports]
                  .reduce((sum, asset) => sum + (asset.estimated_value || 0), 0)
              ).toLocaleString()} credits
            </span>
          </div>
        </div>

        <button onClick={onClose} className="btn btn-primary">
          Close
        </button>
      </div>
    </div>
  );
};

export default PlayerAssetManager;

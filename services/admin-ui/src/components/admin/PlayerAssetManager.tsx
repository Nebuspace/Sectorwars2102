import React, { useState, useEffect } from 'react';
import { api } from '../../utils/auth';
import { PlayerModel } from '../../types/playerManagement';
import './player-asset-manager.css';

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

  const [activeTab, setActiveTab] = useState<'ships' | 'planets' | 'ports'>('ships');
  const [loading, setLoading] = useState(true);

  const ASSET_ASSIGN_ENDPOINT = 'POST /api/v1/admin/players/{id}/assets/assign';
  const ASSET_REMOVE_ENDPOINT = 'POST /api/v1/admin/players/{id}/assets/remove';

  useEffect(() => {
    loadPlayerAssets();
  }, [player.id]);

  const loadPlayerAssets = async () => {
    setLoading(true);
    try {
      const [shipsRes, planetsRes, portsRes] = await Promise.all([
        api.get(`/api/v1/admin/ships?ownerId=${player.id}`),
        api.get(`/api/v1/admin/planets?owner_id=${player.id}`),
        api.get(`/api/v1/admin/ports?owner_id=${player.id}`)
      ]);

      setAssets({
        ships: (shipsRes.data as any)?.ships || [],
        planets: (planetsRes.data as any)?.planets || [],
        ports: (portsRes.data as any)?.ports || []
      });
    } catch (error) {
      console.error('Failed to load player assets:', error);
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
        {assetList.map((asset) => (
          <div key={asset.id} className="asset-item">
            <div className="asset-info">
              <div className="asset-header">
                <h4>{asset.name}</h4>
                <span className="asset-type">
                  {activeTab === 'ships' && asset.ship_type}
                  {activeTab === 'planets' && asset.planet_type}
                  {activeTab === 'ports' && `Class ${asset.port_class}`}
                </span>
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
        ))}
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

  const currentAssets = assets[activeTab];

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
      </div>

      <div className="asset-sections">
        <div className="owned-section">
          <div className="section-header">
            <h4>Owned {activeTab.charAt(0).toUpperCase() + activeTab.slice(1)}</h4>
          </div>
          {renderAssetList(currentAssets)}
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

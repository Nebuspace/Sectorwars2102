import React, { useState, useEffect, useCallback } from 'react';
import { shipUpgradeAPI } from '../../services/api';
import './upgrade-interface.css';

interface UpgradeInfo {
  current_level: number;
  max_level: number;
  at_max: boolean;
  next_cost: number | null;
  effect_per_level: Record<string, number>;
  description: string;
}

interface EquipmentInfo {
  name: string;
  description: string;
  cost: number;
  compatible: boolean;
  installed: boolean;
  effects: Record<string, number>;
}

interface UpgradeData {
  success: boolean;
  ship_id: string;
  ship_name: string;
  ship_type: string;
  upgrades: Record<string, UpgradeInfo>;
  equipment: Record<string, EquipmentInfo>;
  equipped: Record<string, any>;
  player_credits: number;
}

// The component only ever reads `ship.id`; it fetches all upgrade/equipment/
// credit data itself from the live ship-upgrades endpoints. Keep the prop
// surface to exactly what is used so any caller with an `{ id }` can mount it.
interface UpgradeInterfaceProps {
  ship: { id: string };
}

const UPGRADE_ICONS: Record<string, string> = {
  engine: '🚀',
  cargo_hold: '📦',
  shield: '🛡️',
  hull: '🔧',
  sensor: '📡',
  drone_bay: '🤖',
  genesis_containment: '🌍',
};

const EQUIPMENT_ICONS: Record<string, string> = {
  quantum_harvester: '⚡',
  mining_laser: '⛏️',
  planetary_lander: '🛬',
};

const UpgradeInterface: React.FC<UpgradeInterfaceProps> = ({ ship }) => {
  const [data, setData] = useState<UpgradeData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTab, setSelectedTab] = useState<'upgrades' | 'equipment'>('upgrades');
  const [actionLoading, setActionLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<number>(0);

  const RATE_LIMIT_MS = 1000;
  const canPerformAction = useCallback(() => {
    const now = Date.now();
    if (now - lastAction < RATE_LIMIT_MS) return false;
    setLastAction(now);
    return true;
  }, [lastAction]);

  const fetchUpgradeData = useCallback(async () => {
    if (!ship?.id) return;
    try {
      setLoading(true);
      const result = await shipUpgradeAPI.getUpgrades(ship.id);
      setData(result);
      setError(null);
    } catch (err: any) {
      setError(err.message || 'Failed to load upgrade data');
    } finally {
      setLoading(false);
    }
  }, [ship?.id]);

  useEffect(() => {
    fetchUpgradeData();
  }, [fetchUpgradeData]);

  const handlePurchaseUpgrade = async (upgradeType: string) => {
    if (!canPerformAction() || actionLoading || !ship?.id) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      const result = await shipUpgradeAPI.purchaseUpgrade(ship.id, upgradeType);
      if (result.success) {
        setActionMessage(`${result.message}`);
        await fetchUpgradeData();
      } else {
        setActionMessage(result.message || 'Purchase failed');
      }
    } catch (err: any) {
      setActionMessage(err.message || 'Purchase failed');
    } finally {
      setActionLoading(false);
    }
  };

  const handleInstallEquipment = async (equipmentKey: string) => {
    if (!canPerformAction() || actionLoading || !ship?.id) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      const result = await shipUpgradeAPI.installEquipment(ship.id, equipmentKey);
      if (result.success) {
        setActionMessage(result.message);
        await fetchUpgradeData();
      } else {
        setActionMessage(result.message || 'Install failed');
      }
    } catch (err: any) {
      setActionMessage(err.message || 'Install failed');
    } finally {
      setActionLoading(false);
    }
  };

  const handleUninstallEquipment = async (equipmentKey: string) => {
    if (!canPerformAction() || actionLoading || !ship?.id) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      const result = await shipUpgradeAPI.uninstallEquipment(ship.id, equipmentKey);
      if (result.success) {
        setActionMessage(result.message);
        await fetchUpgradeData();
      } else {
        setActionMessage(result.message || 'Uninstall failed');
      }
    } catch (err: any) {
      setActionMessage(err.message || 'Uninstall failed');
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="upgrade-interface">
        <div className="interface-header">
          <h3>Ship Upgrades</h3>
        </div>
        <div className="upgrade-loading">Loading upgrade data...</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="upgrade-interface">
        <div className="interface-header">
          <h3>Ship Upgrades</h3>
        </div>
        <div className="upgrade-error">
          <span>{error || 'Upgrade data unavailable'}</span>
          <button onClick={fetchUpgradeData} className="retry-btn">Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className="upgrade-interface">
      <div className="interface-header">
        <h3>Ship Upgrades</h3>
        <div className="ship-info">
          <span className="ship-name">{data.ship_name}</span>
          <span className="player-credits">Credits: {data.player_credits.toLocaleString()}</span>
        </div>
      </div>

      <div className="upgrade-categories">
        <button
          className={`category-btn ${selectedTab === 'upgrades' ? 'active' : ''}`}
          onClick={() => setSelectedTab('upgrades')}
        >
          Upgrades
        </button>
        <button
          className={`category-btn ${selectedTab === 'equipment' ? 'active' : ''}`}
          onClick={() => setSelectedTab('equipment')}
        >
          Equipment
        </button>
      </div>

      {actionMessage && (
        <div className="action-message">{actionMessage}</div>
      )}

      <div className="upgrade-content">
        {selectedTab === 'upgrades' && (
          <div className="upgrades-list">
            <h4>Ship Upgrades</h4>
            <div className="upgrades-grid">
              {Object.entries(data.upgrades).map(([type, info]) => (
                <div
                  key={type}
                  className={`upgrade-card ${info.at_max ? 'maxed' : ''}`}
                >
                  <div className="upgrade-header">
                    <h5>{UPGRADE_ICONS[type] || '⬆️'} {type.replace(/_/g, ' ')}</h5>
                    <span className="upgrade-tier">
                      Lv {info.current_level}/{info.max_level}
                    </span>
                  </div>
                  <p className="upgrade-description">{info.description}</p>
                  <div className="upgrade-effects">
                    {Object.entries(info.effect_per_level).map(([stat, value]) => (
                      <div key={stat} className="effect-item">
                        <span className="effect-stat">{stat.replace(/_/g, ' ')}:</span>
                        <span className="effect-value">+{value} /level</span>
                      </div>
                    ))}
                  </div>
                  <div className="upgrade-level-bar">
                    <div
                      className="level-fill"
                      style={{
                        width: info.max_level > 0
                          ? `${(info.current_level / info.max_level) * 100}%`
                          : '0%',
                      }}
                    />
                  </div>
                  <div className="upgrade-footer">
                    {!info.at_max && info.next_cost !== null ? (
                      <>
                        <div className="upgrade-cost">
                          <span className="cost-value">{info.next_cost.toLocaleString()}</span>
                          <span className="cost-label">credits</span>
                        </div>
                        <button
                          className="install-btn"
                          onClick={() => handlePurchaseUpgrade(type)}
                          disabled={actionLoading || data.player_credits < info.next_cost!}
                        >
                          {data.player_credits < info.next_cost! ? 'Need Credits' : 'Upgrade'}
                        </button>
                      </>
                    ) : (
                      <span className="maxed-label">MAX LEVEL</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {selectedTab === 'equipment' && (
          <div className="upgrades-list">
            <h4>Equipment</h4>
            <div className="upgrades-grid">
              {Object.entries(data.equipment).map(([key, eq]) => (
                <div
                  key={key}
                  className={`upgrade-card ${eq.installed ? 'installed' : ''} ${!eq.compatible ? 'unavailable' : ''}`}
                >
                  <div className="upgrade-header">
                    <h5>{EQUIPMENT_ICONS[key] || '🔩'} {eq.name}</h5>
                    {eq.installed && <span className="installed-badge">Installed</span>}
                  </div>
                  <p className="upgrade-description">{eq.description}</p>
                  <div className="upgrade-effects">
                    {Object.entries(eq.effects).map(([stat, value]) => (
                      <div key={stat} className="effect-item">
                        <span className="effect-stat">{stat.replace(/_/g, ' ')}:</span>
                        <span className="effect-value">
                          {typeof value === 'number' && value > 1 ? `x${value}` : `+${value}`}
                        </span>
                      </div>
                    ))}
                  </div>
                  <div className="upgrade-footer">
                    {eq.installed ? (
                      <button
                        className="remove-btn"
                        onClick={() => handleUninstallEquipment(key)}
                        disabled={actionLoading}
                      >
                        Uninstall
                      </button>
                    ) : eq.compatible ? (
                      <>
                        <div className="upgrade-cost">
                          <span className="cost-value">{eq.cost.toLocaleString()}</span>
                          <span className="cost-label">credits</span>
                        </div>
                        <button
                          className="install-btn"
                          onClick={() => handleInstallEquipment(key)}
                          disabled={actionLoading || data.player_credits < eq.cost}
                        >
                          {data.player_credits < eq.cost ? 'Need Credits' : 'Install'}
                        </button>
                      </>
                    ) : (
                      <span className="unavailable-reason">Incompatible Ship</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default UpgradeInterface;

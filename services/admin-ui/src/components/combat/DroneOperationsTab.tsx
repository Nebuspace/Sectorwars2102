import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import './drone-operations.css';

// =============================================================================
// Types — mirror the response shapes in
// services/gameserver/src/api/routes/admin_drones.py
// =============================================================================

// Matches the per-drone dict returned by GET /admin/drones/
interface AdminDrone {
  id: string;
  player_id: string;
  team_id: string | null;
  drone_type: string;
  name: string;
  level: number;
  health: number;
  max_health: number;
  attack_power: number;
  defense_power: number;
  speed: number;
  status: string;
  sector_id: string | null;
  deployed_at: string | null;
  last_action: string | null;
  kills: number;
  damage_dealt: number;
  damage_taken: number;
  battles_fought: number;
  abilities: string | null;
  created_at: string;
  destroyed_at: string | null;
}

// Matches DroneStatistics in admin_drones.py
interface DroneStatistics {
  total_drones: number;
  active_drones: number;
  destroyed_drones: number;
  deployed_drones: number;
  in_combat_drones: number;
  drones_by_type: Record<string, number>;
  average_level: number;
  total_kills: number;
  total_battles: number;
}

const DroneOperationsTab: React.FC = () => {
  const toast = useToast();
  const confirm = useConfirm();

  const [stats, setStats] = useState<DroneStatistics | null>(null);
  const [drones, setDrones] = useState<AdminDrone[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actioningId, setActioningId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);

    const [statsRes, dronesRes] = await Promise.allSettled([
      api.get<DroneStatistics>('/api/v1/admin/drones/statistics'),
      api.get<AdminDrone[]>('/api/v1/admin/drones/'),
    ]);

    if (statsRes.status === 'fulfilled') {
      setStats(statsRes.value.data);
    } else {
      setStats(null);
    }

    if (dronesRes.status === 'fulfilled') {
      setDrones(dronesRes.value.data);
    } else {
      setDrones([]);
    }

    const failed = [statsRes, dronesRes].filter((r) => r.status === 'rejected');
    if (failed.length === 2) {
      setError('Failed to load drone operations data.');
    } else if (failed.length > 0) {
      setError('Some drone operations data could not be loaded.');
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleForceRecall = useCallback(
    async (drone: AdminDrone) => {
      const ok = await confirm({
        title: 'Force Recall Drone',
        message: `Force-recall "${drone.name}" from its current deployment? This is an admin override and is logged.`,
        confirmLabel: 'Force Recall',
        cancelLabel: 'Cancel',
        danger: true,
      });
      if (!ok) return;

      setActioningId(drone.id);
      try {
        await api.post(`/api/v1/admin/drones/${drone.id}/force-recall`);
        toast.success(`Drone "${drone.name}" recalled.`);
        await loadData();
      } catch (err) {
        const detail =
          (err as { response?: { data?: { detail?: string } } }).response?.data
            ?.detail ?? 'Failed to force-recall drone.';
        toast.error(detail);
      } finally {
        setActioningId(null);
      }
    },
    [confirm, toast, loadData]
  );

  const handleRestore = useCallback(
    async (drone: AdminDrone) => {
      const ok = await confirm({
        title: 'Restore Drone',
        message: `Restore destroyed drone "${drone.name}" to active status? This is an admin override and is logged.`,
        confirmLabel: 'Restore',
        cancelLabel: 'Cancel',
      });
      if (!ok) return;

      setActioningId(drone.id);
      try {
        await api.post(`/api/v1/admin/drones/${drone.id}/restore`);
        toast.success(`Drone "${drone.name}" restored.`);
        await loadData();
      } catch (err) {
        const detail =
          (err as { response?: { data?: { detail?: string } } }).response?.data
            ?.detail ?? 'Failed to restore drone.';
        toast.error(detail);
      } finally {
        setActioningId(null);
      }
    },
    [confirm, toast, loadData]
  );

  const activeDrones = drones.filter((d) => d.status !== 'destroyed');
  const byTypeEntries = stats
    ? Object.entries(stats.drones_by_type).sort((a, b) => b[1] - a[1])
    : [];

  if (loading) {
    return (
      <div className="drone-operations">
        <div className="drone-ops-loading">
          <div className="loading-spinner"></div>
          <span>Loading drone operations...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="drone-operations">
      {error && (
        <div className="drone-ops-alert error">
          <span>⚠️</span>
          <span className="drone-ops-alert-spacer">{error}</span>
          <button onClick={loadData}>Retry</button>
        </div>
      )}

      <div className="drone-ops-toolbar">
        <button
          className="drone-ops-refresh-btn"
          onClick={loadData}
          disabled={loading}
        >
          🔄 Refresh
        </button>
      </div>

      {/* Stats summary row */}
      {stats && (
        <div className="drone-ops-stats-grid">
          <div className="drone-ops-stat">
            <div className="drone-ops-stat-label">Total Drones</div>
            <div className="drone-ops-stat-value">
              {stats.total_drones.toLocaleString()}
            </div>
            <div className="drone-ops-stat-sub">all-time</div>
          </div>

          <div className="drone-ops-stat">
            <div className="drone-ops-stat-label">Active</div>
            <div className="drone-ops-stat-value">
              {stats.active_drones.toLocaleString()}
            </div>
            <div className="drone-ops-stat-sub">not destroyed</div>
          </div>

          <div className="drone-ops-stat">
            <div className="drone-ops-stat-label">Deployed</div>
            <div className="drone-ops-stat-value">
              {stats.deployed_drones.toLocaleString()}
            </div>
            <div className="drone-ops-stat-sub">in the field</div>
          </div>

          <div className="drone-ops-stat">
            <div className="drone-ops-stat-label">In Combat</div>
            <div
              className={`drone-ops-stat-value${
                stats.in_combat_drones > 0 ? ' danger' : ''
              }`}
            >
              {stats.in_combat_drones.toLocaleString()}
            </div>
            <div className="drone-ops-stat-sub">currently fighting</div>
          </div>

          <div className="drone-ops-stat">
            <div className="drone-ops-stat-label">Destroyed</div>
            <div className="drone-ops-stat-value">
              {stats.destroyed_drones.toLocaleString()}
            </div>
            <div className="drone-ops-stat-sub">lost in action</div>
          </div>

          <div className="drone-ops-stat">
            <div className="drone-ops-stat-label">Average Level</div>
            <div className="drone-ops-stat-value">
              {stats.average_level.toFixed(1)}
            </div>
            <div className="drone-ops-stat-sub">
              {stats.total_kills.toLocaleString()} kills /{' '}
              {stats.total_battles.toLocaleString()} battles
            </div>
          </div>
        </div>
      )}

      {/* By-type breakdown */}
      <div className="drone-ops-panel">
        <div className="drone-ops-panel-header">
          <h4>🧬 Drones by Type</h4>
          <span className="drone-ops-count">{byTypeEntries.length}</span>
        </div>

        {byTypeEntries.length === 0 ? (
          <div className="drone-ops-empty">No drones.</div>
        ) : (
          <div className="drone-ops-type-grid">
            {byTypeEntries.map(([type, count]) => (
              <div className="drone-ops-type-chip" key={type}>
                <span className="drone-ops-type-name">
                  {type.replace(/_/g, ' ')}
                </span>
                <span className="drone-ops-type-count">
                  {count.toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Active drones list */}
      <div className="drone-ops-panel">
        <div className="drone-ops-panel-header">
          <h4>🛸 Active Drones</h4>
          <span className="drone-ops-count">{activeDrones.length}</span>
        </div>

        {activeDrones.length === 0 ? (
          <div className="drone-ops-empty">No drones.</div>
        ) : (
          <div className="drone-ops-table-container">
            <table className="drone-ops-table">
              <thead>
                <tr>
                  <th>Drone</th>
                  <th>Type</th>
                  <th>Level</th>
                  <th>Status</th>
                  <th>Health</th>
                  <th>Kills</th>
                  <th>Battles</th>
                  <th>Sector</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {activeDrones.map((drone) => {
                  const isDeployedOrCombat =
                    drone.status === 'deployed' || drone.status === 'combat';
                  const isActioning = actioningId === drone.id;
                  return (
                    <tr key={drone.id}>
                      <td>{drone.name}</td>
                      <td>{drone.drone_type.replace(/_/g, ' ')}</td>
                      <td className="mono">{drone.level}</td>
                      <td>
                        <span
                          className={`drone-ops-badge${
                            drone.status === 'combat'
                              ? ' battle'
                              : drone.status === 'deployed'
                              ? ' deployed'
                              : ' active'
                          }`}
                        >
                          {drone.status.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td className="mono">
                        {drone.health}/{drone.max_health}
                      </td>
                      <td className="mono">{drone.kills}</td>
                      <td className="mono">{drone.battles_fought}</td>
                      <td>{drone.sector_id ?? '—'}</td>
                      <td>
                        {isDeployedOrCombat ? (
                          <button
                            className="drone-ops-action-btn recall"
                            onClick={() => handleForceRecall(drone)}
                            disabled={isActioning}
                          >
                            {isActioning ? 'Working…' : 'Force Recall'}
                          </button>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Destroyed drones (restore) */}
      {(() => {
        const destroyed = drones.filter((d) => d.status === 'destroyed');
        return (
          <div className="drone-ops-panel">
            <div className="drone-ops-panel-header">
              <h4>💥 Destroyed Drones</h4>
              <span className="drone-ops-count">{destroyed.length}</span>
            </div>

            {destroyed.length === 0 ? (
              <div className="drone-ops-empty">No destroyed drones.</div>
            ) : (
              <div className="drone-ops-table-container">
                <table className="drone-ops-table">
                  <thead>
                    <tr>
                      <th>Drone</th>
                      <th>Type</th>
                      <th>Level</th>
                      <th>Kills</th>
                      <th>Destroyed</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {destroyed.map((drone) => {
                      const isActioning = actioningId === drone.id;
                      return (
                        <tr key={drone.id}>
                          <td>{drone.name}</td>
                          <td>{drone.drone_type.replace(/_/g, ' ')}</td>
                          <td className="mono">{drone.level}</td>
                          <td className="mono">{drone.kills}</td>
                          <td className="mono">
                            {drone.destroyed_at
                              ? new Date(drone.destroyed_at).toLocaleString()
                              : '—'}
                          </td>
                          <td>
                            <button
                              className="drone-ops-action-btn restore"
                              onClick={() => handleRestore(drone)}
                              disabled={isActioning}
                            >
                              {isActioning ? 'Working…' : 'Restore'}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );
};

export default DroneOperationsTab;

import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../../utils/auth';
import './fleet-operations.css';

// =============================================================================
// Types — mirror the Pydantic response models in
// services/gameserver/src/api/routes/admin_fleets.py
// =============================================================================

interface AdminFleet {
  id: string;
  team_id: string;
  team_name: string;
  name: string;
  status: string;
  formation: string;
  total_ships: number;
  total_firepower: number;
  total_shields: number;
  total_hull: number;
  average_speed: number;
  morale: number;
  supply_level: number;
  commander_id: string | null;
  commander_name: string | null;
  sector_id: string | null;
  sector_name: string | null;
  member_count: number;
  created_at: string;
  last_battle: string | null;
}

interface AdminBattle {
  id: string;
  phase: string;
  started_at: string;
  ended_at: string | null;
  attacker_fleet_id: string | null;
  attacker_fleet_name: string | null;
  attacker_team_name: string | null;
  defender_fleet_id: string | null;
  defender_fleet_name: string | null;
  defender_team_name: string | null;
  sector_id: string | null;
  sector_name: string | null;
  attacker_ships_initial: number;
  defender_ships_initial: number;
  attacker_ships_destroyed: number;
  defender_ships_destroyed: number;
  attacker_ships_retreated: number;
  defender_ships_retreated: number;
  total_damage_dealt: number;
  winner: string | null;
  credits_looted: number;
  duration: string | null;
}

interface FleetSummaryRef {
  id: string;
  name: string;
  team: string;
  firepower?: number;
  ships?: number;
}

interface FleetStats {
  total_fleets: number;
  active_fleets: number;
  fleets_in_battle: number;
  total_ships_in_fleets: number;
  total_firepower: number;
  average_fleet_size: number;
  battles_today: number;
  battles_this_week: number;
  most_powerful_fleet: FleetSummaryRef | null;
  largest_fleet: FleetSummaryRef | null;
}

// Matches InterveneBattleRequest pattern in admin_fleets.py
type InterveneAction = 'end_battle' | 'pause_battle' | 'force_winner';
type BattleWinner = 'attacker' | 'defender' | 'draw';

const FleetOperationsTab: React.FC = () => {
  const [stats, setStats] = useState<FleetStats | null>(null);
  const [fleets, setFleets] = useState<AdminFleet[]>([]);
  const [battles, setBattles] = useState<AdminBattle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Inline intervention confirm state (no native confirm/alert)
  const [confirmBattleId, setConfirmBattleId] = useState<string | null>(null);
  const [interveneAction, setInterveneAction] = useState<InterveneAction>('end_battle');
  const [interveneWinner, setInterveneWinner] = useState<BattleWinner>('draw');
  const [interveneReason, setInterveneReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);

    const [statsRes, fleetsRes, battlesRes] = await Promise.allSettled([
      api.get<FleetStats>('/api/v1/admin/fleets/stats'),
      api.get<AdminFleet[]>('/api/v1/admin/fleets/'),
      api.get<AdminBattle[]>('/api/v1/admin/fleets/battles'),
    ]);

    if (statsRes.status === 'fulfilled') {
      setStats(statsRes.value.data);
    }
    if (fleetsRes.status === 'fulfilled') {
      setFleets(fleetsRes.value.data);
    }
    if (battlesRes.status === 'fulfilled') {
      setBattles(battlesRes.value.data);
    }

    const failed = [statsRes, fleetsRes, battlesRes].filter(
      (r) => r.status === 'rejected'
    );
    if (failed.length > 0) {
      setError(
        failed.length === 3
          ? 'Failed to load fleet operations data.'
          : 'Some fleet operations data could not be loaded.'
      );
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const openConfirm = (battleId: string) => {
    setConfirmBattleId(battleId);
    setInterveneAction('end_battle');
    setInterveneWinner('draw');
    setInterveneReason('');
    setError(null);
    setSuccessMessage(null);
  };

  const cancelConfirm = () => {
    setConfirmBattleId(null);
    setInterveneReason('');
  };

  const submitIntervention = async (battleId: string) => {
    if (interveneReason.trim().length < 10) {
      setError('Intervention reason must be at least 10 characters.');
      return;
    }

    setSubmitting(true);
    setError(null);
    setSuccessMessage(null);

    try {
      const payload: {
        action: InterveneAction;
        reason: string;
        winner?: BattleWinner;
      } = {
        action: interveneAction,
        reason: interveneReason.trim(),
      };
      if (interveneAction === 'force_winner') {
        payload.winner = interveneWinner;
      }

      await api.post(`/api/v1/admin/fleets/battles/${battleId}/intervene`, payload);
      setSuccessMessage('Battle intervention applied successfully.');
      setConfirmBattleId(null);
      setInterveneReason('');
      await loadData();
    } catch (err) {
      console.error('Error intervening in battle:', err);
      setError('Failed to apply battle intervention.');
    } finally {
      setSubmitting(false);
    }
  };

  const activeBattles = battles.filter((b) => b.ended_at === null);

  if (loading) {
    return (
      <div className="fleet-operations">
        <div className="fleet-ops-loading">
          <div className="loading-spinner"></div>
          <span>Loading fleet operations...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="fleet-operations">
      {error && (
        <div className="fleet-ops-alert error">
          <span>⚠️</span>
          <span className="fleet-ops-alert-spacer">{error}</span>
          <button onClick={loadData}>Retry</button>
        </div>
      )}

      {successMessage && (
        <div className="fleet-ops-alert success">
          <span>✓</span>
          <span className="fleet-ops-alert-spacer">{successMessage}</span>
          <button onClick={() => setSuccessMessage(null)}>Dismiss</button>
        </div>
      )}

      <div className="fleet-ops-toolbar">
        <button
          className="fleet-ops-refresh-btn"
          onClick={loadData}
          disabled={loading}
        >
          🔄 Refresh
        </button>
      </div>

      {/* Stats summary row */}
      {stats && (
        <div className="fleet-ops-stats-grid">
          <div className="fleet-ops-stat">
            <div className="fleet-ops-stat-label">Active Fleets</div>
            <div className="fleet-ops-stat-value">
              {stats.active_fleets.toLocaleString()}
            </div>
            <div className="fleet-ops-stat-sub">
              {stats.total_fleets.toLocaleString()} total
            </div>
          </div>

          <div className="fleet-ops-stat">
            <div className="fleet-ops-stat-label">Fleets in Battle</div>
            <div
              className={`fleet-ops-stat-value${
                stats.fleets_in_battle > 0 ? ' danger' : ''
              }`}
            >
              {stats.fleets_in_battle.toLocaleString()}
            </div>
            <div className="fleet-ops-stat-sub">currently engaged</div>
          </div>

          <div className="fleet-ops-stat">
            <div className="fleet-ops-stat-label">Ships in Fleets</div>
            <div className="fleet-ops-stat-value">
              {stats.total_ships_in_fleets.toLocaleString()}
            </div>
            <div className="fleet-ops-stat-sub">
              avg {stats.average_fleet_size.toFixed(1)} per fleet
            </div>
          </div>

          <div className="fleet-ops-stat">
            <div className="fleet-ops-stat-label">Total Firepower</div>
            <div className="fleet-ops-stat-value">
              {stats.total_firepower.toLocaleString()}
            </div>
            <div className="fleet-ops-stat-sub">across active fleets</div>
          </div>

          <div className="fleet-ops-stat">
            <div className="fleet-ops-stat-label">Battles Today</div>
            <div className="fleet-ops-stat-value">
              {stats.battles_today.toLocaleString()}
            </div>
            <div className="fleet-ops-stat-sub">
              {stats.battles_this_week.toLocaleString()} this week
            </div>
          </div>

          <div className="fleet-ops-stat">
            <div className="fleet-ops-stat-label">Most Powerful</div>
            <div className="fleet-ops-stat-value">
              {stats.most_powerful_fleet
                ? stats.most_powerful_fleet.firepower?.toLocaleString() ?? '—'
                : '—'}
            </div>
            <div className="fleet-ops-stat-sub">
              {stats.most_powerful_fleet
                ? `${stats.most_powerful_fleet.name} (${stats.most_powerful_fleet.team})`
                : 'no fleets yet'}
            </div>
          </div>
        </div>
      )}

      {/* Fleets list (all statuses; the table's Status column differentiates) */}
      <div className="fleet-ops-panel">
        <div className="fleet-ops-panel-header">
          <h4>🛰️ Fleets</h4>
          <span className="fleet-ops-count">{fleets.length}</span>
        </div>

        {fleets.length === 0 ? (
          <div className="fleet-ops-empty">No fleets.</div>
        ) : (
          <div className="fleet-ops-table-container">
            <table className="fleet-ops-table">
              <thead>
                <tr>
                  <th>Fleet</th>
                  <th>Team</th>
                  <th>Status</th>
                  <th>Formation</th>
                  <th>Ships</th>
                  <th>Firepower</th>
                  <th>Morale</th>
                  <th>Sector</th>
                  <th>Commander</th>
                </tr>
              </thead>
              <tbody>
                {fleets.map((fleet) => (
                  <tr key={fleet.id}>
                    <td>{fleet.name}</td>
                    <td>{fleet.team_name}</td>
                    <td>
                      <span
                        className={`fleet-ops-badge${
                          fleet.status === 'in_battle' ? ' battle' : ' active'
                        }`}
                      >
                        {fleet.status.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td>{fleet.formation}</td>
                    <td className="mono">
                      {fleet.total_ships} ({fleet.member_count} members)
                    </td>
                    <td className="mono">
                      {fleet.total_firepower.toLocaleString()}
                    </td>
                    <td className="mono">{fleet.morale}%</td>
                    <td>{fleet.sector_name ?? '—'}</td>
                    <td>{fleet.commander_name ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Recent battles list */}
      <div className="fleet-ops-panel">
        <div className="fleet-ops-panel-header">
          <h4>⚔️ Recent Battles</h4>
          <span className="fleet-ops-count">{battles.length}</span>
        </div>

        {battles.length === 0 ? (
          <div className="fleet-ops-empty">No recent battles.</div>
        ) : (
          <div className="fleet-ops-table-container">
            <table className="fleet-ops-table">
              <thead>
                <tr>
                  <th>Phase</th>
                  <th>Attacker</th>
                  <th>Defender</th>
                  <th>Sector</th>
                  <th>Damage</th>
                  <th>Winner</th>
                  <th>Started</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {battles.map((battle) => {
                  const isActive = battle.ended_at === null;
                  return (
                    <React.Fragment key={battle.id}>
                      <tr>
                        <td>
                          <span
                            className={`fleet-ops-badge${
                              isActive ? ' battle' : ''
                            }`}
                          >
                            {battle.phase.replace(/_/g, ' ')}
                          </span>
                        </td>
                        <td>
                          {battle.attacker_fleet_name ?? '—'}
                          {battle.attacker_team_name
                            ? ` (${battle.attacker_team_name})`
                            : ''}
                        </td>
                        <td>
                          {battle.defender_fleet_name ?? '—'}
                          {battle.defender_team_name
                            ? ` (${battle.defender_team_name})`
                            : ''}
                        </td>
                        <td>{battle.sector_name ?? '—'}</td>
                        <td className="mono">
                          {battle.total_damage_dealt.toLocaleString()}
                        </td>
                        <td>{battle.winner ?? (isActive ? 'ongoing' : '—')}</td>
                        <td className="mono">
                          {new Date(battle.started_at).toLocaleString()}
                        </td>
                        <td>
                          {isActive ? (
                            <button
                              className="fleet-ops-intervene-btn"
                              onClick={() => openConfirm(battle.id)}
                              disabled={
                                submitting && confirmBattleId === battle.id
                              }
                            >
                              Intervene
                            </button>
                          ) : (
                            '—'
                          )}
                        </td>
                      </tr>
                      {confirmBattleId === battle.id && (
                        <tr>
                          <td colSpan={8}>
                            <div className="fleet-ops-confirm">
                              <div className="fleet-ops-confirm-text">
                                Admin intervention in active battle. This is
                                logged to the audit trail.
                              </div>

                              <div className="fleet-ops-confirm-field">
                                <label htmlFor={`action-${battle.id}`}>
                                  Action
                                </label>
                                <select
                                  id={`action-${battle.id}`}
                                  value={interveneAction}
                                  onChange={(e) =>
                                    setInterveneAction(
                                      e.target.value as InterveneAction
                                    )
                                  }
                                >
                                  <option value="end_battle">End Battle</option>
                                  <option value="pause_battle">
                                    Pause Battle
                                  </option>
                                  <option value="force_winner">
                                    Force Winner
                                  </option>
                                </select>
                              </div>

                              {interveneAction === 'force_winner' && (
                                <div className="fleet-ops-confirm-field">
                                  <label htmlFor={`winner-${battle.id}`}>
                                    Winner
                                  </label>
                                  <select
                                    id={`winner-${battle.id}`}
                                    value={interveneWinner}
                                    onChange={(e) =>
                                      setInterveneWinner(
                                        e.target.value as BattleWinner
                                      )
                                    }
                                  >
                                    <option value="attacker">Attacker</option>
                                    <option value="defender">Defender</option>
                                    <option value="draw">Draw</option>
                                  </select>
                                </div>
                              )}

                              <div className="fleet-ops-confirm-field">
                                <label htmlFor={`reason-${battle.id}`}>
                                  Reason (min 10 characters)
                                </label>
                                <input
                                  id={`reason-${battle.id}`}
                                  type="text"
                                  value={interveneReason}
                                  onChange={(e) =>
                                    setInterveneReason(e.target.value)
                                  }
                                  placeholder="Why is this intervention needed?"
                                />
                              </div>

                              <div className="fleet-ops-confirm-actions">
                                <button
                                  className="confirm-danger"
                                  onClick={() => submitIntervention(battle.id)}
                                  disabled={
                                    submitting ||
                                    interveneReason.trim().length < 10
                                  }
                                >
                                  {submitting
                                    ? 'Applying...'
                                    : 'Confirm Intervention'}
                                </button>
                                <button
                                  onClick={cancelConfirm}
                                  disabled={submitting}
                                >
                                  Cancel
                                </button>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {battles.length > 0 && activeBattles.length === 0 && (
          <div className="fleet-ops-stat-sub" style={{ marginTop: '1rem' }}>
            No battles are currently active.
          </div>
        )}
      </div>
    </div>
  );
};

export default FleetOperationsTab;

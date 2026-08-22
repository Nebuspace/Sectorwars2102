import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import './drone-operations.css';

const responseStatus = (err: unknown): number | undefined =>
  typeof err === 'object' && err !== null && 'response' in err
    ? (err as { response?: { status?: number } }).response?.status
    : undefined;

const settledStatuses = (results: PromiseSettledResult<unknown>[]): number[] =>
  results
    .filter((r): r is PromiseRejectedResult => r.status === 'rejected')
    .map((r) => responseStatus(r.reason))
    .filter((s): s is number => s !== undefined);

const droneLoadError = (results: PromiseSettledResult<unknown>[], allFailed: boolean): string => {
  const statuses = settledStatuses(results);
  if (statuses.some((s) => s === 429)) {
    return 'Admin rate limit exceeded — wait a moment and try again.';
  }
  if (statuses.some((s) => s === 401 || s === 403)) {
    return 'Access denied — drone operations require the admin players view scope (PLAYERS_VIEW).';
  }
  return allFailed
    ? 'Failed to load drone operations data.'
    : 'Some drone operations data could not be loaded.';
};

const droneActError = (err: unknown, fallback: string): string => {
  const status = responseStatus(err);
  if (status === 401 || status === 403) {
    return 'Access denied — drone overrides require COMBAT_INTERVENE.';
  }
  if (status === 429) {
    return 'Admin rate limit exceeded — wait a moment and try again.';
  }
  const detail =
    (err as { response?: { data?: { detail?: string } } }).response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
};

/** PATCH/DELETE use GS SHIPS_MANAGE — do not reuse COMBAT_INTERVENE recall/restore copy. */
const droneMutateError = (err: unknown, fallback: string): string =>
  formatAdminApiError(err, {
    fallback,
    scopeHint:
      'drone edit/delete require the admin ships manage scope (SHIPS_MANAGE).',
  });

interface DroneEditForm {
  name: string;
  level: string;
  health: string;
  max_health: string;
  attack_power: string;
  defense_power: string;
  speed: string;
  status: string;
  abilities: string;
}

const formFromDrone = (drone: AdminDrone): DroneEditForm => ({
  name: drone.name,
  level: String(drone.level),
  health: String(drone.health),
  max_health: String(drone.max_health),
  attack_power: String(drone.attack_power),
  defense_power: String(drone.defense_power),
  speed: String(drone.speed),
  status: drone.status,
  abilities: drone.abilities ?? '',
});

const parseOptionalInt = (raw: string): number | undefined => {
  const t = raw.trim();
  if (t === '') return undefined;
  const n = Number.parseInt(t, 10);
  return Number.isFinite(n) ? n : undefined;
};

const parseOptionalFloat = (raw: string): number | undefined => {
  const t = raw.trim();
  if (t === '') return undefined;
  const n = Number.parseFloat(t);
  return Number.isFinite(n) ? n : undefined;
};

/** Body keys match GS AdminDroneUpdate; omit empty / unparseable (exclude_unset). */
const payloadFromForm = (form: DroneEditForm): Record<string, string | number> => {
  const payload: Record<string, string | number> = {};
  const name = form.name.trim();
  if (name) payload.name = name;
  const level = parseOptionalInt(form.level);
  if (level !== undefined) payload.level = level;
  const health = parseOptionalInt(form.health);
  if (health !== undefined) payload.health = health;
  const maxHealth = parseOptionalInt(form.max_health);
  if (maxHealth !== undefined) payload.max_health = maxHealth;
  const attack = parseOptionalInt(form.attack_power);
  if (attack !== undefined) payload.attack_power = attack;
  const defense = parseOptionalInt(form.defense_power);
  if (defense !== undefined) payload.defense_power = defense;
  const speed = parseOptionalFloat(form.speed);
  if (speed !== undefined) payload.speed = speed;
  const status = form.status.trim();
  if (status) payload.status = status;
  payload.abilities = form.abilities.trim();
  return payload;
};


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

// One entry of the combat_details list CombatService._resolve_sector_drone_combat
// builds and json.dumps's into DroneCombat.combat_log (a String(2000) column — long
// engagements get hard-truncated server-side, which can leave the JSON malformed).
// This row shape is builder-defined and not yet ratified, so every field is optional.
interface DroneCombatLogEntry {
  round?: number | string;
  actor?: string;
  action?: string;
  tag?: string;
  message?: string;
}

// Matches one entry of `recent_combats` in the GET /admin/drones/{id} response
// (admin_drones.py:276-287). `combat_log` is serialized (via `_parse_combat_log`,
// null when missing/unparseable). `combat_details` is not a response key — kept
// optional only as a defensive alias for older/alternate shapes.
interface DroneRecentCombat {
  id: string;
  started_at: string;
  ended_at: string | null;
  rounds: number;
  was_attacker: boolean;
  won: boolean;
  damage_dealt: number;
  damage_taken: number;
  combat_log?: string | DroneCombatLogEntry[] | null;
  combat_details?: DroneCombatLogEntry[] | null;
}

// The slice of GET /admin/drones/{drone_id}'s response this panel consumes —
// `drone` / `recent_deployments` are also in that envelope but unused here.
interface DroneDetailResponse {
  recent_combats?: DroneRecentCombat[];
}

// Best-effort normalizer for DroneRecentCombat.combat_log / combat_details: tolerates
// a raw JSON string (possibly truncated mid-object by the server's column cap), an
// already-parsed array, or the field being absent — never throws.
function parseCombatLogEntries(combat: DroneRecentCombat): DroneCombatLogEntry[] {
  const raw = combat.combat_log ?? combat.combat_details;
  if (raw == null) return [];

  const isEntry = (e: unknown): e is DroneCombatLogEntry =>
    typeof e === 'object' && e !== null;

  if (Array.isArray(raw)) {
    return raw.filter(isEntry);
  }
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return parsed.filter(isEntry);
      }
    } catch {
      // Truncated/malformed JSON — degrade to "no round detail" rather than crash.
    }
  }
  return [];
}

const DroneOperationsTab: React.FC = () => {
  const toast = useToast();
  const confirm = useConfirm();

  const [stats, setStats] = useState<DroneStatistics | null>(null);
  const [drones, setDrones] = useState<AdminDrone[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actioningId, setActioningId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<DroneEditForm | null>(null);

  // Combat-history row expansion. Fetched lazily per drone on first expand
  // (no polling); 'loading' / 'error' are sentinel states distinct from "not
  // yet requested" (key absent) and "loaded, zero combats" (empty array).
  const [expandedDroneId, setExpandedDroneId] = useState<string | null>(null);
  const [combatHistory, setCombatHistory] = useState<
    Record<string, DroneRecentCombat[] | 'loading' | 'error'>
  >({});

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

    const results = [statsRes, dronesRes];
    const failed = results.filter((r) => r.status === 'rejected');
    if (failed.length > 0) {
      setError(droneLoadError(results, failed.length === 2));
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
        toast.error(droneActError(err, 'Failed to force-recall drone.'));
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
        toast.error(droneActError(err, 'Failed to restore drone.'));
      } finally {
        setActioningId(null);
      }
    },
    [confirm, toast, loadData]
  );

  const openEdit = useCallback((drone: AdminDrone) => {
    setEditingId(drone.id);
    setEditForm(formFromDrone(drone));
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setEditForm(null);
  }, []);

  const handleSaveEdit = useCallback(async () => {
    if (!editingId || !editForm) return;
    const payload = payloadFromForm(editForm);
    setActioningId(editingId);
    try {
      await api.patch(`/api/v1/admin/drones/${editingId}`, payload);
      toast.success('Drone updated.');
      setEditingId(null);
      setEditForm(null);
      await loadData();
    } catch (err) {
      toast.error(droneMutateError(err, 'Failed to update drone.'));
    } finally {
      setActioningId(null);
    }
  }, [editingId, editForm, toast, loadData]);

  const handleDelete = useCallback(
    async (drone: AdminDrone) => {
      const ok = await confirm({
        title: 'Delete Drone',
        message: `Permanently delete "${drone.name}"? This cannot be undone.`,
        confirmLabel: 'Delete',
        cancelLabel: 'Cancel',
        danger: true,
      });
      if (!ok) return;

      setActioningId(drone.id);
      try {
        await api.delete(`/api/v1/admin/drones/${drone.id}`);
        toast.success(`Drone "${drone.name}" deleted.`);
        if (editingId === drone.id) {
          setEditingId(null);
          setEditForm(null);
        }
        await loadData();
      } catch (err) {
        toast.error(droneMutateError(err, 'Failed to delete drone.'));
      } finally {
        setActioningId(null);
      }
    },
    [confirm, toast, loadData, editingId]
  );

  const fetchCombatHistory = useCallback(async (droneId: string) => {
    setCombatHistory((prev) => ({ ...prev, [droneId]: 'loading' }));
    try {
      const res = await api.get<DroneDetailResponse>(
        `/api/v1/admin/drones/${droneId}`
      );
      const combats = Array.isArray(res.data?.recent_combats)
        ? res.data.recent_combats
        : [];
      setCombatHistory((prev) => ({ ...prev, [droneId]: combats }));
    } catch {
      setCombatHistory((prev) => ({ ...prev, [droneId]: 'error' }));
    }
  }, []);

  const toggleCombatHistory = useCallback(
    (droneId: string) => {
      if (expandedDroneId === droneId) {
        setExpandedDroneId(null);
        return;
      }
      setExpandedDroneId(droneId);
      if (combatHistory[droneId] === undefined) {
        fetchCombatHistory(droneId);
      }
    },
    [expandedDroneId, combatHistory, fetchCombatHistory]
  );

  const updateEditField = useCallback((field: keyof DroneEditForm, value: string) => {
    setEditForm((prev) => (prev ? { ...prev, [field]: value } : prev));
  }, []);

  const renderMutateButtons = (drone: AdminDrone) => {
    const isActioning = actioningId === drone.id;
    const isEditing = editingId === drone.id;
    return (
      <>
        <button
          type="button"
          className="drone-ops-action-btn edit"
          onClick={() => openEdit(drone)}
          disabled={isActioning}
          aria-expanded={isEditing}
        >
          {isEditing ? 'Editing' : 'Edit'}
        </button>
        <button
          type="button"
          className="drone-ops-action-btn delete"
          onClick={() => handleDelete(drone)}
          disabled={isActioning}
        >
          Delete
        </button>
      </>
    );
  };

  const renderEditFormRow = (colSpan: number) => {
    if (!editForm) return null;
    const isActioning = actioningId === editingId;
    return (
      <tr className="drone-ops-edit-row">
        <td colSpan={colSpan}>
          <form
            className="drone-ops-edit-form"
            onSubmit={(e) => {
              e.preventDefault();
              void handleSaveEdit();
            }}
          >
            <label className="drone-ops-edit-field">
              Name
              <input
                value={editForm.name}
                onChange={(e) => updateEditField('name', e.target.value)}
                disabled={isActioning}
              />
            </label>
            <label className="drone-ops-edit-field">
              Level
              <input
                type="number"
                value={editForm.level}
                onChange={(e) => updateEditField('level', e.target.value)}
                disabled={isActioning}
              />
            </label>
            <label className="drone-ops-edit-field">
              Health
              <input
                type="number"
                value={editForm.health}
                onChange={(e) => updateEditField('health', e.target.value)}
                disabled={isActioning}
              />
            </label>
            <label className="drone-ops-edit-field">
              Max Health
              <input
                type="number"
                value={editForm.max_health}
                onChange={(e) => updateEditField('max_health', e.target.value)}
                disabled={isActioning}
              />
            </label>
            <label className="drone-ops-edit-field">
              Attack
              <input
                type="number"
                value={editForm.attack_power}
                onChange={(e) => updateEditField('attack_power', e.target.value)}
                disabled={isActioning}
              />
            </label>
            <label className="drone-ops-edit-field">
              Defense
              <input
                type="number"
                value={editForm.defense_power}
                onChange={(e) => updateEditField('defense_power', e.target.value)}
                disabled={isActioning}
              />
            </label>
            <label className="drone-ops-edit-field">
              Speed
              <input
                type="number"
                step="any"
                value={editForm.speed}
                onChange={(e) => updateEditField('speed', e.target.value)}
                disabled={isActioning}
              />
            </label>
            <label className="drone-ops-edit-field">
              Status
              <input
                value={editForm.status}
                onChange={(e) => updateEditField('status', e.target.value)}
                disabled={isActioning}
              />
            </label>
            <label className="drone-ops-edit-field wide">
              Abilities
              <input
                value={editForm.abilities}
                onChange={(e) => updateEditField('abilities', e.target.value)}
                disabled={isActioning}
              />
            </label>
            <div className="drone-ops-edit-actions">
              <button
                type="submit"
                className="drone-ops-action-btn restore"
                disabled={isActioning}
              >
                {isActioning ? 'Working…' : 'Save'}
              </button>
              <button
                type="button"
                className="drone-ops-action-btn history"
                onClick={cancelEdit}
                disabled={isActioning}
              >
                Cancel
              </button>
            </div>
          </form>
        </td>
      </tr>
    );
  };

  const renderCombatHistoryPanel = (droneId: string) => {
    const entry = combatHistory[droneId];

    if (entry === undefined || entry === 'loading') {
      return (
        <div className="drone-ops-combat-history drone-ops-combat-history-status">
          <div className="loading-spinner"></div>
          <span>Loading combat history…</span>
        </div>
      );
    }

    if (entry === 'error') {
      return (
        <div className="drone-ops-combat-history drone-ops-combat-history-status">
          <span>⚠️ Failed to load combat history.</span>
          <button
            className="drone-ops-action-btn history"
            onClick={() => fetchCombatHistory(droneId)}
          >
            Retry
          </button>
        </div>
      );
    }

    if (entry.length === 0) {
      return (
        <div className="drone-ops-combat-history drone-ops-combat-history-status">
          No recorded combats.
        </div>
      );
    }

    return (
      <div className="drone-ops-combat-history">
        <table className="drone-ops-combat-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Role</th>
              <th>Rounds</th>
              <th>Outcome</th>
              <th>Damage Dealt</th>
              <th>Damage Taken</th>
            </tr>
          </thead>
          <tbody>
            {entry.map((combat) => {
              const logEntries = parseCombatLogEntries(combat);
              return (
                <React.Fragment key={combat.id}>
                  <tr>
                    <td className="mono">
                      {combat.started_at
                        ? new Date(combat.started_at).toLocaleString()
                        : '—'}
                    </td>
                    <td>{combat.was_attacker ? 'Attacker' : 'Defender'}</td>
                    <td className="mono">{combat.rounds ?? '—'}</td>
                    <td>
                      <span
                        className={`drone-ops-badge${
                          combat.won ? ' active' : ' battle'
                        }`}
                      >
                        {combat.won ? 'Won' : 'Lost'}
                      </span>
                    </td>
                    <td className="mono">{combat.damage_dealt ?? '—'}</td>
                    <td className="mono">{combat.damage_taken ?? '—'}</td>
                  </tr>
                  {logEntries.length > 0 && (
                    <tr className="drone-ops-combat-log-row">
                      <td colSpan={6}>
                        <ul className="drone-ops-combat-log-list">
                          {logEntries.map((detail, idx) => (
                            <li key={idx}>
                              {detail.round !== undefined && (
                                <span className="mono">
                                  R{String(detail.round)}
                                </span>
                              )}
                              {detail.tag === 'SECTOR_DEFENSE' && (
                                <span className="drone-ops-badge deployed">
                                  sector defense
                                </span>
                              )}
                              <span>
                                {typeof detail.message === 'string'
                                  ? detail.message
                                  : detail.action ?? 'Unrecognized combat event'}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

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
                  <th>History</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {activeDrones.map((drone) => {
                  const isDeployedOrCombat =
                    drone.status === 'deployed' || drone.status === 'combat';
                  const isActioning = actioningId === drone.id;
                  const isExpanded = expandedDroneId === drone.id;
                  return (
                    <React.Fragment key={drone.id}>
                      <tr>
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
                          <button
                            className="drone-ops-action-btn history"
                            onClick={() => toggleCombatHistory(drone.id)}
                            aria-expanded={isExpanded}
                          >
                            {isExpanded ? 'Hide' : 'Combat History'}
                          </button>
                        </td>
                        <td>
                          <div className="drone-ops-actions">
                            {isDeployedOrCombat ? (
                              <button
                                type="button"
                                className="drone-ops-action-btn recall"
                                onClick={() => handleForceRecall(drone)}
                                disabled={isActioning}
                              >
                                {isActioning ? 'Working…' : 'Force Recall'}
                              </button>
                            ) : null}
                            {renderMutateButtons(drone)}
                          </div>
                        </td>
                      </tr>
                      {editingId === drone.id && renderEditFormRow(10)}
                      {isExpanded && (
                        <tr className="drone-ops-combat-expand-row">
                          <td colSpan={10}>
                            {renderCombatHistoryPanel(drone.id)}
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
                      <th>History</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {destroyed.map((drone) => {
                      const isActioning = actioningId === drone.id;
                      const isExpanded = expandedDroneId === drone.id;
                      return (
                        <React.Fragment key={drone.id}>
                          <tr>
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
                                className="drone-ops-action-btn history"
                                onClick={() => toggleCombatHistory(drone.id)}
                                aria-expanded={isExpanded}
                              >
                                {isExpanded ? 'Hide' : 'Combat History'}
                              </button>
                            </td>
                            <td>
                              <div className="drone-ops-actions">
                                <button
                                  type="button"
                                  className="drone-ops-action-btn restore"
                                  onClick={() => handleRestore(drone)}
                                  disabled={isActioning}
                                >
                                  {isActioning ? 'Working…' : 'Restore'}
                                </button>
                                {renderMutateButtons(drone)}
                              </div>
                            </td>
                          </tr>
                          {editingId === drone.id && renderEditFormRow(7)}
                          {isExpanded && (
                            <tr className="drone-ops-combat-expand-row">
                              <td colSpan={7}>
                                {renderCombatHistoryPanel(drone.id)}
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
          </div>
        );
      })()}
    </div>
  );
};

export default DroneOperationsTab;

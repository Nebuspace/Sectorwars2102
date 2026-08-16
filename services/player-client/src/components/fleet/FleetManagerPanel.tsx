/**
 * FleetManagerPanel — LEG-INI-01
 *
 * Player-facing fleet coordination surface over existing `/api/v1/fleets`
 * endpoints: roster list, composition, create, add/remove ship, formation,
 * disband, resupply. Roster uses team fleets (`GET /`), not `my-fleets`
 * (member-ship filter would hide a just-created empty formation).
 * Move-as-one is NOT wired — gameserver has no `/move` route (canon
 * fleet-coordination.md still lists it; escalate to gameserver).
 */

import React, { useCallback, useEffect, useState } from 'react';
import { fleetAPI } from '../../services/api';
import { useGame } from '../../contexts/GameContext';
import CockpitInstrument from '../cockpit/CockpitInstrument';
import { useEmbedded } from '../cockpit/EmbeddedContext';
import './fleet-manager.css';

export interface FleetSummary {
  id: string;
  name: string;
  status: string;
  formation: string;
  total_ships: number;
  total_firepower: number;
  total_shields: number;
  total_hull: number;
  coordination_bonus: number;
  morale: number;
  supply_level: number;
  commander_name?: string | null;
  sector_name?: string | null;
  member_count: number;
}

export interface FleetMemberRow {
  id: string;
  ship_id: string;
  ship_name: string;
  ship_type: string;
  player_name: string;
  role: string;
  position: number;
  ready_status: boolean;
}

const FORMATIONS: { value: string; label: string }[] = [
  { value: 'standard', label: 'Standard' },
  { value: 'aggressive', label: 'Wedge' },
  { value: 'defensive', label: 'Defensive' },
  { value: 'flanking', label: 'Offensive' },
  { value: 'turtle', label: 'Scatter' },
];

const ROLES = ['attacker', 'defender', 'support', 'scout', 'flagship'] as const;

type Busy =
  | 'load'
  | 'create'
  | 'formation'
  | 'add'
  | 'remove'
  | 'disband'
  | 'resupply'
  | null;

const errMsg = (e: unknown): string =>
  e instanceof Error ? e.message : 'Fleet request failed';

const FleetShell: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  useEmbedded();
  return (
    <CockpitInstrument title="FLEET COORDINATION" accent="#F5A623" subtitle="TEAM FORMATIONS">
      {children}
    </CockpitInstrument>
  );
};

export const FleetManagerPanel: React.FC = () => {
  const { ships } = useGame();
  const [fleets, setFleets] = useState<FleetSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [members, setMembers] = useState<FleetMemberRow[]>([]);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [newFormation, setNewFormation] = useState('standard');
  const [addShipId, setAddShipId] = useState('');
  const [addRole, setAddRole] = useState<string>('attacker');

  const selected = fleets.find((f) => f.id === selectedId) ?? null;

  const refresh = useCallback(async () => {
    setBusy('load');
    setError(null);
    try {
      // Team roster — empty new fleets are invisible on my-fleets until a ship joins.
      const list = (await fleetAPI.getFleets()) as FleetSummary[];
      const next = Array.isArray(list) ? list : [];
      setFleets(next);
      setSelectedId((prev) => (prev && next.some((f) => f.id === prev) ? prev : null));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(null);
    }
  }, []);

  const loadMembers = useCallback(async (fleetId: string) => {
    setError(null);
    try {
      const rows = (await fleetAPI.getFleetMembers(fleetId)) as FleetMemberRow[];
      setMembers(Array.isArray(rows) ? rows : []);
    } catch (e) {
      setError(errMsg(e));
      setMembers([]);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (selectedId) void loadMembers(selectedId);
    else setMembers([]);
  }, [selectedId, loadMembers]);

  const memberShipIds = new Set(members.map((m) => m.ship_id));
  const availableShips = ships.filter((s) => !memberShipIds.has(s.id));

  const run = async (kind: Exclude<Busy, 'load' | null>, fn: () => Promise<void>) => {
    setBusy(kind);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <FleetShell>
      <div className="fleet-manager" data-testid="fleet-manager">
        {error && (
          <p className="fleet-manager-error" role="alert" data-testid="fleet-manager-error">
            {error}
          </p>
        )}

        <section className="fleet-manager-create" aria-label="Create fleet">
          <h3 className="fleet-manager-heading">New formation</h3>
          <div className="fleet-manager-row">
            <label className="fleet-manager-label">
              Name
              <input
                data-testid="fleet-create-name"
                type="text"
                value={newName}
                maxLength={100}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Fleet name"
              />
            </label>
            <label className="fleet-manager-label">
              Formation
              <select
                data-testid="fleet-create-formation"
                value={newFormation}
                onChange={(e) => setNewFormation(e.target.value)}
              >
                {FORMATIONS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              data-testid="fleet-create-submit"
              disabled={busy !== null || !newName.trim()}
              onClick={() =>
                run('create', async () => {
                  await fleetAPI.createFleet(newName.trim(), newFormation);
                  setNewName('');
                  await refresh();
                })
              }
            >
              {busy === 'create' ? 'Creating…' : 'Create fleet'}
            </button>
          </div>
        </section>

        <p className="fleet-manager-muted" data-testid="fleet-move-unavailable">
          Move-as-one is not available yet — the gameserver has no fleet{' '}
          <code>/move</code> route (canon still lists it). Roster, composition, and
          formation are live.
        </p>

        <section className="fleet-manager-list" aria-label="Fleet roster">
          <h3 className="fleet-manager-heading">Your fleets</h3>
          {busy === 'load' && fleets.length === 0 ? (
            <p className="fleet-manager-muted">Loading fleets…</p>
          ) : fleets.length === 0 ? (
            <p className="fleet-manager-muted" data-testid="fleet-empty">
              No fleets yet. Create one above (requires a team).
            </p>
          ) : (
            <ul className="fleet-manager-roster" data-testid="fleet-roster">
              {fleets.map((f) => (
                <li key={f.id}>
                  <button
                    type="button"
                    className={
                      f.id === selectedId
                        ? 'fleet-manager-roster-item is-selected'
                        : 'fleet-manager-roster-item'
                    }
                    data-testid={`fleet-select-${f.id}`}
                    onClick={() => setSelectedId(f.id)}
                  >
                    <span className="fleet-manager-roster-name">{f.name}</span>
                    <span className="fleet-manager-roster-meta">
                      {f.status} · {f.total_ships} ships · {f.formation}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {selected && (
          <section className="fleet-manager-detail" aria-label={`Fleet ${selected.name}`}>
            <h3 className="fleet-manager-heading">{selected.name}</h3>
            <dl className="fleet-manager-stats" data-testid="fleet-stats">
              <div>
                <dt>Status</dt>
                <dd>{selected.status}</dd>
              </div>
              <div>
                <dt>Ships</dt>
                <dd>{selected.total_ships}</dd>
              </div>
              <div>
                <dt>Firepower</dt>
                <dd>{selected.total_firepower}</dd>
              </div>
              <div>
                <dt>Shields / Hull</dt>
                <dd>
                  {selected.total_shields} / {selected.total_hull}
                </dd>
              </div>
              <div>
                <dt>Coordination</dt>
                <dd>{selected.coordination_bonus}</dd>
              </div>
              <div>
                <dt>Morale</dt>
                <dd>{selected.morale}</dd>
              </div>
              <div>
                <dt>Supply</dt>
                <dd>{selected.supply_level}</dd>
              </div>
              <div>
                <dt>Sector</dt>
                <dd>{selected.sector_name ?? '—'}</dd>
              </div>
              <div>
                <dt>Commander</dt>
                <dd>{selected.commander_name ?? '—'}</dd>
              </div>
            </dl>

            <div className="fleet-manager-row">
              <label className="fleet-manager-label">
                Formation
                <select
                  data-testid="fleet-formation-select"
                  value={selected.formation}
                  disabled={busy !== null || selected.status === 'in_battle'}
                  onChange={(e) => {
                    const formation = e.target.value;
                    void run('formation', async () => {
                      await fleetAPI.updateFormation(selected.id, formation);
                      await refresh();
                    });
                  }}
                >
                  {FORMATIONS.map((f) => (
                    <option key={f.value} value={f.value}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                data-testid="fleet-resupply"
                disabled={busy !== null || selected.status === 'in_battle'}
                onClick={() =>
                  run('resupply', async () => {
                    await fleetAPI.resupplyFleet(selected.id);
                    await refresh();
                  })
                }
              >
                {busy === 'resupply' ? 'Resupplying…' : 'Resupply at station'}
              </button>
              <button
                type="button"
                className="fleet-manager-danger"
                data-testid="fleet-disband"
                disabled={busy !== null}
                onClick={() => {
                  if (!window.confirm(`Disband fleet "${selected.name}"?`)) return;
                  void run('disband', async () => {
                    await fleetAPI.disbandFleet(selected.id);
                    setSelectedId(null);
                    setMembers([]);
                    await refresh();
                  });
                }}
              >
                {busy === 'disband' ? 'Disbanding…' : 'Disband'}
              </button>
            </div>

            <h4 className="fleet-manager-subheading">Composition</h4>
            {members.length === 0 ? (
              <p className="fleet-manager-muted" data-testid="fleet-members-empty">
                No ships assigned.
              </p>
            ) : (
              <ul className="fleet-manager-members" data-testid="fleet-members">
                {members.map((m) => (
                  <li key={m.id} className="fleet-manager-member">
                    <span>
                      {m.ship_name} ({m.ship_type}) · {m.role} · {m.player_name}
                    </span>
                    <button
                      type="button"
                      data-testid={`fleet-remove-${m.ship_id}`}
                      disabled={busy !== null || selected.status === 'in_battle'}
                      onClick={() =>
                        run('remove', async () => {
                          await fleetAPI.removeShipFromFleet(selected.id, m.ship_id);
                          await loadMembers(selected.id);
                          await refresh();
                        })
                      }
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {(selected.status === 'forming' || selected.status === 'ready') && (
              <div className="fleet-manager-row fleet-manager-add">
                <label className="fleet-manager-label">
                  Add ship
                  <select
                    data-testid="fleet-add-ship"
                    value={addShipId}
                    onChange={(e) => setAddShipId(e.target.value)}
                  >
                    <option value="">Select hull…</option>
                    {availableShips.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name} ({s.type})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="fleet-manager-label">
                  Role
                  <select
                    data-testid="fleet-add-role"
                    value={addRole}
                    onChange={(e) => setAddRole(e.target.value)}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  data-testid="fleet-add-submit"
                  disabled={busy !== null || !addShipId}
                  onClick={() =>
                    run('add', async () => {
                      await fleetAPI.addShipToFleet(selected.id, addShipId, addRole);
                      setAddShipId('');
                      await loadMembers(selected.id);
                      await refresh();
                    })
                  }
                >
                  {busy === 'add' ? 'Adding…' : 'Add to fleet'}
                </button>
              </div>
            )}
          </section>
        )}
      </div>
    </FleetShell>
  );
};

export default FleetManagerPanel;

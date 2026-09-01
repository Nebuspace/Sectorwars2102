/**
 * FleetManagerPanel — LEG-INI-01 roster + LEG-133/141 move + LEG-2278 battle viewer
 *
 * Player-facing fleet coordination surface over existing `/api/v1/fleets`
 * endpoints: roster list, composition, create, add/remove ship, formation,
 * disband, resupply, move-as-one (`POST /fleets/{id}/move`, LEG-49),
 * initiateBattle, simulateBattleRound, getBattles, getBattle.
 * Roster uses team fleets (`GET /`), not `my-fleets` (member-ship filter
 * would hide a just-created empty formation).
 *
 * Move destination: Sector row UUID via `fleetAPI.move`. Adjacent hops come
 * from GameContext `availableMoves` keyed by `MoveOption.id` (LEG-132/133).
 * LEG-141: refresh available-moves on mount / sector change.
 *
 * LEG-2278 / LEG-308: initiateBattle + simulateBattleRound on existing
 * fleetAPI (no new GS fields). Tip GET /fleets is team-only — defender is a
 * UUID the server validates (same-sector, not same-team). GET battle detail
 * omits battle_log (LEG-400); rounds from simulate-round are shown locally.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { fleetAPI } from '../../services/api';
import { useGame, type MoveOption } from '../../contexts/GameContext';
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
  /** Sector row UUID when known (FleetResponse.sector_id). */
  sector_id?: string | null;
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

/** Canon table from fleet-tactics.md — static UI preview (no endpoint). */
export const FORMATION_PREVIEW: Record<string, { attack: number; defense: number }> = {
  standard: { attack: 1.0, defense: 1.0 },
  aggressive: { attack: 1.15, defense: 0.85 },
  defensive: { attack: 0.85, defense: 1.15 },
  flanking: { attack: 1.1, defense: 0.9 },
  turtle: { attack: 0.6, defense: 1.4 },
};

const formatMult = (n: number): string => `×${n.toFixed(2)}`;

const GaugeBar: React.FC<{ label: string; value: number; testId: string }> = ({
  label,
  value,
  testId,
}) => {
  const clamped = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <div className="fleet-manager-gauge" data-testid={testId}>
      <div className="fleet-manager-gauge-label">
        <span>{label}</span>
        <span>{clamped}</span>
      </div>
      <div className="fleet-manager-gauge-track" aria-hidden="true">
        <div className="fleet-manager-gauge-fill" style={{ width: `${clamped}%` }} />
      </div>
    </div>
  );
};

type BattleLogEntry = Record<string, unknown>;

type BattleStatusSnapshot = {
  battle_id?: string;
  phase?: string;
  is_active?: boolean;
  rounds_completed?: number;
  winner?: string | null;
  battle_log?: BattleLogEntry[] | null;
  attacker?: {
    ships_remaining?: number;
    ships_retreated?: number;
    formation?: string | null;
  };
  defender?: {
    ships_remaining?: number;
    ships_retreated?: number;
    formation?: string | null;
  };
  casualties?: {
    attacker?: Array<{ ship_name?: string; destroyed?: boolean; retreated?: boolean }>;
    defender?: Array<{ ship_name?: string; destroyed?: boolean; retreated?: boolean }>;
  };
};

type FlatRound = {
  round: number;
  attacker_damage: unknown;
  defender_damage: unknown;
  destroyed: number;
  retreated: number;
};

const flattenRoundEntry = (entry: BattleLogEntry): FlatRound | null => {
  const nested =
    entry.results && typeof entry.results === 'object'
      ? (entry.results as BattleLogEntry)
      : entry;
  const round =
    typeof nested.round === 'number'
      ? nested.round
      : typeof entry.round === 'number'
        ? entry.round
        : null;
  if (round == null) return null;
  const destroyedList = Array.isArray(nested.ships_destroyed) ? nested.ships_destroyed : [];
  const retreatedList = Array.isArray(nested.ships_retreated) ? nested.ships_retreated : [];
  return {
    round,
    attacker_damage: nested.attacker_damage,
    defender_damage: nested.defender_damage,
    destroyed: destroyedList.length,
    retreated: retreatedList.length,
  };
};

const ROLES = ['attacker', 'defender', 'support', 'scout', 'flagship'] as const;

type HopChoice = {
  id: string;
  label: string;
  kind: 'warp' | 'tunnel';
};

type Busy =
  | 'load'
  | 'create'
  | 'formation'
  | 'add'
  | 'remove'
  | 'disband'
  | 'resupply'
  | 'move'
  | 'initiate'
  | 'simulate'
  | null;

/** Transport collapse copy is not gameserver detail (LEG-3279 densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

export function formatFleetManagerError(
  e: unknown,
  fallback = 'Fleet request failed',
): string {
  if (e instanceof TypeError) return fallback;
  if (e instanceof Error) {
    if (isNetworkCollapseMessage(e.message)) return fallback;
    return e.message;
  }
  return fallback;
}

const isUuid = (value: string): boolean =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);

const FleetShell: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  useEmbedded();
  return (
    <CockpitInstrument title="FLEET COORDINATION" accent="#F5A623" subtitle="TEAM FORMATIONS">
      {children}
    </CockpitInstrument>
  );
};

const hopLabel = (kind: 'warp' | 'tunnel', move: MoveOption): string => {
  const num = move.sector_number ?? move.sector_id;
  const nameBit = move.name ? ` (${move.name})` : '';
  const prefix = kind === 'warp' ? 'Warp' : 'Tunnel';
  return `${prefix} — Sector ${num}${nameBit} · ${move.turn_cost}t`;
};

const toHopChoice = (kind: 'warp' | 'tunnel', move: MoveOption): HopChoice | null => {
  if (!move.can_afford) return null;
  const raw = move.id;
  if (raw == null) return null;
  const id = String(raw);
  if (!isUuid(id)) return null;
  return { id, label: hopLabel(kind, move), kind };
};

export const FleetManagerPanel: React.FC = () => {
  const { ships, currentSector, availableMoves, getAvailableMoves } = useGame();
  const [fleets, setFleets] = useState<FleetSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [members, setMembers] = useState<FleetMemberRow[]>([]);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [newFormation, setNewFormation] = useState('standard');
  const [addShipId, setAddShipId] = useState('');
  const [addRole, setAddRole] = useState<string>('attacker');
  const [previewFormation, setPreviewFormation] = useState('standard');
  const [moveDest, setMoveDest] = useState('');
  const [battleId, setBattleId] = useState<string | null>(null);
  const [battleStatus, setBattleStatus] = useState<BattleStatusSnapshot | null>(null);
  const [defenderFleetId, setDefenderFleetId] = useState('');
  const [localRounds, setLocalRounds] = useState<FlatRound[]>([]);

  const selected = fleets.find((f) => f.id === selectedId) ?? null;
  const inBattle = selected?.status === 'in_battle';

  const currentSectorUuid = useMemo(() => {
    const raw = currentSector?.id;
    if (raw == null) return null;
    const asStr = String(raw);
    return isUuid(asStr) ? asStr : null;
  }, [currentSector?.id]);

  const currentMoveLabel = useMemo(() => {
    if (!currentSector || !currentSectorUuid) return null;
    const num = currentSector.sector_number ?? currentSector.sector_id;
    return `Current — Sector ${num}${currentSector.name ? ` (${currentSector.name})` : ''}`;
  }, [currentSector, currentSectorUuid]);

  const adjacentHops = useMemo(() => {
    const warps = (availableMoves?.warps ?? [])
      .map((m) => toHopChoice('warp', m))
      .filter((h): h is HopChoice => h != null);
    const tunnels = (availableMoves?.tunnels ?? [])
      .map((m) => toHopChoice('tunnel', m))
      .filter((h): h is HopChoice => h != null);
    const seen = new Set<string>();
    const out: HopChoice[] = [];
    for (const hop of [...warps, ...tunnels]) {
      if (seen.has(hop.id)) continue;
      seen.add(hop.id);
      out.push(hop);
    }
    return out;
  }, [availableMoves]);

  const hasMoveDestinations = adjacentHops.length > 0 || currentSectorUuid != null;

  // LEG-141: context only refreshes moves on sector change / explore / latent-scan.
  useEffect(() => {
    void getAvailableMoves();
  }, [currentSector?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const preferred = adjacentHops[0]?.id ?? currentSectorUuid ?? '';
    setMoveDest((prev) => {
      if (prev && (adjacentHops.some((h) => h.id === prev) || prev === currentSectorUuid)) {
        return prev;
      }
      return preferred;
    });
  }, [adjacentHops, currentSectorUuid]);

  const formationPreviewKey = selected
    ? inBattle
      ? selected.formation
      : previewFormation || selected.formation
    : previewFormation;
  const formationMods =
    FORMATION_PREVIEW[formationPreviewKey] ?? FORMATION_PREVIEW.standard;

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
      setError(formatFleetManagerError(e));
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
      setError(formatFleetManagerError(e));
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

  useEffect(() => {
    if (selected) setPreviewFormation(selected.formation || 'standard');
  }, [selected?.id, selected?.formation]);

  // LEG-308: poll active battles for the selected fleet; detail via getBattle.
  useEffect(() => {
    if (!selected || !inBattle) {
      setBattleId(null);
      setBattleStatus(null);
      setLocalRounds([]);
      return;
    }

    let cancelled = false;
    const tick = async () => {
      try {
        const list = (await fleetAPI.getBattles(true)) as Array<{
          battle_id?: string;
          attacker_fleet_id?: string;
          defender_fleet_id?: string;
        }>;
        const rows = Array.isArray(list) ? list : [];
        const match = rows.find(
          (b) =>
            String(b.attacker_fleet_id) === selected.id ||
            String(b.defender_fleet_id) === selected.id,
        );
        const id = match?.battle_id ? String(match.battle_id) : null;
        if (cancelled) return;
        setBattleId(id);
        if (!id) {
          setBattleStatus(null);
          return;
        }
        const detail = (await fleetAPI.getBattle(id)) as BattleStatusSnapshot;
        if (!cancelled) setBattleStatus(detail && typeof detail === 'object' ? detail : null);
      } catch {
        if (!cancelled) {
          /* keep last snapshot; roster refresh already surfaces hard errors */
        }
      }
    };

    void tick();
    const handle = window.setInterval(() => void tick(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [selected?.id, inBattle]);

  const memberShipIds = new Set(members.map((m) => m.ship_id));
  const availableShips = ships.filter((s) => !memberShipIds.has(s.id));
  const defenderIdTrimmed = defenderFleetId.trim();
  const defenderIdValid = isUuid(defenderIdTrimmed);
  const outOfSupply = (selected?.supply_level ?? 0) <= 0;
  const battleEnded =
    battleStatus?.is_active === false || Boolean(battleStatus?.winner);
  const logRounds: FlatRound[] = (() => {
    const raw = battleStatus?.battle_log;
    const fromGet = Array.isArray(raw)
      ? raw.map(flattenRoundEntry).filter((r): r is FlatRound => r != null)
      : [];
    if (fromGet.length > 0) return fromGet;
    return localRounds;
  })();

  const run = async (kind: Exclude<Busy, 'load' | null>, fn: () => Promise<void>) => {
    setBusy(kind);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(formatFleetManagerError(e));
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
              <div className="fleet-manager-stats-span">
                <dt>Morale</dt>
                <dd>
                  <GaugeBar
                    label="Morale"
                    value={selected.morale}
                    testId="fleet-morale-gauge"
                  />
                </dd>
              </div>
              <div className="fleet-manager-stats-span">
                <dt>Supply</dt>
                <dd>
                  <GaugeBar
                    label="Supply"
                    value={selected.supply_level}
                    testId="fleet-supply-gauge"
                  />
                </dd>
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

            <div className="fleet-manager-row" data-testid="fleet-move-controls">
              <label className="fleet-manager-label">
                Move to
                <select
                  data-testid="fleet-move-dest"
                  value={moveDest}
                  disabled={busy !== null || inBattle || !hasMoveDestinations}
                  onChange={(e) => setMoveDest(e.target.value)}
                >
                  {!hasMoveDestinations ? (
                    <option value="">No destinations available</option>
                  ) : (
                    <>
                      {currentSectorUuid && currentMoveLabel && (
                        <option value={currentSectorUuid}>{currentMoveLabel}</option>
                      )}
                      {adjacentHops.map((hop) => (
                        <option
                          key={`${hop.kind}-${hop.id}`}
                          value={hop.id}
                          data-testid={`fleet-move-hop-${hop.kind}-${hop.id}`}
                        >
                          {hop.label}
                        </option>
                      ))}
                    </>
                  )}
                </select>
              </label>
              <button
                type="button"
                data-testid="fleet-move-submit"
                disabled={
                  busy !== null ||
                  inBattle ||
                  !moveDest ||
                  !isUuid(moveDest) ||
                  !hasMoveDestinations
                }
                onClick={() =>
                  run('move', async () => {
                    if (!isUuid(moveDest)) {
                      throw new Error(
                        'Move destination needs a Sector UUID (adjacent hop or current sector).'
                      );
                    }
                    await fleetAPI.move(selected.id, moveDest);
                    await refresh();
                  })
                }
              >
                {busy === 'move'
                  ? 'Moving…'
                  : inBattle
                    ? 'In battle — cannot move'
                    : 'Move as one'}
              </button>
            </div>
            {inBattle && (
              <p className="fleet-manager-muted" data-testid="fleet-move-in-battle">
                Cannot move a fleet during battle (server rejects IN_BATTLE).
              </p>
            )}
            {!hasMoveDestinations && !inBattle && (
              <p className="fleet-manager-muted" data-testid="fleet-move-no-sector">
                No adjacent hops with Sector UUIDs and current sector not loaded —
                open NAV / wait for available-moves sync, then move the fleet here.
              </p>
            )}

            <div className="fleet-manager-row">
              <label className="fleet-manager-label">
                Formation
                <select
                  data-testid="fleet-formation-select"
                  value={inBattle ? selected.formation : previewFormation}
                  disabled={busy !== null || inBattle}
                  onChange={(e) => {
                    const formation = e.target.value;
                    setPreviewFormation(formation);
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
              <div
                className="fleet-manager-formation-preview"
                data-testid="fleet-formation-preview"
                aria-live="polite"
              >
                <span>Attack {formatMult(formationMods.attack)}</span>
                <span>Defense {formatMult(formationMods.defense)}</span>
              </div>
              <button
                type="button"
                data-testid="fleet-resupply"
                disabled={busy !== null || inBattle}
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

            {!inBattle && (
              <section
                className="fleet-manager-battle-initiate"
                data-testid="fleet-battle-initiate"
                aria-label="Initiate fleet battle"
              >
                <h4 className="fleet-manager-subheading">Initiate battle</h4>
                <p className="fleet-manager-muted" data-testid="fleet-battle-initiate-hint">
                  GET /fleets is team-only; same-team fleets cannot fight. Enter a
                  defender fleet UUID in this sector — the server rejects friendly
                  fire, out-of-sector, and out-of-supply.
                </p>
                {outOfSupply && (
                  <p className="fleet-manager-muted" data-testid="fleet-battle-out-of-supply">
                    This fleet is out of supply and cannot initiate combat.
                  </p>
                )}
                <div className="fleet-manager-row">
                  <label className="fleet-manager-label">
                    Defender fleet id
                    <input
                      data-testid="fleet-battle-defender"
                      type="text"
                      value={defenderFleetId}
                      onChange={(e) => setDefenderFleetId(e.target.value)}
                      placeholder="uuid"
                      disabled={busy !== null || outOfSupply}
                    />
                  </label>
                  <button
                    type="button"
                    data-testid="fleet-battle-initiate-submit"
                    disabled={busy !== null || !defenderIdValid || outOfSupply}
                    onClick={() =>
                      run('initiate', async () => {
                        const created = (await fleetAPI.initiateBattle(
                          selected.id,
                          defenderIdTrimmed,
                        )) as { battle_id?: string };
                        const id = created?.battle_id ? String(created.battle_id) : null;
                        if (id) setBattleId(id);
                        setLocalRounds([]);
                        await refresh();
                      })
                    }
                  >
                    {busy === 'initiate' ? 'Initiating…' : 'Initiate battle'}
                  </button>
                </div>
              </section>
            )}

            {inBattle && (
              <section
                className="fleet-manager-battle"
                data-testid="fleet-battle-viewer"
                aria-label="Active battle"
              >
                <h4 className="fleet-manager-subheading">Battle viewer</h4>
                {battleEnded && (
                  <p className="fleet-manager-battle-meta" data-testid="fleet-battle-terminal">
                    Battle ended{battleStatus?.winner ? ` — winner ${battleStatus.winner}` : ''}.
                  </p>
                )}
                {!battleId && (
                  <p className="fleet-manager-muted" data-testid="fleet-battle-waiting">
                    Looking up active battle for this fleet…
                  </p>
                )}
                {battleId && !battleEnded && (
                  <div className="fleet-manager-row">
                    <button
                      type="button"
                      data-testid="fleet-battle-simulate"
                      disabled={busy !== null}
                      onClick={() =>
                        run('simulate', async () => {
                          const result = (await fleetAPI.simulateBattleRound(battleId)) as {
                            battle_id?: string;
                            battle_ongoing?: boolean;
                            winner?: string | null;
                            round_results?: BattleLogEntry;
                          };
                          const flat = result?.round_results
                            ? flattenRoundEntry(result.round_results)
                            : null;
                          if (flat) {
                            setLocalRounds((prev) => [...prev, flat]);
                          }
                          if (result?.battle_ongoing === false) {
                            setBattleStatus((prev) => ({
                              ...(prev ?? {}),
                              is_active: false,
                              winner: result.winner ?? prev?.winner ?? null,
                            }));
                            await refresh();
                            return;
                          }
                          const detail = (await fleetAPI.getBattle(battleId)) as BattleStatusSnapshot;
                          setBattleStatus(
                            detail && typeof detail === 'object' ? detail : null,
                          );
                        })
                      }
                    >
                      {busy === 'simulate' ? 'Resolving round…' : 'Simulate round'}
                    </button>
                  </div>
                )}
                {battleStatus && (
                  <>
                    <p className="fleet-manager-battle-meta" data-testid="fleet-battle-meta">
                      Phase {battleStatus.phase ?? '—'} · rounds completed{' '}
                      {battleStatus.rounds_completed ?? logRounds.length}
                      {battleStatus.attacker?.ships_remaining != null &&
                        battleStatus.defender?.ships_remaining != null && (
                          <>
                            {' '}
                            · ships {battleStatus.attacker.ships_remaining} vs{' '}
                            {battleStatus.defender.ships_remaining}
                          </>
                        )}
                      {battleStatus.winner ? ` · winner ${battleStatus.winner}` : ''}
                    </p>
                    {logRounds.length > 0 ? (
                      <ol className="fleet-manager-battle-log" data-testid="fleet-battle-log">
                        {logRounds.map((entry, idx) => (
                          <li
                            key={`round-${entry.round}-${idx}`}
                            data-testid={`fleet-battle-round-${entry.round}`}
                          >
                            Round {entry.round}: atk dmg {String(entry.attacker_damage ?? '—')} · def
                            dmg {String(entry.defender_damage ?? '—')}
                            {entry.destroyed > 0 ? ` · destroyed ${entry.destroyed}` : ''}
                            {entry.retreated > 0 ? ` · retreated ${entry.retreated}` : ''}
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p
                        className="fleet-manager-muted"
                        data-testid="fleet-battle-log-residual"
                      >
                        Round-by-round battle_log is not on player GET /fleets/battles/
                        {'{id}'} yet (tip returns rounds_completed / casualties only). Use
                        Simulate round to record each server-resolved round locally.
                      </p>
                    )}
                    {battleStatus.casualties && (
                      <p className="fleet-manager-muted" data-testid="fleet-battle-casualties">
                        Casualties — attacker:{' '}
                        {(battleStatus.casualties.attacker ?? []).filter((c) => c.destroyed).length}
                        , defender:{' '}
                        {(battleStatus.casualties.defender ?? []).filter((c) => c.destroyed).length}
                      </p>
                    )}
                    <p className="fleet-manager-muted" data-testid="fleet-battle-retreats">
                      Retreated — attacker:{' '}
                      {battleStatus.attacker?.ships_retreated ??
                        (battleStatus.casualties?.attacker ?? []).filter((c) => c.retreated).length}
                      , defender:{' '}
                      {battleStatus.defender?.ships_retreated ??
                        (battleStatus.casualties?.defender ?? []).filter((c) => c.retreated).length}
                      {' '}(hull-break retreat is server-side; no player retreat POST)
                    </p>
                  </>
                )}
              </section>
            )}

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
                      disabled={busy !== null || inBattle}
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

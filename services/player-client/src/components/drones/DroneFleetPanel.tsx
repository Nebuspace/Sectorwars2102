/**
 * DroneFleetPanel — typed DroneType lifecycle (LEG-277).
 *
 * Distinct from planetary DefenseConfiguration fighters, which the colony UI
 * labels "Drones". This panel manages individually-tracked drones
 * (attack/defense/scout/mining/repair) via /api/v1/drones/*.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { combatAPI, droneFleetAPI, type DroneTypeCatalogEntry } from '../../services/api';
import './drone-fleet.css';

type DroneRow = {
  id: string;
  drone_type: string;
  name: string | null;
  level: number;
  health: number;
  max_health: number;
  status: string | null;
};

type DeploymentRow = {
  deploymentId: string;
  droneId: string;
  sectorId: string;
  deployedAt: string;
  droneType: string;
  health: number;
  maxHealth: number;
};

const normalizeDeployment = (raw: unknown): DeploymentRow | null => {
  const r = asRecord(raw);
  if (!r) return null;
  const deploymentId = typeof r.deploymentId === 'string' ? r.deploymentId : null;
  if (!deploymentId) return null;
  return {
    deploymentId,
    droneId: typeof r.droneId === 'string' ? r.droneId : '',
    sectorId: typeof r.sectorId === 'string' ? r.sectorId : '',
    deployedAt: typeof r.deployedAt === 'string' ? r.deployedAt : '',
    droneType: typeof r.droneType === 'string' ? r.droneType : 'unknown',
    health: typeof r.health === 'number' ? r.health : 0,
    maxHealth: typeof r.maxHealth === 'number' ? r.maxHealth : 0,
  };
};

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

const normalizeDrone = (raw: unknown): DroneRow | null => {
  const r = asRecord(raw);
  if (!r) return null;
  const id = typeof r.id === 'string' ? r.id : null;
  if (!id) return null;
  return {
    id,
    drone_type: typeof r.drone_type === 'string' ? r.drone_type : 'unknown',
    name: typeof r.name === 'string' ? r.name : null,
    level: typeof r.level === 'number' ? r.level : 1,
    health: typeof r.health === 'number' ? r.health : 0,
    max_health: typeof r.max_health === 'number' ? r.max_health : 0,
    status: typeof r.status === 'string' ? r.status : null,
  };
};

const errorMessage = (error: unknown, fallback: string): string => {
  const e = asRecord(error);
  const response = asRecord(e?.response);
  const data = asRecord(response?.data);
  const raw = data?.detail ?? data?.message ?? e?.message;
  if (typeof raw === 'string' && raw) return raw;
  return fallback;
};

export const DroneFleetPanel: React.FC = () => {
  const { currentSector, playerState } = useGame();
  const [types, setTypes] = useState<DroneTypeCatalogEntry[]>([]);
  const [drones, setDrones] = useState<DroneRow[]>([]);
  const [deployments, setDeployments] = useState<DeploymentRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [createType, setCreateType] = useState('attack');
  const [createName, setCreateName] = useState('');
  const [repairAmounts, setRepairAmounts] = useState<Record<string, string>>({});
  const [deployCount, setDeployCount] = useState(1);
  const [notice, setNotice] = useState<string | null>(null);

  // GS deploy expects sector UUID string. Client Sector.id is loosely typed;
  // prefer string id when present.
  const sectorUuid = (() => {
    const id = currentSector?.id as unknown;
    if (typeof id === 'string' && id.length > 8) return id;
    return '';
  })();

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [typesRes, listRes, deployedRes] = await Promise.all([
        droneFleetAPI.getTypes(),
        droneFleetAPI.getMyDrones(false),
        combatAPI.getDeployedDrones(),
      ]);
      const catalog = asRecord(typesRes);
      const typeList = Array.isArray(catalog?.types) ? (catalog!.types as DroneTypeCatalogEntry[]) : [];
      setTypes(typeList);
      setCreateType((prev) =>
        typeList.some((t) => t.type === prev) ? prev : (typeList[0]?.type ?? prev),
      );
      const rows = Array.isArray(listRes)
        ? listRes.map(normalizeDrone).filter((d): d is DroneRow => d !== null)
        : [];
      setDrones(rows);
      const deployedRecord = asRecord(deployedRes);
      const deploymentRows = Array.isArray(deployedRecord?.deployments)
        ? (deployedRecord!.deployments as unknown[])
            .map(normalizeDeployment)
            .filter((d): d is DeploymentRow => d !== null)
        : [];
      setDeployments(deploymentRows);
    } catch (err) {
      setError(errorMessage(err, 'Could not load drone fleet.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = async (key: string, fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (err) {
      setError(errorMessage(err, 'Drone fleet action failed.'));
    } finally {
      setBusy(null);
    }
  };

  const onCreate = () =>
    run('create', async () => {
      await droneFleetAPI.create({
        drone_type: createType,
        ...(createName.trim() ? { name: createName.trim() } : {}),
      });
      setCreateName('');
      setNotice(`Created ${createType} drone.`);
      await refresh();
    });

  const onRepair = (droneId: string) =>
    run(`repair-${droneId}`, async () => {
      const amount = parseInt(repairAmounts[droneId] || '10', 10);
      if (!Number.isFinite(amount) || amount <= 0) {
        throw { message: 'Repair amount must be a positive integer.' };
      }
      await droneFleetAPI.repair(droneId, amount);
      setNotice('Repair applied.');
      await refresh();
    });

  const onUpgrade = (droneId: string) =>
    run(`upgrade-${droneId}`, async () => {
      await droneFleetAPI.upgrade(droneId);
      setNotice('Drone upgraded.');
      await refresh();
    });

  const onRecall = (droneId: string) =>
    run(`recall-${droneId}`, async () => {
      await droneFleetAPI.recall(droneId);
      setNotice('Drone recalled.');
      await refresh();
    });

  const onRecallDeployment = (deploymentId: string) =>
    run(`recall-deployment-${deploymentId}`, async () => {
      await combatAPI.recallDrones(deploymentId);
      setNotice('Deployment recalled.');
      await refresh();
    });

  const onDeploy = () =>
    run('deploy', async () => {
      if (!sectorUuid) {
        throw {
          message:
            'Current sector UUID unavailable — cannot deploy. (Batch deploy needs sector UUID.)',
        };
      }
      const count = Math.max(1, Math.round(deployCount) || 1);
      await combatAPI.deployDrones(sectorUuid, count);
      setNotice(`Deployed up to ${count} undeployed drone(s) to this sector.`);
      await refresh();
    });

  const onDeployOne = (droneId: string) =>
    run(`deploy-one-${droneId}`, async () => {
      if (!sectorUuid) {
        throw { message: 'Current sector UUID unavailable — cannot deploy.' };
      }
      await droneFleetAPI.deployOne(droneId, {
        sector_id: sectorUuid,
        deployment_type: 'defense',
      });
      setNotice('Drone deployed to this sector.');
      await refresh();
    });

  return (
    <div className="drone-fleet-panel" data-testid="drone-fleet-panel">
      <header className="drone-fleet-header">
        <h3 className="drone-fleet-title">Drone Fleet</h3>
        <p className="drone-fleet-blurb">
          Individually tracked drones (attack / defense / scout / mining / repair).
          Not the same as planetary defense fighters labeled &quot;Drones&quot; on colony
          defenses.
        </p>
      </header>

      {loading && <div className="drone-fleet-status">Loading drone fleet…</div>}
      {error && (
        <div className="drone-fleet-error" role="alert">
          {error}
          <button type="button" className="drone-fleet-btn" onClick={() => void refresh()}>
            Retry
          </button>
        </div>
      )}
      {notice && <div className="drone-fleet-notice">{notice}</div>}

      <section className="drone-fleet-section" aria-labelledby="drone-type-catalog">
        <h4 id="drone-type-catalog">Type catalog</h4>
        {types.length === 0 && !loading ? (
          <p className="drone-fleet-empty">No types returned from the yard.</p>
        ) : (
          <ul className="drone-type-list" data-testid="drone-type-catalog">
            {types.map((t) => (
              <li key={t.type} className="drone-type-card">
                <strong>{t.type}</strong>
                <span>{t.description}</span>
                <span className="drone-type-stats">
                  HP {t.base_stats?.health} · ATK {t.base_stats?.attack_power} · DEF{' '}
                  {t.base_stats?.defense_power} · SPD {t.base_stats?.speed}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="drone-fleet-section" aria-labelledby="drone-create">
        <h4 id="drone-create">Commission drone</h4>
        <div className="drone-fleet-row">
          <label>
            Type
            <select
              aria-label="Drone type to create"
              value={createType}
              disabled={Boolean(busy)}
              onChange={(e) => setCreateType(e.target.value)}
            >
              {(types.length > 0 ? types.map((t) => t.type) : ['attack', 'defense', 'scout', 'mining', 'repair']).map(
                (t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ),
              )}
            </select>
          </label>
          <label>
            Name (optional)
            <input
              aria-label="Optional drone name"
              value={createName}
              disabled={Boolean(busy)}
              onChange={(e) => setCreateName(e.target.value)}
              placeholder="Callsign"
            />
          </label>
          <button
            type="button"
            className="drone-fleet-btn primary"
            disabled={Boolean(busy)}
            onClick={() => void onCreate()}
          >
            {busy === 'create' ? 'Building…' : 'Create'}
          </button>
        </div>
      </section>

      <section className="drone-fleet-section" aria-labelledby="drone-deploy">
        <h4 id="drone-deploy">Deploy (undeployed pool)</h4>
        <p className="drone-fleet-hint">
          Batch-deploys N undeployed drones to the current sector UUID via the existing
          deploy contract. Create a typed drone first; deploy does not pick by type
          server-side.
          {playerState?.current_sector_id != null && (
            <> Sector #{playerState.current_sector_id}.</>
          )}
        </p>
        <div className="drone-fleet-row">
          <label>
            Count
            <input
              type="number"
              min={1}
              aria-label="Deploy drone count"
              value={deployCount}
              disabled={Boolean(busy) || !sectorUuid}
              onChange={(e) => setDeployCount(parseInt(e.target.value, 10) || 1)}
            />
          </label>
          <button
            type="button"
            className="drone-fleet-btn primary"
            disabled={Boolean(busy) || !sectorUuid}
            onClick={() => void onDeploy()}
          >
            {busy === 'deploy' ? 'Deploying…' : 'Deploy'}
          </button>
        </div>
        {!sectorUuid && (
          <p className="drone-fleet-hint">Waiting for current sector UUID before deploy is enabled.</p>
        )}
      </section>

      <section className="drone-fleet-section" aria-labelledby="drone-deployed-list">
        <h4 id="drone-deployed-list">Active deployments</h4>
        <p className="drone-fleet-hint">
          Sector deployments from GET /drones/deployed — recall by deployment id.
        </p>
        {deployments.length === 0 && !loading ? (
          <p className="drone-fleet-empty" data-testid="drone-deployed-empty">
            No active deployments.
          </p>
        ) : (
          <ul className="drone-roster" data-testid="drone-deployed-list">
            {deployments.map((dep) => (
              <li key={dep.deploymentId} className="drone-roster-row">
                <div className="drone-roster-meta">
                  <strong>{dep.droneType}</strong>
                  <span>
                    Sector {dep.sectorId.slice(0, 8)}… · {dep.health}/{dep.maxHealth} HP
                    {dep.deployedAt ? ` · since ${dep.deployedAt}` : ''}
                  </span>
                </div>
                <div className="drone-roster-actions">
                  <button
                    type="button"
                    className="drone-fleet-btn"
                    data-testid={`deployment-recall-${dep.deploymentId}`}
                    disabled={Boolean(busy)}
                    onClick={() => void onRecallDeployment(dep.deploymentId)}
                  >
                    Recall
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="drone-fleet-section" aria-labelledby="drone-roster">
        <h4 id="drone-roster">Your drones</h4>
        {drones.length === 0 && !loading ? (
          <p className="drone-fleet-empty">No drones in the hangar yet.</p>
        ) : (
          <ul className="drone-roster" data-testid="drone-roster">
            {drones.map((d) => (
              <li key={d.id} className="drone-roster-row">
                <div className="drone-roster-meta">
                  <strong>{d.name || d.drone_type}</strong>
                  <span>
                    {d.drone_type} · L{d.level} · {d.health}/{d.max_health} HP
                    {d.status ? ` · ${d.status}` : ''}
                  </span>
                </div>
                <div className="drone-roster-actions">
                  <input
                    type="number"
                    min={1}
                    aria-label={`Repair amount for ${d.id}`}
                    placeholder="HP"
                    value={repairAmounts[d.id] ?? '10'}
                    disabled={Boolean(busy)}
                    onChange={(e) =>
                      setRepairAmounts((prev) => ({ ...prev, [d.id]: e.target.value }))
                    }
                  />
                  <button
                    type="button"
                    className="drone-fleet-btn"
                    disabled={Boolean(busy)}
                    onClick={() => void onRepair(d.id)}
                  >
                    Repair
                  </button>
                  <button
                    type="button"
                    className="drone-fleet-btn"
                    disabled={Boolean(busy)}
                    onClick={() => void onUpgrade(d.id)}
                  >
                    Upgrade
                  </button>
                  {d.status === 'idle' && (
                    <button
                      type="button"
                      className="drone-fleet-btn"
                      data-testid={`drone-deploy-one-${d.id}`}
                      aria-label={`Deploy ${d.name || d.drone_type}`}
                      disabled={Boolean(busy) || !sectorUuid}
                      onClick={() => void onDeployOne(d.id)}
                    >
                      {busy === `deploy-one-${d.id}` ? 'Deploying…' : 'Deploy this drone'}
                    </button>
                  )}
                  {(d.status === 'deployed' || d.status === 'combat') && (
                    <button
                      type="button"
                      className="drone-fleet-btn"
                      data-testid={`drone-recall-${d.id}`}
                      disabled={Boolean(busy)}
                      onClick={() => void onRecall(d.id)}
                    >
                      Recall
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
};

export default DroneFleetPanel;

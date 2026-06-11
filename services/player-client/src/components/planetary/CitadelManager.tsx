import React, { useState, useEffect, useCallback } from 'react';
import { citadelAPI } from '../../services/api';
import './citadel.css';

interface CitadelNextLevel {
  level: number;
  name: string;
  upgrade_cost: number;
  upgrade_hours: number;
  resource_cost: Record<string, number>;
  max_population: number;
  safe_storage: number;
  drone_capacity: number;
}

interface CitadelInfo {
  success: boolean;
  planet_id: string;
  planet_name: string;
  citadel_level: number;
  citadel_name: string;
  max_population: number;
  safe_storage: number;
  safe_credits: number;
  drone_capacity: number;
  is_upgrading: boolean;
  upgrade_started_at?: string;
  upgrade_complete_at?: string;
  upgrade_remaining_seconds?: number;
  next_level: CitadelNextLevel | null;
}

interface CitadelManagerProps {
  planetId: string;
  playerCredits: number;
  onUpdate?: () => void;
}

/** Canon 5-level progression (mirrors gameserver CITADEL_LEVELS). */
const CITADEL_TRACK = [
  { level: 1, name: 'Outpost', maxColonists: 1000, safeStorage: 100000, droneCapacity: 10 },
  { level: 2, name: 'Garrison', maxColonists: 5000, safeStorage: 500000, droneCapacity: 25 },
  { level: 3, name: 'Fortress', maxColonists: 15000, safeStorage: 2000000, droneCapacity: 50 },
  { level: 4, name: 'Stronghold', maxColonists: 50000, safeStorage: 10000000, droneCapacity: 100 },
  { level: 5, name: 'Citadel', maxColonists: 200000, safeStorage: 50000000, droneCapacity: 200 },
] as const;

/** Defense-level prerequisites enforced by the gameserver before each upgrade. */
const CITADEL_PREREQS: Record<number, string> = {
  3: 'Requires planetary defense level 2+',
  4: 'Requires planetary defense level 5+',
  5: 'Requires planetary defense level 8+',
};

const RESOURCE_LABELS: Record<string, string> = {
  fuel_ore: '⛽ Fuel Ore',
  organics: '🌿 Organics',
  equipment: '⚙️ Equipment',
};

const compact = (n: number): string => {
  if (n >= 1_000_000) return `${n % 1_000_000 === 0 ? n / 1_000_000 : (n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${n % 1_000 === 0 ? n / 1_000 : (n / 1_000).toFixed(1)}k`;
  return `${n}`;
};

const CitadelManager: React.FC<CitadelManagerProps> = ({
  planetId,
  playerCredits,
  onUpdate,
}) => {
  const [citadel, setCitadel] = useState<CitadelInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [depositAmount, setDepositAmount] = useState('');
  const [withdrawAmount, setWithdrawAmount] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const fetchCitadel = useCallback(async () => {
    try {
      setLoading(true);
      const data = await citadelAPI.getInfo(planetId);
      setCitadel(data);
      setError(null);
    } catch (err: any) {
      setError(err.message || 'Failed to load citadel info');
    } finally {
      setLoading(false);
    }
  }, [planetId]);

  useEffect(() => {
    fetchCitadel();
  }, [fetchCitadel]);

  const handleUpgrade = async () => {
    if (!citadel || actionLoading) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      await citadelAPI.upgrade(planetId);
      setActionMessage('Citadel upgrade initiated!');
      await fetchCitadel();
      onUpdate?.();
    } catch (err: any) {
      setActionMessage(err.message || 'Upgrade failed');
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeposit = async () => {
    const amount = parseInt(depositAmount);
    if (!amount || amount <= 0 || actionLoading) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      await citadelAPI.deposit(planetId, amount);
      setActionMessage(`Deposited ${amount.toLocaleString()} credits`);
      setDepositAmount('');
      await fetchCitadel();
      onUpdate?.();
    } catch (err: any) {
      setActionMessage(err.message || 'Deposit failed');
    } finally {
      setActionLoading(false);
    }
  };

  const handleWithdraw = async () => {
    const amount = parseInt(withdrawAmount);
    if (!amount || amount <= 0 || actionLoading) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      await citadelAPI.withdraw(planetId, amount);
      setActionMessage(`Withdrew ${amount.toLocaleString()} credits`);
      setWithdrawAmount('');
      await fetchCitadel();
      onUpdate?.();
    } catch (err: any) {
      setActionMessage(err.message || 'Withdraw failed');
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="citadel-manager citadel-loading">
        <div className="citadel-spinner" />
        <span>Loading citadel...</span>
      </div>
    );
  }

  if (error || !citadel) {
    return (
      <div className="citadel-manager citadel-error">
        <span>{error || 'Citadel unavailable'}</span>
        <button onClick={fetchCitadel} className="citadel-retry-btn">Retry</button>
      </div>
    );
  }

  const level = citadel.citadel_level ?? 0;
  const safeCapacity = citadel.safe_storage ?? 0;
  const safeCredits = citadel.safe_credits ?? 0;
  const storagePercent = safeCapacity > 0 ? (safeCredits / safeCapacity) * 100 : 0;
  const next = citadel.next_level;
  const upgradeCost = next?.upgrade_cost ?? 0;
  const canAffordUpgrade = playerCredits >= upgradeCost;

  const upgradeDisabledReason = actionLoading
    ? 'Action in progress'
    : !canAffordUpgrade
      ? `Insufficient credits: need ${upgradeCost.toLocaleString()}, you have ${playerCredits.toLocaleString()}`
      : next && CITADEL_PREREQS[next.level]
        ? `${CITADEL_PREREQS[next.level]} (validated on upgrade)`
        : 'Begin citadel upgrade';

  return (
    <div className="citadel-manager">
      <div className="citadel-header">
        <h3>Citadel</h3>
        <span className="citadel-level-badge">Level {level}</span>
      </div>

      <div className="citadel-level-name">
        {citadel.citadel_name || (level === 0 ? 'No Citadel' : `Level ${level}`)}
      </div>

      {/* 5-Level Stepped Progression Track */}
      <div className="citadel-track" role="list" aria-label="Citadel progression">
        {CITADEL_TRACK.map((step) => {
          const state = step.level < level ? 'completed' : step.level === level ? 'current' : 'locked';
          const isNext = next?.level === step.level;
          return (
            <div
              key={step.level}
              role="listitem"
              className={`citadel-step ${state} ${isNext ? 'next-up' : ''}`}
              title={`L${step.level} ${step.name} — Workforce cap ${step.maxColonists.toLocaleString()}, safe storage ${step.safeStorage.toLocaleString()} cr, ${step.droneCapacity} drones${CITADEL_PREREQS[step.level] ? `. ${CITADEL_PREREQS[step.level]}` : ''}`}
            >
              <div className="step-node">
                <span className="step-level">L{step.level}</span>
              </div>
              <div className="step-name">{step.name}</div>
              <div className="step-stats">
                <span className="step-stat" title={`Max colonists: ${step.maxColonists.toLocaleString()}`}>
                  👥 {compact(step.maxColonists)}
                </span>
                <span className="step-stat" title={`Safe storage: ${step.safeStorage.toLocaleString()} credits`}>
                  🔒 {compact(step.safeStorage)}
                </span>
                <span className="step-stat" title={`Drone capacity: ${step.droneCapacity}`}>
                  ✈️ {step.droneCapacity}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="citadel-stats">
        <div className="citadel-stat">
          <span className="stat-label">Safe Storage</span>
          <div className="storage-bar">
            <div
              className="storage-fill"
              style={{ width: `${Math.min(100, storagePercent)}%` }}
            />
          </div>
          <span className="stat-value">
            {safeCredits.toLocaleString()} / {safeCapacity.toLocaleString()} credits
          </span>
        </div>
        <div className="citadel-stat-row">
          <div className="citadel-stat">
            <span className="stat-label">Workforce Cap</span>
            <span className="stat-value">{(citadel.max_population ?? 0).toLocaleString()}</span>
          </div>
          <div className="citadel-stat">
            <span className="stat-label">Drone Capacity</span>
            <span className="stat-value">{citadel.drone_capacity ?? 0}</span>
          </div>
        </div>
      </div>

      {/* Safe Storage Controls */}
      <div className="citadel-storage-controls">
        <div className="storage-action">
          <input
            type="number"
            placeholder="Amount"
            value={depositAmount}
            onChange={(e) => setDepositAmount(e.target.value)}
            min="1"
            className="storage-input"
            disabled={level === 0}
            title={level === 0 ? 'Build an Outpost (L1) to unlock safe storage' : 'Credits to deposit into the safe'}
          />
          <button
            onClick={handleDeposit}
            disabled={actionLoading || !depositAmount || level === 0}
            className="citadel-btn deposit-btn"
            title={
              level === 0
                ? 'Build an Outpost (L1) to unlock safe storage'
                : !depositAmount
                  ? 'Enter an amount to deposit'
                  : actionLoading
                    ? 'Action in progress'
                    : 'Deposit credits into safe storage'
            }
          >
            Deposit
          </button>
        </div>
        <div className="storage-action">
          <input
            type="number"
            placeholder="Amount"
            value={withdrawAmount}
            onChange={(e) => setWithdrawAmount(e.target.value)}
            min="1"
            max={safeCredits.toString()}
            className="storage-input"
            disabled={safeCredits === 0}
            title={safeCredits === 0 ? 'No credits in safe storage to withdraw' : 'Credits to withdraw from the safe'}
          />
          <button
            onClick={handleWithdraw}
            disabled={actionLoading || !withdrawAmount || safeCredits === 0}
            className="citadel-btn withdraw-btn"
            title={
              safeCredits === 0
                ? 'No credits in safe storage to withdraw'
                : !withdrawAmount
                  ? 'Enter an amount to withdraw'
                  : actionLoading
                    ? 'Action in progress'
                    : 'Withdraw credits from safe storage'
            }
          >
            Withdraw
          </button>
        </div>
      </div>

      {/* Upgrade Section */}
      {next && !citadel.is_upgrading && (
        <div className="citadel-upgrade">
          <div className="upgrade-info">
            <span className="upgrade-label">
              Upgrade to L{next.level} — {next.name}
            </span>
            <span className="upgrade-cost">
              {upgradeCost > 0 ? `${upgradeCost.toLocaleString()} credits` : 'Free'}
            </span>
            {next.upgrade_hours > 0 && (
              <span className="upgrade-time">{next.upgrade_hours}h build time</span>
            )}
            {next.resource_cost && Object.keys(next.resource_cost).length > 0 && (
              <span className="upgrade-resources">
                {Object.entries(next.resource_cost)
                  .map(([res, amt]) => `${RESOURCE_LABELS[res] || res} ${amt.toLocaleString()}`)
                  .join(' · ')}
              </span>
            )}
            {CITADEL_PREREQS[next.level] && (
              <span className="upgrade-prereq">⚠ {CITADEL_PREREQS[next.level]}</span>
            )}
            <span className="upgrade-gains">
              Gains: 👥 {compact(next.max_population)} · 🔒 {compact(next.safe_storage)} · ✈️ {next.drone_capacity}
            </span>
          </div>
          <button
            onClick={handleUpgrade}
            disabled={actionLoading || !canAffordUpgrade}
            className="citadel-btn upgrade-btn"
            title={upgradeDisabledReason}
          >
            {!canAffordUpgrade ? 'Insufficient Credits' : 'Upgrade'}
          </button>
        </div>
      )}

      {citadel.is_upgrading && citadel.upgrade_complete_at && (
        <div className="citadel-upgrading">
          Upgrading... Completes at{' '}
          {new Date(citadel.upgrade_complete_at).toLocaleString()}
        </div>
      )}

      {!next && !citadel.is_upgrading && (
        <div className="citadel-max-level">Maximum Level Reached</div>
      )}

      {actionMessage && (
        <div className="citadel-message">{actionMessage}</div>
      )}
    </div>
  );
};

export default CitadelManager;

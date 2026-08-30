import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { planetaryAPI } from '../../services/api';
import './professions-panel.css';

/** Citadel L3+ (Colony phase) — mirrors gameserver MIN_CITADEL_FOR_TRAINING. */
const MIN_CITADEL_FOR_TRAINING = 3;

/** Twelve canon professions (colonist_profession.ProfessionType). */
const PROFESSION_ORDER = [
  'SPACE_ENGINEERS',
  'STRUCTURAL_ENGINEERS',
  'MINING_ENGINEERS',
  'RESEARCH_SCIENTISTS',
  'AGRICULTURAL_SCIENTISTS',
  'MEDICAL_PROFESSIONALS',
  'TERRAFORM_ENGINEERS',
  'COMBAT_PILOTS',
  'DEFENSE_COORDINATORS',
  'STRATEGIC_ANALYSTS',
  'TRADE_SPECIALISTS',
  'INDUSTRIAL_MANAGERS',
] as const;

const PROFESSION_LABELS: Record<string, string> = {
  SPACE_ENGINEERS: 'Space Engineers',
  STRUCTURAL_ENGINEERS: 'Structural Engineers',
  MINING_ENGINEERS: 'Mining Engineers',
  RESEARCH_SCIENTISTS: 'Research Scientists',
  AGRICULTURAL_SCIENTISTS: 'Agricultural Scientists',
  MEDICAL_PROFESSIONALS: 'Medical Professionals',
  TERRAFORM_ENGINEERS: 'Terraform Engineers',
  COMBAT_PILOTS: 'Combat Pilots',
  DEFENSE_COORDINATORS: 'Defense Coordinators',
  STRATEGIC_ANALYSTS: 'Strategic Analysts',
  TRADE_SPECIALISTS: 'Trade Specialists',
  INDUSTRIAL_MANAGERS: 'Industrial Managers',
};

interface TrainingQueueRow {
  id: string;
  profession: string;
  trainee_count: number;
  queued_at?: string | null;
  completes_at?: string | null;
  status: string;
  training_days?: number | null;
}

interface PlanetProfessionsState {
  planet_id: string;
  generic_colonists: number;
  cost_blocked: boolean;
  cost_block_reason?: string;
  professions: Record<string, number>;
  training_queue: TrainingQueueRow[];
  training_durations_days?: Record<string, number>;
  /** Per-profession training gates from gameserver (LEG-2697 / LEG-2698). */
  training_eligibility?: Record<string, boolean>;
}

export interface ProfessionsPanelProps {
  planetId: string;
  /** From landed poll — client mirrors citadel L3 gate before calling train. */
  citadelLevel?: number | null;
  onUpdate?: () => void;
}

const formatProfessionLabel = (key: string): string =>
  PROFESSION_LABELS[key] ?? key.replace(/_/g, ' ');

/** Static gate copy for known training_eligibility=false keys (GS sends booleans only). */
const TRAINING_ELIGIBILITY_GATE_MESSAGES: Partial<Record<string, string>> = {
  RESEARCH_SCIENTISTS: 'Research Lab level 3 required to train Research Scientists.',
};

const trainingEligibilityGateTestId = (professionKey: string): string =>
  professionKey === 'RESEARCH_SCIENTISTS'
    ? 'professions-research-lab-gate'
    : `professions-training-gate-${professionKey.toLowerCase()}`;

const trainingEligibilityGateMessage = (professionKey: string): string =>
  TRAINING_ELIGIBILITY_GATE_MESSAGES[professionKey] ??
  `${formatProfessionLabel(professionKey)} training is not available yet — prerequisite buildings may be required.`;

const formatCompletesAt = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return new Date(ms).toLocaleString();
};

const isNotOwnerError = (message: string): boolean =>
  /not_owner|only the planet owner/i.test(message);

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Surface gameserver detail when profession state load fails. */
export function formatProfessionsLoadError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  // Network collapse (fetch TypeError) is not gameserver copy — use the fallback.
  const hasServerDetail =
    !(err instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim());

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not own this planet.';
  }

  if (status === 404) {
    if (hasServerDetail) return message!;
    return 'Planet not found.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to load professions';
}

const ProfessionsPanel: React.FC<ProfessionsPanelProps> = ({
  planetId,
  citadelLevel,
  onUpdate,
}) => {
  const [state, setState] = useState<PlanetProfessionsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [hidden, setHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [selectedProfession, setSelectedProfession] = useState<string>(PROFESSION_ORDER[0]);
  const [traineeCount, setTraineeCount] = useState(100);

  const citadelGateOpen =
    typeof citadelLevel === 'number' && citadelLevel >= MIN_CITADEL_FOR_TRAINING;

  const fetchState = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = (await planetaryAPI.getPlanetProfessions(planetId)) as PlanetProfessionsState;
      setState(data);
      setHidden(false);
    } catch (err) {
      const message = formatProfessionsLoadError(err);
      if (isNotOwnerError(message)) {
        setHidden(true);
        setState(null);
        setError(null);
        return;
      }
      setError(message);
      setState(null);
    } finally {
      setLoading(false);
    }
  }, [planetId]);

  useEffect(() => {
    fetchState();
  }, [fetchState]);

  const eligibility = state?.training_eligibility;
  const eligibleProfessions = useMemo(
    () =>
      eligibility != null
        ? PROFESSION_ORDER.filter((key) => eligibility[key] !== false)
        : [...PROFESSION_ORDER],
    [eligibility],
  );

  useEffect(() => {
    if (eligibleProfessions.length === 0) {
      return;
    }
    if (!eligibleProfessions.includes(selectedProfession as (typeof PROFESSION_ORDER)[number])) {
      setSelectedProfession(eligibleProfessions[0]);
    }
  }, [eligibleProfessions, selectedProfession]);

  const ineligibleProfessions = useMemo(
    () =>
      citadelGateOpen && eligibility != null
        ? PROFESSION_ORDER.filter((key) => eligibility[key] === false)
        : [],
    [citadelGateOpen, eligibility],
  );

  const handleTrain = async () => {
    if (!citadelGateOpen) {
      setActionMessage('Citadel level 3+ required to queue profession training.');
      return;
    }
    setActionLoading(true);
    setActionMessage(null);
    try {
      const result = await planetaryAPI.trainPlanetProfession(
        planetId,
        selectedProfession,
        traineeCount,
      );
      const msg =
        typeof result?.message === 'string'
          ? result.message
          : 'Training queued.';
      setActionMessage(msg);
      await fetchState();
      onUpdate?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Training failed';
      if (message.includes('citadel_level_too_low')) {
        setActionMessage('Citadel level 3+ required to queue profession training.');
      } else if (message.includes('research_lab_level_too_low')) {
        setActionMessage('Research Lab level 3 required to train Research Scientists.');
      } else if (message.includes('insufficient_generic_colonists')) {
        setActionMessage('Not enough generic colonists available for this training run.');
      } else if (isNotOwnerError(message)) {
        setHidden(true);
      } else {
        setActionMessage(message);
      }
    } finally {
      setActionLoading(false);
    }
  };

  if (hidden) {
    return null;
  }

  if (loading && !state) {
    return (
      <section className="professions-panel" aria-label="Colonist professions">
        <h3 className="professions-panel__title">Colonist Professions</h3>
        <p className="professions-panel__meta">Loading profession state…</p>
      </section>
    );
  }

  if (error && !state) {
    return (
      <section className="professions-panel" aria-label="Colonist professions">
        <h3 className="professions-panel__title">Colonist Professions</h3>
        <p className="professions-panel__error">{error}</p>
      </section>
    );
  }

  if (!state) {
    return null;
  }

  const durations = state.training_durations_days ?? {};
  const queue = state.training_queue ?? [];

  return (
    <section className="professions-panel" aria-label="Colonist professions">
      <h3 className="professions-panel__title">Colonist Professions</h3>
      <p className="professions-panel__meta">
        Generic colonists available: {state.generic_colonists.toLocaleString()}
      </p>

      {!citadelGateOpen && (
        <p className="professions-panel__notice">
          Citadel level 3+ (Colony phase) required to operate profession training on this planet.
        </p>
      )}

      {state.cost_blocked && (
        <p className="professions-panel__notice" data-testid="professions-cost-blocked">
          {state.cost_block_reason ??
            'DECISION-NEEDED: profession training costs are not yet ruled — training queues without charge; no prices are shown.'}
        </p>
      )}

      {ineligibleProfessions.map((professionKey) => (
        <p
          key={professionKey}
          className="professions-panel__notice"
          data-testid={trainingEligibilityGateTestId(professionKey)}
        >
          {trainingEligibilityGateMessage(professionKey)}
        </p>
      ))}

      <div className="professions-panel__grid">
        {PROFESSION_ORDER.map((key) => (
          <div key={key} className="professions-panel__row">
            <span>{formatProfessionLabel(key)}</span>
            <span>{(state.professions?.[key] ?? 0).toLocaleString()}</span>
          </div>
        ))}
      </div>

      {queue.length > 0 && (
        <div className="professions-panel__queue">
          <strong>In-flight training</strong>
          {queue.map((row) => (
            <div key={row.id} className="professions-panel__queue-item">
              {formatProfessionLabel(row.profession)} — {row.trainee_count} trainees · completes{' '}
              {formatCompletesAt(row.completes_at)}
              {row.training_days != null ? ` (${row.training_days}d)` : ''}
            </div>
          ))}
        </div>
      )}

      <div className="professions-panel__train">
        <label>
          Profession
          <select
            value={selectedProfession}
            onChange={(e) => setSelectedProfession(e.target.value)}
            disabled={!citadelGateOpen || actionLoading}
          >
            {eligibleProfessions.map((key) => (
              <option key={key} value={key}>
                {formatProfessionLabel(key)}
                {durations[key] != null ? ` (${durations[key]}d)` : ''}
              </option>
            ))}
          </select>
        </label>
        <label>
          Trainees
          <input
            type="number"
            min={1}
            value={traineeCount}
            onChange={(e) => setTraineeCount(Math.max(1, Number(e.target.value) || 1))}
            disabled={!citadelGateOpen || actionLoading}
          />
        </label>
        <button
          type="button"
          onClick={handleTrain}
          disabled={!citadelGateOpen || actionLoading}
        >
          {actionLoading ? 'Queueing…' : 'Queue training'}
        </button>
      </div>

      {actionMessage && <p className="professions-panel__notice">{actionMessage}</p>}
    </section>
  );
};

export default ProfessionsPanel;

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

interface TrainingCostRecipe {
  credits: number;
  equipment?: number;
  organics?: number;
}

interface PlanetProfessionsState {
  planet_id: string;
  generic_colonists: number;
  cost_blocked: boolean;
  cost_block_reason?: string;
  professions: Record<string, number>;
  active_professions?: Record<string, number>;
  training_queue: TrainingQueueRow[];
  training_durations_days?: Record<string, number>;
  /** Per-100 provisional recipe from gameserver (LEG-3084 / ADR-0093 item 35). */
  training_costs_per_100?: Record<string, TrainingCostRecipe>;
  /** Per-profession training gates from gameserver (LEG-2697 / LEG-2698). */
  training_eligibility?: Record<string, boolean>;
  /** Citadel-phase specialization ceiling (LEG-3975 / LEG-3969). */
  specialization_cap_max?: number;
  /** Trained specialists + queued trainees counted toward the cap. */
  specialized_total?: number;
  specialization_cap_fraction?: number;
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

/** Scale per-100 recipe to trainee count (matches gameserver TrainingCostPer100.scale). */
export const scaleTrainingCost = (
  recipe: TrainingCostRecipe,
  traineeCount: number,
): TrainingCostRecipe => {
  const scale = (n: number) => Math.floor((n * traineeCount) / 100);
  const scaled: TrainingCostRecipe = { credits: scale(recipe.credits) };
  if (recipe.equipment) scaled.equipment = scale(recipe.equipment);
  if (recipe.organics) scaled.organics = scale(recipe.organics);
  return scaled;
};

/** Human-readable queue cost line for the train form preview (LEG-3759). */
export const formatTrainingCostPreview = (cost: TrainingCostRecipe): string => {
  const parts = [`${cost.credits.toLocaleString()} cr`];
  if (cost.organics) parts.push(`${cost.organics.toLocaleString()} organics`);
  if (cost.equipment) parts.push(`${cost.equipment.toLocaleString()} equipment`);
  return parts.join(' + ');
};

/** Player-safe copy when training would exceed the citadel-phase specialization cap (LEG-3977). */
export const SPECIALIZATION_CAP_EXCEEDED_MESSAGE =
  'Specialization limit reached for this citadel phase — reduce trainees or wait for queued training to complete.';

/** Train-form cap summary when GET payload exposes cap fields (LEG-3975). */
export const formatSpecializationCapSummary = (
  specializedTotal: number,
  capMax: number,
): string =>
  `Specialists (trained + queued): ${specializedTotal.toLocaleString()} / ${capMax.toLocaleString()} citadel cap`;

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

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

/** Surface gameserver detail when profession state load fails. */
export function formatProfessionsLoadError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  // Network collapse (fetch TypeError / axios Network Error) is not gameserver copy — use the fallback.
  const hasServerDetail =
    !(err instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);

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

/** Surface gameserver detail when training queue mutation fails. */
export function formatProfessionsTrainError(err: unknown): string {
  if (err instanceof TypeError) return 'Training failed';
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    !(err instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to train professions on this planet.';
  }

  if (status === 429) {
    return 'Profession training rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) {
    if (message!.includes('specialization_cap_exceeded')) {
      return SPECIALIZATION_CAP_EXCEEDED_MESSAGE;
    }
    return message!;
  }
  return 'Training failed';
}

/** Surface gameserver detail when active profession assignment fails. */
export function formatProfessionsAssignError(err: unknown): string {
  if (err instanceof TypeError) return 'Active assignment failed';
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    !(err instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to assign professions on this planet.';
  }

  if (status === 429) {
    return 'Profession assignment rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Active assignment failed';
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
  const [assignProfession, setAssignProfession] = useState<string>(PROFESSION_ORDER[0]);
  const [activeCount, setActiveCount] = useState(0);
  const [assignLoading, setAssignLoading] = useState(false);

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

  const trainedProfessions = useMemo(
    () => PROFESSION_ORDER.filter((key) => (state?.professions?.[key] ?? 0) > 0),
    [state?.professions],
  );

  useEffect(() => {
    if (trainedProfessions.length === 0) {
      return;
    }
    if (!trainedProfessions.includes(assignProfession as (typeof PROFESSION_ORDER)[number])) {
      setAssignProfession(trainedProfessions[0]);
    }
  }, [trainedProfessions, assignProfession]);

  useEffect(() => {
    if (!state?.active_professions) {
      return;
    }
    const trained = state.professions?.[assignProfession] ?? 0;
    const active = state.active_professions[assignProfession];
    const next =
      typeof active === 'number' ? active : trained > 0 ? trained : 0;
    setActiveCount(next);
  }, [state?.active_professions, state?.professions, assignProfession]);

  const handleAssign = async () => {
    const trained = state?.professions?.[assignProfession] ?? 0;
    if (trained <= 0) {
      setActionMessage('No trained specialists for this profession yet.');
      return;
    }
    if (activeCount < 0 || activeCount > trained) {
      setActionMessage(`Active count must be between 0 and ${trained.toLocaleString()}.`);
      return;
    }
    setAssignLoading(true);
    setActionMessage(null);
    try {
      const result = await planetaryAPI.assignPlanetProfession(
        planetId,
        assignProfession,
        activeCount,
      );
      const msg =
        typeof result?.message === 'string'
          ? result.message
          : 'Active profession assignment updated.';
      setActionMessage(msg);
      await fetchState();
      onUpdate?.();
    } catch (err) {
      const rawMessage = err instanceof Error ? err.message : '';
      if (rawMessage.includes('active_count_exceeds_trained')) {
        setActionMessage('Active count cannot exceed trained specialists.');
      } else if (isNotOwnerError(rawMessage)) {
        setHidden(true);
      } else {
        setActionMessage(formatProfessionsAssignError(err));
      }
    } finally {
      setAssignLoading(false);
    }
  };

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
      const rawMessage = err instanceof Error ? err.message : '';
      if (rawMessage.includes('citadel_level_too_low')) {
        setActionMessage('Citadel level 3+ required to queue profession training.');
      } else if (rawMessage.includes('research_lab_level_too_low')) {
        setActionMessage('Research Lab level 3 required to train Research Scientists.');
      } else if (rawMessage.includes('insufficient_generic_colonists')) {
        setActionMessage('Not enough generic colonists available for this training run.');
      } else if (rawMessage.includes('specialization_cap_exceeded')) {
        setActionMessage(SPECIALIZATION_CAP_EXCEEDED_MESSAGE);
      } else if (isNotOwnerError(rawMessage)) {
        setHidden(true);
      } else {
        setActionMessage(formatProfessionsTrainError(err));
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
  const per100Recipe = state.training_costs_per_100?.[selectedProfession];
  const scaledTrainingCost =
    !state.cost_blocked && per100Recipe
      ? scaleTrainingCost(per100Recipe, traineeCount)
      : null;
  const specializationCapSummary =
    typeof state.specialization_cap_max === 'number' &&
    typeof state.specialized_total === 'number'
      ? formatSpecializationCapSummary(state.specialized_total, state.specialization_cap_max)
      : null;

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
            'Training cost preview is unavailable — charges may not be shown until costs are enabled.'}
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
        {PROFESSION_ORDER.map((key) => {
          const trained = state.professions?.[key] ?? 0;
          const active =
            state.active_professions?.[key] ??
            (trained > 0 ? trained : 0);
          return (
            <div key={key} className="professions-panel__row">
              <span>{formatProfessionLabel(key)}</span>
              <span data-testid={`professions-count-${key}`}>
                {trained.toLocaleString()}
                {trained > 0 ? ` / ${active.toLocaleString()} active` : ''}
              </span>
            </div>
          );
        })}
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

      {trainedProfessions.length > 0 && (
        <div
          className="professions-panel__assign"
          data-testid="professions-active-assign"
        >
          <strong>Active assignment</strong>
          <label>
            Profession
            <select
              value={assignProfession}
              onChange={(e) => setAssignProfession(e.target.value)}
              disabled={assignLoading || actionLoading}
            >
              {trainedProfessions.map((key) => (
                <option key={key} value={key}>
                  {formatProfessionLabel(key)} (
                  {(state.professions?.[key] ?? 0).toLocaleString()} trained)
                </option>
              ))}
            </select>
          </label>
          <label>
            Active headcount
            <input
              type="number"
              min={0}
              max={state.professions?.[assignProfession] ?? 0}
              value={activeCount}
              onChange={(e) =>
                setActiveCount(Math.max(0, Number(e.target.value) || 0))
              }
              disabled={assignLoading || actionLoading}
            />
          </label>
          <button
            type="button"
            onClick={handleAssign}
            disabled={assignLoading || actionLoading}
          >
            {assignLoading ? 'Saving…' : 'Set active'}
          </button>
        </div>
      )}

      <div className="professions-panel__train">
        <label>
          Profession
          <select
            value={selectedProfession}
            onChange={(e) => setSelectedProfession(e.target.value)}
            disabled={!citadelGateOpen || actionLoading || assignLoading}
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
            disabled={!citadelGateOpen || actionLoading || assignLoading}
          />
        </label>
        <button
          type="button"
          onClick={handleTrain}
          disabled={!citadelGateOpen || actionLoading || assignLoading}
        >
          {actionLoading ? 'Queueing…' : 'Queue training'}
        </button>
        {specializationCapSummary && (
          <p
            className="professions-panel__meta"
            data-testid="professions-specialization-cap-summary"
          >
            {specializationCapSummary}
          </p>
        )}
        {scaledTrainingCost && (
          <p
            className="professions-panel__cost-preview"
            data-testid="professions-training-cost-preview"
          >
            Estimated cost (provisional): {formatTrainingCostPreview(scaledTrainingCost)} — charged
            when queued.
          </p>
        )}
      </div>

      {actionMessage && <p className="professions-panel__notice">{actionMessage}</p>}
    </section>
  );
};

export default ProfessionsPanel;

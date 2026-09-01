import React, { useState } from 'react';
import { governanceAPI } from '../../services/api';
import { GOVERNANCE_TYPES, POLICY_TYPE_SUGGESTIONS } from '../../types/governance';

interface ProposePolicyFormProps {
  regionId: string;
  onCreated: () => void;
  onCancel: () => void;
}

interface TradeBonusRow {
  resource: string;
  bonus: string;
}

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** True when err looks like gameserver detail (not bare API Error: N / TypeError noise). */
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

function hasProposePolicyServerDetail(err: unknown, message: string | undefined): boolean {
  // Network collapse (fetch TypeError / axios transport) is not gameserver copy — use the caller fallback.
  if (err instanceof TypeError) return false;
  if (typeof message === 'string' && isNetworkCollapseMessage(message)) return false;
  return (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  );
}

/** Surface GS propose-policy detail (LEG-2945 Soft-ORDER). */
export function formatProposePolicyError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail = hasProposePolicyServerDetail(err, message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to propose a policy in this region.';
  }

  if (status === 429) {
    return 'Policy proposal rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to propose policy.';
}

const ProposePolicyForm: React.FC<ProposePolicyFormProps> = ({ regionId, onCreated, onCancel }) => {
  const [policyType, setPolicyType] = useState<string>(POLICY_TYPE_SUGGESTIONS[0]);
  const [policyTypeCustom, setPolicyTypeCustom] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [votingDurationDays, setVotingDurationDays] = useState('7');

  // KNOWN_POLICY_KEYS guided fields -- each optional; only non-empty ones
  // are sent. Kept as raw strings so an in-progress edit (e.g. "0.") never
  // gets coerced mid-keystroke.
  const [taxRate, setTaxRate] = useState('');
  const [votingThreshold, setVotingThreshold] = useState('');
  const [electionFrequencyDays, setElectionFrequencyDays] = useState('');
  const [governanceType, setGovernanceType] = useState('');
  const [governanceQuorumPct, setGovernanceQuorumPct] = useState('');
  const [tradeBonusRows, setTradeBonusRows] = useState<TradeBonusRow[]>([]);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<string[] | null>(null);
  const [createdPolicyId, setCreatedPolicyId] = useState<string | null>(null);

  const addTradeBonusRow = () =>
    setTradeBonusRows((rows) => [...rows, { resource: '', bonus: '' }]);
  const removeTradeBonusRow = (index: number) =>
    setTradeBonusRows((rows) => rows.filter((_, i) => i !== index));
  const updateTradeBonusRow = (index: number, patch: Partial<TradeBonusRow>) =>
    setTradeBonusRows((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const buildProposedChanges = (): Record<string, unknown> => {
    const changes: Record<string, unknown> = {};
    if (taxRate.trim() !== '') changes.tax_rate = parseFloat(taxRate);
    if (votingThreshold.trim() !== '') changes.voting_threshold = parseFloat(votingThreshold);
    if (electionFrequencyDays.trim() !== '')
      changes.election_frequency_days = parseInt(electionFrequencyDays, 10);
    if (governanceType.trim() !== '') changes.governance_type = governanceType;
    if (governanceQuorumPct.trim() !== '')
      changes.governance_quorum_pct = parseFloat(governanceQuorumPct);

    const bonuses: Record<string, number> = {};
    for (const row of tradeBonusRows) {
      if (row.resource.trim() !== '' && row.bonus.trim() !== '') {
        bonuses[row.resource.trim()] = parseFloat(row.bonus);
      }
    }
    if (Object.keys(bonuses).length > 0) changes.trade_bonuses = bonuses;

    return changes;
  };

  const handleSubmit = async () => {
    setSubmitError(null);
    setFieldErrors(null);

    const effectiveType = policyType === 'other' ? policyTypeCustom.trim() : policyType;
    if (!effectiveType) {
      setSubmitError('Policy type is required.');
      return;
    }
    if (!title.trim()) {
      setSubmitError('Title is required.');
      return;
    }

    setSubmitting(true);
    try {
      const result = await governanceAPI.proposePolicy(regionId, {
        policy_type: effectiveType,
        title: title.trim(),
        description: description.trim() || undefined,
        proposed_changes: buildProposedChanges(),
        voting_duration_days: votingDurationDays.trim() ? parseInt(votingDurationDays, 10) : undefined,
      });
      setCreatedPolicyId(result?.policy_id ?? null);
      onCreated();
    } catch (err) {
      // NEVER let a rejected proposal crash the panel -- always land in an
      // inline, readable state.
      const maybeErrors = (err as { errors?: unknown })?.errors;
      if (Array.isArray(maybeErrors) && maybeErrors.every((e) => typeof e === 'string')) {
        setFieldErrors(maybeErrors as string[]);
      } else {
        setSubmitError(formatProposePolicyError(err));
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (createdPolicyId) {
    return (
      <div className="gov-propose-form gov-propose-success">
        <p className="gov-success-note">Policy proposal created — voting is now open.</p>
        <button type="button" className="gov-btn ghost" onClick={onCancel}>
          BACK TO POLICIES
        </button>
      </div>
    );
  }

  return (
    <div className="gov-propose-form">
      <h3>PROPOSE POLICY</h3>

      <div className="gov-form-row">
        <label>Policy type</label>
        <select value={policyType} onChange={(e) => setPolicyType(e.target.value)} disabled={submitting}>
          {POLICY_TYPE_SUGGESTIONS.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
          <option value="other">other…</option>
        </select>
        {policyType === 'other' && (
          <input
            type="text"
            placeholder="Custom policy type"
            value={policyTypeCustom}
            disabled={submitting}
            onChange={(e) => setPolicyTypeCustom(e.target.value)}
          />
        )}
      </div>

      <div className="gov-form-row">
        <label>Title</label>
        <input type="text" value={title} disabled={submitting} onChange={(e) => setTitle(e.target.value)} />
      </div>

      <div className="gov-form-row">
        <label>Description (optional)</label>
        <textarea value={description} disabled={submitting} onChange={(e) => setDescription(e.target.value)} />
      </div>

      <div className="gov-form-row">
        <label>Voting duration (days)</label>
        <input
          type="number"
          min={1}
          max={30}
          value={votingDurationDays}
          disabled={submitting}
          onChange={(e) => setVotingDurationDays(e.target.value)}
        />
      </div>

      <fieldset className="gov-changes-editor">
        <legend>PROPOSED CHANGES (leave blank to skip)</legend>

        <div className="gov-form-row">
          <label>Tax rate (5%–25%)</label>
          <input
            type="number"
            step="0.01"
            placeholder="e.g. 0.15"
            value={taxRate}
            disabled={submitting}
            onChange={(e) => setTaxRate(e.target.value)}
          />
        </div>

        <div className="gov-form-row">
          <label>Voting threshold (10%–90%)</label>
          <input
            type="number"
            step="0.01"
            placeholder="e.g. 0.51"
            value={votingThreshold}
            disabled={submitting}
            onChange={(e) => setVotingThreshold(e.target.value)}
          />
        </div>

        <div className="gov-form-row">
          <label>Election frequency (30–365 days)</label>
          <input
            type="number"
            placeholder="e.g. 90"
            value={electionFrequencyDays}
            disabled={submitting}
            onChange={(e) => setElectionFrequencyDays(e.target.value)}
          />
        </div>

        <div className="gov-form-row">
          <label>Governance type</label>
          <select value={governanceType} disabled={submitting} onChange={(e) => setGovernanceType(e.target.value)}>
            <option value="">(unchanged)</option>
            {GOVERNANCE_TYPES.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        </div>

        <div className="gov-form-row">
          <label>Governance quorum (25%–60%)</label>
          <input
            type="number"
            step="0.01"
            placeholder="e.g. 0.30"
            value={governanceQuorumPct}
            disabled={submitting}
            onChange={(e) => setGovernanceQuorumPct(e.target.value)}
          />
        </div>

        <div className="gov-trade-bonus-editor">
          <label>Trade bonuses (multiplier 1.0–3.0)</label>
          {tradeBonusRows.map((row, i) => (
            <div className="gov-trade-bonus-row" key={i}>
              <input
                type="text"
                placeholder="resource"
                value={row.resource}
                disabled={submitting}
                onChange={(e) => updateTradeBonusRow(i, { resource: e.target.value })}
              />
              <input
                type="number"
                step="0.01"
                placeholder="bonus"
                value={row.bonus}
                disabled={submitting}
                onChange={(e) => updateTradeBonusRow(i, { bonus: e.target.value })}
              />
              <button
                type="button"
                className="gov-btn ghost small"
                disabled={submitting}
                onClick={() => removeTradeBonusRow(i)}
              >
                REMOVE
              </button>
            </div>
          ))}
          <button
            type="button"
            className="gov-btn ghost small"
            disabled={submitting}
            onClick={addTradeBonusRow}
          >
            + ADD TRADE BONUS
          </button>
        </div>
      </fieldset>

      {submitError && <div className="gov-validation-strip">{submitError}</div>}
      {fieldErrors && (
        <ul className="gov-validation-strip gov-validation-list">
          {fieldErrors.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}

      <div className="gov-confirm-row">
        <button type="button" className="gov-btn primary" disabled={submitting} onClick={handleSubmit}>
          {submitting ? 'SUBMITTING…' : 'SUBMIT PROPOSAL'}
        </button>
        <button type="button" className="gov-btn ghost" disabled={submitting} onClick={onCancel}>
          CANCEL
        </button>
      </div>
    </div>
  );
};

export default ProposePolicyForm;

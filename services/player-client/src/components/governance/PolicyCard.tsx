import React, { useState } from 'react';
import { governanceAPI } from '../../services/api';
import type { Policy } from '../../types/governance';
import { POLICY_FIELD_RANGES } from '../../types/governance';

interface PolicyCardProps {
  policy: Policy;
  regionId: string;
  canVote: boolean;
  onChanged: () => void;
}

// ADR-0059 N-F5 finality copy -- static (ARIA narration is out of scope for
// this WO; see the matching TODO in ElectionCard).
const FINALITY_COPY = 'Your vote is recorded. Votes are final once cast.';

const VOTE_ERROR_COPY: Record<string, string> = {
  ERR_ALREADY_VOTED: 'You have already voted on this policy.',
  ERR_POLICY_NOT_VOTING: 'This policy is no longer open for voting.',
  ERR_VOTING_WINDOW_CLOSED: 'The voting window for this policy has closed.',
  ERR_NOT_A_MEMBER: 'You must be a member of this region to vote.',
  ERR_NOT_ELIGIBLE: 'You are not currently eligible to vote in this region.',
  ERR_ACCOUNT_TOO_NEW: 'Your account must be at least 60 days old to vote (anti-alt-ring rule).',
};

function formatChangeValue(key: string, value: unknown): string {
  if (key === 'trade_bonuses' && value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([resource, bonus]) => `${resource} ×${bonus}`)
      .join(', ');
  }
  const range = POLICY_FIELD_RANGES[key];
  if (range && typeof value === 'number' && key !== 'election_frequency_days') {
    return `${(value * 100).toFixed(1)}%`;
  }
  return String(value);
}

const PolicyCard: React.FC<PolicyCardProps> = ({ policy, regionId, canVote, onChanged }) => {
  const [selectedSupport, setSelectedSupport] = useState<boolean | null>(null);
  const [confirmArmed, setConfirmArmed] = useState(false);
  const [casting, setCasting] = useState(false);
  const [voteError, setVoteError] = useState<string | null>(null);
  // Same limitation as ElectionCard: no per-voter pre-check read exists.
  const [alreadyVoted, setAlreadyVoted] = useState(false);

  const totalVotes = policy.votes_for + policy.votes_against;
  const approvalPct =
    policy.approval_percentage ?? (totalVotes > 0 ? (policy.votes_for / totalVotes) * 100 : 0);

  const changeEntries = Object.entries(policy.proposed_changes || {});

  const handleVote = async () => {
    if (selectedSupport === null) return;
    setCasting(true);
    setVoteError(null);
    try {
      await governanceAPI.castPolicyVote(regionId, policy.id, selectedSupport);
      setAlreadyVoted(true);
      setConfirmArmed(false);
      onChanged();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to cast vote.';
      if (message.includes('ERR_ALREADY_VOTED')) setAlreadyVoted(true);
      setConfirmArmed(false);
      setVoteError(VOTE_ERROR_COPY[message] || message);
    } finally {
      setCasting(false);
    }
  };

  return (
    <div className={`gov-card gov-policy-card gov-status-${policy.status}`}>
      <div className="gov-card-header">
        <span className="gov-card-title">{policy.title}</span>
        <span className="gov-policy-type-badge">{policy.policy_type}</span>
        <span className={`gov-status-badge gov-status-${policy.status}`}>{policy.status}</span>
      </div>

      {policy.description && <p className="gov-policy-description">{policy.description}</p>}

      {changeEntries.length > 0 && (
        <div className="gov-changes-diff">
          <h4>PROPOSED CHANGES</h4>
          <ul>
            {changeEntries.map(([key, value]) => (
              <li key={key}>
                <span className="gov-change-key">{key.replace(/_/g, ' ')}</span>
                <span className="gov-change-arrow">→</span>
                <span className="gov-change-value">{formatChangeValue(key, value)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="gov-tally-bar" title={`${policy.votes_for} for / ${policy.votes_against} against`}>
        <div className="gov-tally-fill" style={{ width: `${Math.min(100, Math.max(0, approvalPct))}%` }} />
      </div>
      <p className="gov-tally-caption">
        {policy.votes_for.toLocaleString()} FOR · {policy.votes_against.toLocaleString()} AGAINST ·{' '}
        {approvalPct.toFixed(1)}% approval
      </p>

      {policy.status === 'voting' && (
        <>
          {!canVote && (
            <p className="gov-ineligible-note">You are not currently eligible to vote here.</p>
          )}
          {canVote && alreadyVoted && (
            <p className="gov-success-note">VOTE RECORDED — your ballot on this policy is final.</p>
          )}
          {canVote && !alreadyVoted && (
            <>
              {voteError && <div className="gov-validation-strip">{voteError}</div>}
              {!confirmArmed ? (
                <div className="gov-policy-vote-buttons">
                  <button
                    type="button"
                    className="gov-btn primary aye"
                    onClick={() => {
                      setSelectedSupport(true);
                      setConfirmArmed(true);
                    }}
                  >
                    AYE
                  </button>
                  <button
                    type="button"
                    className="gov-btn primary nay"
                    onClick={() => {
                      setSelectedSupport(false);
                      setConfirmArmed(true);
                    }}
                  >
                    NAY
                  </button>
                </div>
              ) : (
                <div className="gov-confirm-card">
                  <p className="gov-confirm-text">
                    Voting <strong>{selectedSupport ? 'AYE' : 'NAY'}</strong>. {FINALITY_COPY}
                  </p>
                  <div className="gov-confirm-row">
                    <button
                      type="button"
                      className="gov-btn primary commit"
                      disabled={casting}
                      onClick={handleVote}
                    >
                      {casting ? 'CASTING…' : 'CONFIRM VOTE'}
                    </button>
                    <button
                      type="button"
                      className="gov-btn ghost"
                      disabled={casting}
                      onClick={() => setConfirmArmed(false)}
                    >
                      CANCEL
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
};

export default PolicyCard;

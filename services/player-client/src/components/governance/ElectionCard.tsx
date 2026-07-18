import React, { useEffect, useMemo, useState } from 'react';
import { governanceAPI } from '../../services/api';
import type { Candidate, Election } from '../../types/governance';

interface ElectionCardProps {
  election: Election;
  regionId: string;
  currentPlayerId: string | null;
  /** Effective voter-roll eligibility (membership.can_vote). */
  canVote: boolean;
  /** membership_type === 'citizen' -- a coarse client-side hint for the
   *  self-nominate CTA; the server's reputation-floor check is authoritative
   *  (ERR_INSUFFICIENT_REPUTATION / ERR_NOT_A_CITIZEN render inline either way). */
  isCitizen: boolean;
  /** Called after a vote or candidacy mutation succeeds, so the parent can
   *  refetch the live election list. */
  onChanged: () => void;
}

const candidateId = (c: Candidate | string): string =>
  typeof c === 'string' ? c : c.player_id;

const candidatePlatform = (c: Candidate | string): string | undefined =>
  typeof c === 'string' ? undefined : c.platform;

// ADR-0059 N-F5 -- ARIA would narrate this at cast-time; the LLM gate is
// excluded from this WO, so the exact canon copy ships as static text.
// TODO(aria-narration): swap for an ARIA-voiced finality line once the
// dialogue gate covers governance actions.
const FINALITY_COPY = 'Your vote is recorded. Votes are final once cast.';

const VOTE_ERROR_COPY: Record<string, string> = {
  ERR_ALREADY_VOTED: 'You have already voted in this election.',
  ERR_ELECTION_NOT_ACTIVE: 'Voting is not currently open for this election.',
  ERR_VOTING_WINDOW_CLOSED: 'The voting window for this election has closed.',
  ERR_UNKNOWN_CANDIDATE: 'That candidate is not registered in this election.',
  ERR_NOT_A_MEMBER: 'You must be a member of this region to vote.',
  ERR_NOT_ELIGIBLE: 'You are not currently eligible to vote in this region.',
  ERR_ACCOUNT_TOO_NEW: 'Your account must be at least 60 days old to vote (anti-alt-ring rule).',
};

const CANDIDACY_ERROR_COPY: Record<string, string> = {
  ERR_CANDIDATES_LOCKED: 'Candidate registration has closed for this election.',
  ERR_NOT_A_CITIZEN: 'Only region citizens may stand as a candidate.',
  ERR_INSUFFICIENT_REPUTATION: 'Your regional reputation is below the candidacy threshold.',
  ERR_ALREADY_CANDIDATE: 'You are already registered as a candidate.',
  ERR_NOT_A_MEMBER: 'You must be a member of this region to stand as a candidate.',
};

function formatCountdown(targetIso: string, now: number): string {
  const target = new Date(targetIso).getTime();
  const diffMs = target - now;
  if (diffMs <= 0) return 'opening now';
  const totalMinutes = Math.floor(diffMs / 60000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `opens in ${days}d ${hours}h`;
  if (hours > 0) return `opens in ${hours}h ${minutes}m`;
  return `opens in ${minutes}m`;
}

const ElectionCard: React.FC<ElectionCardProps> = ({
  election,
  regionId,
  currentPlayerId,
  canVote,
  isCitizen,
  onChanged,
}) => {
  const [now, setNow] = useState(() => Date.now());
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const [confirmArmed, setConfirmArmed] = useState(false);
  const [casting, setCasting] = useState(false);
  const [voteError, setVoteError] = useState<string | null>(null);
  // No per-voter read exists on the election object (results only appear
  // once COMPLETED) -- this flips true on a successful cast OR on catching
  // ERR_ALREADY_VOTED, and resets on remount. A page reload before either
  // happens will re-show the ballot; a repeat attempt still fails cleanly
  // server-side. [NO-CANON] flagged in the WO report as a follow-up gap.
  const [alreadyVoted, setAlreadyVoted] = useState(false);

  const [platformInput, setPlatformInput] = useState('');
  const [nominating, setNominating] = useState(false);
  const [nominateError, setNominateError] = useState<string | null>(null);
  const [justRegistered, setJustRegistered] = useState(false);

  useEffect(() => {
    if (election.status !== 'pending') return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(id);
  }, [election.status]);

  const isAlreadyCandidate = useMemo(() => {
    if (justRegistered) return true;
    if (!currentPlayerId) return false;
    return (election.candidates || []).some((c) => candidateId(c) === currentPlayerId);
  }, [election.candidates, currentPlayerId, justRegistered]);

  const handleVoteClick = async () => {
    if (!selectedCandidateId) return;
    setCasting(true);
    setVoteError(null);
    try {
      await governanceAPI.castElectionVote(regionId, election.id, selectedCandidateId);
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

  const handleNominate = async () => {
    setNominating(true);
    setNominateError(null);
    try {
      await governanceAPI.registerCandidacy(regionId, election.id, platformInput.trim() || undefined);
      setJustRegistered(true);
      onChanged();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to register candidacy.';
      if (message.includes('ERR_ALREADY_CANDIDATE')) setJustRegistered(true);
      setNominateError(CANDIDACY_ERROR_COPY[message] || message);
    } finally {
      setNominating(false);
    }
  };

  return (
    <div className={`gov-card gov-election-card gov-status-${election.status}`}>
      <div className="gov-card-header">
        <span className="gov-card-title">{election.position.replace(/_/g, ' ').toUpperCase()}</span>
        <span className={`gov-status-badge gov-status-${election.status}`}>{election.status}</span>
      </div>

      {election.status === 'pending' && (
        <div className="gov-election-pending">
          <p className="gov-countdown">{formatCountdown(election.voting_opens_at, now)}</p>

          <ul className="gov-candidate-preview-list">
            {(election.candidates || []).length === 0 && (
              <li className="gov-muted">No candidates registered yet.</li>
            )}
            {(election.candidates || []).map((c) => (
              <li key={candidateId(c)}>
                {candidateId(c) === currentPlayerId ? <strong>YOU</strong> : candidateId(c).slice(0, 8)}
                {candidatePlatform(c) ? ` — ${candidatePlatform(c)}` : ''}
              </li>
            ))}
          </ul>

          {!isAlreadyCandidate && isCitizen && (
            <div className="gov-nominate-form">
              <input
                type="text"
                className="gov-platform-input"
                placeholder="Optional platform statement (max 500 chars)"
                maxLength={500}
                value={platformInput}
                disabled={nominating}
                onChange={(e) => setPlatformInput(e.target.value)}
              />
              <button
                type="button"
                className="gov-btn primary"
                disabled={nominating}
                onClick={handleNominate}
              >
                {nominating ? 'REGISTERING…' : 'SELF-NOMINATE'}
              </button>
            </div>
          )}
          {isAlreadyCandidate && (
            <p className="gov-success-note">You are registered as a candidate.</p>
          )}
          {nominateError && <div className="gov-validation-strip">{nominateError}</div>}
        </div>
      )}

      {election.status === 'active' && (
        <div className="gov-election-active">
          {!canVote && (
            <p className="gov-ineligible-note">You are not currently eligible to vote here.</p>
          )}
          {canVote && alreadyVoted && (
            <p className="gov-success-note">VOTE RECORDED — your ballot for this election is final.</p>
          )}
          {canVote && !alreadyVoted && (
            <>
              <ul className="gov-ballot-list">
                {(election.candidates || []).map((c) => (
                  <li key={candidateId(c)}>
                    <label className="gov-ballot-option">
                      <input
                        type="radio"
                        name={`election-${election.id}`}
                        checked={selectedCandidateId === candidateId(c)}
                        disabled={casting || confirmArmed}
                        onChange={() => setSelectedCandidateId(candidateId(c))}
                      />
                      <span>
                        {candidateId(c) === currentPlayerId ? <strong>YOU</strong> : candidateId(c).slice(0, 8)}
                        {candidatePlatform(c) ? ` — ${candidatePlatform(c)}` : ''}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>

              {voteError && <div className="gov-validation-strip">{voteError}</div>}

              {!confirmArmed ? (
                <button
                  type="button"
                  className="gov-btn primary"
                  disabled={!selectedCandidateId}
                  onClick={() => setConfirmArmed(true)}
                >
                  CAST VOTE
                </button>
              ) : (
                <div className="gov-confirm-card">
                  <p className="gov-confirm-text">{FINALITY_COPY}</p>
                  <div className="gov-confirm-row">
                    <button
                      type="button"
                      className="gov-btn primary commit"
                      disabled={casting}
                      onClick={handleVoteClick}
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
        </div>
      )}

      {election.status === 'completed' && (
        <div className="gov-election-results">
          {!election.results && <p className="gov-muted">Results pending.</p>}
          {election.results && election.results.inconclusive && (
            <p className="gov-ineligible-note">INCONCLUSIVE — no votes were cast.</p>
          )}
          {election.results && !election.results.inconclusive && election.results.voided && (
            <p className="gov-ineligible-note">
              VOIDED — no candidate cleared the required supermajority.
            </p>
          )}
          {election.results && (
            <ul className="gov-results-list">
              {Object.entries(election.results.tallies)
                .sort(([, a], [, b]) => b - a)
                .map(([cid, weight]) => (
                  <li
                    key={cid}
                    className={cid === election.results?.winner ? 'gov-result-winner' : ''}
                  >
                    <span>
                      {cid === currentPlayerId ? <strong>YOU</strong> : cid.slice(0, 8)}
                      {cid === election.results?.winner ? ' 🏆' : ''}
                    </span>
                    <span>{weight.toLocaleString()}</span>
                  </li>
                ))}
            </ul>
          )}
        </div>
      )}

      {election.status === 'cancelled' && (
        <p className="gov-muted">This election was cancelled.</p>
      )}
    </div>
  );
};

export default ElectionCard;

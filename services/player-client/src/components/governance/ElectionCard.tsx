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

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Surface GS cast-vote detail (LEG-2938 Soft-ORDER). */
export function formatElectionVoteError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim());

  if (message && VOTE_ERROR_COPY[message]) return VOTE_ERROR_COPY[message];

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You are not allowed to vote in this election.';
  }

  if (status === 429) {
    return 'Vote rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to cast vote.';
}

/** Surface GS register-candidacy detail (LEG-2938 Soft-ORDER). */
export function formatElectionCandidacyError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim());

  if (message && CANDIDACY_ERROR_COPY[message]) return CANDIDACY_ERROR_COPY[message];

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You are not allowed to register as a candidate.';
  }

  if (status === 429) {
    return 'Candidacy rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to register candidacy.';
}

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
  // WO-WIRE-ELECTION-RESULTS-API — list payloads sometimes omit results;
  // fetch GET …/results when COMPLETED and the embed is empty.
  const [fetchedResults, setFetchedResults] = useState<Election['results']>(null);
  const [resultsLoading, setResultsLoading] = useState(false);

  useEffect(() => {
    if (election.status !== 'pending') return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(id);
  }, [election.status]);

  useEffect(() => {
    if (election.status !== 'completed') {
      setFetchedResults(null);
      return undefined;
    }
    if (election.results) {
      setFetchedResults(null);
      return undefined;
    }
    let cancelled = false;
    setResultsLoading(true);
    governanceAPI
      .getElectionResults(regionId, election.id)
      .then((data: { results?: Election['results'] }) => {
        if (!cancelled) setFetchedResults(data?.results ?? null);
      })
      .catch(() => {
        if (!cancelled) setFetchedResults(null);
      })
      .finally(() => {
        if (!cancelled) setResultsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [election.status, election.id, election.results, regionId]);

  const results = election.results ?? fetchedResults;
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
      const message = err instanceof Error ? err.message : '';
      if (message.includes('ERR_ALREADY_VOTED')) setAlreadyVoted(true);
      setConfirmArmed(false);
      setVoteError(formatElectionVoteError(err));
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
      const message = err instanceof Error ? err.message : '';
      if (message.includes('ERR_ALREADY_CANDIDATE')) setJustRegistered(true);
      setNominateError(formatElectionCandidacyError(err));
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
        <div className="gov-election-results" data-testid="gov-election-results">
          {resultsLoading && <p className="gov-muted">Loading results…</p>}
          {!resultsLoading && !results && <p className="gov-muted">Results pending.</p>}
          {results && results.inconclusive && (
            <p className="gov-ineligible-note">INCONCLUSIVE — no votes were cast.</p>
          )}
          {results && !results.inconclusive && results.voided && (
            <p className="gov-ineligible-note">
              VOIDED — no candidate cleared the required supermajority.
            </p>
          )}
          {results && (
            <ul className="gov-results-list">
              {Object.entries(results.tallies)
                .sort(([, a], [, b]) => b - a)
                .map(([cid, weight]) => (
                  <li
                    key={cid}
                    className={cid === results.winner ? 'gov-result-winner' : ''}
                  >
                    <span>
                      {cid === currentPlayerId ? <strong>YOU</strong> : cid.slice(0, 8)}
                      {cid === results.winner ? ' 🏆' : ''}
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

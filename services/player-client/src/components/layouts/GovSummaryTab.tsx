import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useGame } from '../../contexts/GameContext';
import { governanceAPI } from '../../services/api';
import type { Election, MembershipStatus } from '../../types/governance';
import EmptyState from '../common/EmptyState';

/**
 * GovSummaryTab — the StatusBar dossier dropdown's "GOVERNANCE" tab.
 *
 * GovernancePanel.tsx (components/governance/) is the full REGIONAL
 * GOVERNANCE console (elections/policies/treaties tabs, vote/propose
 * actions) — orphaned the same way TeamManager.tsx is (no route mounts
 * either post-UI5-retirement), and just as heavy to embed in this
 * fixed-size dropdown. Same "pull the personal-standing summary, not the
 * whole page" pattern TeamSummaryTab.tsx used for TeamManager: this shows
 * only the player's own citizenship status + next election at a glance,
 * reusing the SAME wire shapes GovernancePanel.tsx already established
 * (governanceAPI.getMyMembership / listElections, snake_case field names
 * per types/governance.ts's member-facing convention), and links out to
 * /game/governance for anything that mutates state (vote, propose,
 * register candidacy) — a dead-end today, matching TeamSummaryTab's
 * /game/team link, since neither destination is routed.
 */

const formatMembershipType = (type: string | null): string => {
  if (!type) return '—';
  return type.charAt(0).toUpperCase() + type.slice(1);
};

const nextElection = (elections: Election[]): Election | null => {
  const pendingOrActive = elections.filter(
    (e) => e.status === 'pending' || e.status === 'active'
  );
  if (pendingOrActive.length === 0) return null;
  return pendingOrActive.reduce((soonest, e) =>
    new Date(e.voting_opens_at).getTime() < new Date(soonest.voting_opens_at).getTime() ? e : soonest
  );
};

const GovSummaryTab: React.FC = () => {
  const { currentSector } = useGame();
  const regionId = currentSector?.region_id ?? null;

  const [membership, setMembership] = useState<MembershipStatus | null>(null);
  const [elections, setElections] = useState<Election[]>([]);
  const [loading, setLoading] = useState(!!regionId);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!regionId) {
      setMembership(null);
      setElections([]);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    (governanceAPI.getMyMembership(regionId) as Promise<MembershipStatus>)
      .then((membershipData) => {
        if (cancelled) return;
        setMembership(membershipData);
        if (!membershipData.is_member) {
          setElections([]);
          setLoading(false);
          return;
        }
        return (governanceAPI.listElections(regionId) as Promise<Election[]>)
          .then((electionData) => {
            if (cancelled) return;
            setElections(electionData || []);
          })
          .finally(() => {
            if (!cancelled) setLoading(false);
          });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Failed to load governance data');
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [regionId]);

  if (error) {
    return <div className="sb-gov-error" role="alert">{error}</div>;
  }

  if (loading) {
    return (
      <div className="sb-gov-loading" role="status" aria-live="polite">
        Loading…
      </div>
    );
  }

  if (!regionId || !membership) {
    return (
      <EmptyState
        icon="🏛️"
        title="No Region"
        message="You are not currently in a governed region."
      />
    );
  }

  if (!membership.is_member) {
    return (
      <EmptyState
        icon="🏛️"
        title="Not Yet a Citizen"
        message="You are not currently a member of this region's voter roll."
      >
        <Link to="/game/governance" className="sb-gov-link">
          View Regional Governance →
        </Link>
      </EmptyState>
    );
  }

  const upcoming = nextElection(elections);

  return (
    <div className="sb-gov-summary">
      <h2 className="sb-gov-name">Regional Governance</h2>
      <div className="sb-gov-grid">
        <div className="sb-identity-field">
          <span className="sb-identity-k">STANDING</span>
          <span className="sb-identity-v">{formatMembershipType(membership.membership_type)}</span>
        </div>
        <div className="sb-identity-field">
          <span className="sb-identity-k">CAN VOTE</span>
          <span className="sb-identity-v">{membership.can_vote ? 'Yes' : 'No'}</span>
        </div>
        <div className="sb-identity-field">
          <span className="sb-identity-k">NEXT ELECTION</span>
          <span className="sb-identity-v">
            {upcoming ? upcoming.position : 'None scheduled'}
          </span>
        </div>
      </div>
      <Link to="/game/governance" className="sb-gov-link">
        Manage Governance →
      </Link>
    </div>
  );
};

export default GovSummaryTab;

import React, { useCallback, useEffect, useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { governanceAPI } from '../../services/api';
import type { Election, MembershipStatus, Policy, Treaty } from '../../types/governance';
import GameLayout from '../layouts/GameLayout';
import CockpitInstrument from '../cockpit/CockpitInstrument';
import EmptyState from '../common/EmptyState';
import LoadingState from '../common/LoadingState';
import ElectionCard from './ElectionCard';
import PolicyCard from './PolicyCard';
import ProposePolicyForm from './ProposePolicyForm';
import './governance-panel.css';

/* REGIONAL GOVERNANCE console shell (Law 3) -- module-level so the monitor
   frame keeps its identity across loading/error/empty/tab states and never
   remounts mid-session (mirrors TeamManager's CrewShell). */
const GovShell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <GameLayout>
    <CockpitInstrument title="REGIONAL GOVERNANCE" accent="#00D9FF" subtitle="CIVIC OPERATIONS">
      {children}
    </CockpitInstrument>
  </GameLayout>
);

type GovTab = 'elections' | 'policies' | 'treaties';

const GovernancePanel: React.FC = () => {
  const { playerState, currentSector } = useGame();
  const regionId = currentSector?.region_id ?? null;
  const currentPlayerId = playerState?.id ?? null;

  const [membership, setMembership] = useState<MembershipStatus | null>(null);
  const [elections, setElections] = useState<Election[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [treaties, setTreaties] = useState<Treaty[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [noGovernanceSurface, setNoGovernanceSurface] = useState(false);

  const [activeTab, setActiveTab] = useState<GovTab>('elections');
  const [showProposeForm, setShowProposeForm] = useState(false);

  const load = useCallback(async () => {
    if (!regionId) {
      setMembership(null);
      setElections([]);
      setPolicies([]);
      setTreaties([]);
      setLoadError(null);
      setNoGovernanceSurface(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError(null);
    try {
      // Membership is fetched independently first: some regions (e.g.
      // Central Nexus) may have no governance surface at all, and this read
      // is the cheapest probe for that (mirrors CitizenshipBadge's
      // "stay quiet on failure" idiom, but a full page needs an honest
      // empty state rather than silence).
      const membershipData = (await governanceAPI.getMyMembership(regionId)) as MembershipStatus;
      setMembership(membershipData);
      setNoGovernanceSurface(false);

      if (!membershipData.is_member) {
        // Non-member: the list endpoints would 403 ERR_NOT_A_MEMBER anyway —
        // skip them and render the non-citizen state directly.
        setElections([]);
        setPolicies([]);
        setTreaties([]);
        setLoading(false);
        return;
      }

      const [electionData, policyData, treatyData] = await Promise.all([
        governanceAPI.listElections(regionId) as Promise<Election[]>,
        governanceAPI.listPolicies(regionId) as Promise<Policy[]>,
        governanceAPI.listTreaties(regionId) as Promise<Treaty[]>,
      ]);
      setElections(electionData || []);
      setPolicies(policyData || []);
      setTreaties(treatyData || []);
    } catch (error) {
      console.error('Failed to load regional governance data:', error);
      setNoGovernanceSurface(true);
      setMembership(null);
    } finally {
      setLoading(false);
    }
  }, [regionId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <GovShell>
        <LoadingState message="Loading regional governance…" />
      </GovShell>
    );
  }

  if (!regionId) {
    return (
      <GovShell>
        <EmptyState
          icon="🏛️"
          title="No Region"
          message="You are not currently in a governed region. Fly into a player-owned or central region to access its governance panel."
        />
      </GovShell>
    );
  }

  if (noGovernanceSurface) {
    return (
      <GovShell>
        <EmptyState
          icon="🏛️"
          title="Governance Unavailable"
          message="This region has no governance surface to display right now."
        >
          <button type="button" className="gov-btn ghost" onClick={() => void load()}>
            RETRY
          </button>
        </EmptyState>
      </GovShell>
    );
  }

  if (loadError) {
    return (
      <GovShell>
        <EmptyState icon="⚠️" title="Load Failed" message={loadError}>
          <button type="button" className="gov-btn ghost" onClick={() => void load()}>
            RETRY
          </button>
        </EmptyState>
      </GovShell>
    );
  }

  if (membership && !membership.is_member) {
    return (
      <GovShell>
        <div className="gov-panel">
          <EmptyState
            icon="🏛️"
            title="Not Yet a Citizen"
            message="You are not currently a member of this region's voter roll. Own a colony in this region, or gain resident/citizen standing, to participate in its governance."
          />
        </div>
      </GovShell>
    );
  }

  return (
    <GovShell>
      <div className="gov-panel">
        <div className="gov-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={activeTab === 'elections'}
            className={activeTab === 'elections' ? 'active' : ''}
            onClick={() => setActiveTab('elections')}
          >
            Elections
          </button>
          <button
            role="tab"
            aria-selected={activeTab === 'policies'}
            className={activeTab === 'policies' ? 'active' : ''}
            onClick={() => setActiveTab('policies')}
          >
            Policies
          </button>
          <button
            role="tab"
            aria-selected={activeTab === 'treaties'}
            className={activeTab === 'treaties' ? 'active' : ''}
            onClick={() => setActiveTab('treaties')}
          >
            Treaties
          </button>
        </div>

        <div className="gov-tab-content">
          {activeTab === 'elections' && (
            <>
              {elections.length === 0 ? (
                <EmptyState
                  icon="🗳️"
                  title="No Elections"
                  message="No elections are scheduled in this region right now."
                />
              ) : (
                <div className="gov-card-list">
                  {elections.map((election) => (
                    <ElectionCard
                      key={election.id}
                      election={election}
                      regionId={regionId}
                      currentPlayerId={currentPlayerId}
                      canVote={!!membership?.can_vote}
                      isCitizen={membership?.membership_type === 'citizen'}
                      onChanged={() => void load()}
                    />
                  ))}
                </div>
              )}
            </>
          )}

          {activeTab === 'policies' && (
            <>
              {membership?.can_vote && !showProposeForm && (
                <button type="button" className="gov-btn primary" onClick={() => setShowProposeForm(true)}>
                  + PROPOSE POLICY
                </button>
              )}
              {showProposeForm && (
                <ProposePolicyForm
                  regionId={regionId}
                  onCreated={() => {
                    setShowProposeForm(false);
                    void load();
                  }}
                  onCancel={() => setShowProposeForm(false)}
                />
              )}
              {!showProposeForm && policies.length === 0 && (
                <EmptyState
                  icon="📜"
                  title="No Policies"
                  message="No active policy proposals in this region right now."
                />
              )}
              {!showProposeForm && policies.length > 0 && (
                <div className="gov-card-list">
                  {policies.map((policy) => (
                    <PolicyCard
                      key={policy.id}
                      policy={policy}
                      regionId={regionId}
                      canVote={!!membership?.can_vote}
                      onChanged={() => void load()}
                    />
                  ))}
                </div>
              )}
            </>
          )}

          {activeTab === 'treaties' && (
            <>
              {treaties.length === 0 ? (
                <EmptyState
                  icon="🤝"
                  title="No Treaties"
                  message="No treaties are on record for this region."
                />
              ) : (
                <ul className="gov-treaty-list">
                  {treaties.map((treaty) => (
                    <li key={treaty.id} className={`gov-treaty-row gov-status-${treaty.status}`}>
                      <span className="gov-treaty-type">{treaty.treaty_type}</span>
                      <span className="gov-treaty-partner">{treaty.partner_region || 'Unknown'}</span>
                      <span className={`gov-status-badge gov-status-${treaty.status}`}>{treaty.status}</span>
                      <span className="gov-treaty-expiry">
                        {treaty.expires_at ? `expires ${new Date(treaty.expires_at).toLocaleDateString()}` : 'no expiry'}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </div>
    </GovShell>
  );
};

export default GovernancePanel;

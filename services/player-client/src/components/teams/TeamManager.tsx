import React, { useCallback, useEffect, useState } from 'react';
import { teamAPI } from '../../services/api';
import { useGame } from '../../contexts/GameContext';
import type {
  Team,
  TeamMember,
  TeamPermissions,
  TeamApiResponse,
  TeamMemberApiResponse,
  TeamPermissionsApiResponse
} from '../../types/team';
import GameLayout from '../layouts/GameLayout';
import CockpitInstrument from '../cockpit/CockpitInstrument';
import EmptyState from '../common/EmptyState';
import LoadingState from '../common/LoadingState';
import { ResourceSharing } from './ResourceSharing';
import { TeamChat } from './TeamChat';
import './team-manager.css';

/* CREW MANIFEST console shell (Law 3) — module-level so the monitor frame
   keeps its identity across loading/error/no-team/team branches and never
   remounts mid-session. */
const CrewShell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <GameLayout>
    <CockpitInstrument title="CREW MANIFEST" accent="#00FF7F" subtitle="TEAM OPERATIONS">
      {children}
    </CockpitInstrument>
  </GameLayout>
);

// --- Wire mappers ----------------------------------------------------------
// The gameserver speaks snake_case (teams.py response models); the UI types
// are camelCase. Translate at the boundary so render code stays typed.

const RECRUITMENT_TO_UI: Record<string, Team['recruitmentStatus']> = {
  OPEN: 'open',
  INVITE_ONLY: 'invite-only',
  CLOSED: 'closed'
};

const RECRUITMENT_TO_API: Record<Team['recruitmentStatus'], string> = {
  'open': 'OPEN',
  'invite-only': 'INVITE_ONLY',
  'closed': 'CLOSED'
};

const mapTeam = (raw: TeamApiResponse): Team => ({
  id: raw.id,
  name: raw.name,
  tag: raw.tag ?? '',
  description: raw.description ?? '',
  leaderId: raw.leader_id,
  memberCount: raw.member_count,
  maxMembers: raw.max_members,
  founded: raw.created_at,
  recruitmentStatus: RECRUITMENT_TO_UI[raw.recruitment_status] ?? 'closed',
  combatRating: raw.combat_rating,
  tradeRating: raw.trade_rating,
  totalPlanets: raw.total_planets,
  treasury: {
    // Canon gap: TeamResponse only exposes treasury_credits; the other
    // treasury columns exist on the Team model but are not in the contract.
    credits: raw.treasury_credits
  }
});

const mapRole = (role: string): TeamMember['role'] => {
  switch (role) {
    case 'LEADER':
      return 'leader';
    case 'OFFICER':
      return 'officer';
    case 'RECRUIT':
      return 'recruit';
    default:
      return 'member';
  }
};

const mapMember = (raw: TeamMemberApiResponse): TeamMember => {
  const contributions = raw.contribution_credits ?? {};
  const credits = typeof contributions.credits === 'number' ? contributions.credits : 0;
  const resources = Object.entries(contributions)
    .filter(([key, value]) => key !== 'credits' && typeof value === 'number')
    .reduce((sum, [, value]) => sum + value, 0);

  return {
    id: raw.player_id,
    playerId: raw.player_id,
    playerName: raw.nickname,
    role: mapRole(raw.role),
    joinedAt: raw.joined_at,
    contributions: {
      credits,
      resources,
      // Canon gap: per-member kill tracking is not exposed by the teams API
      combatKills: 0
    },
    // Canon gap: the teams API has no presence signal (only last_active);
    // never claim a member is online without real telemetry
    online: false,
    location: {
      sectorId: raw.current_sector !== null ? String(raw.current_sector) : '',
      sectorName: raw.current_sector !== null ? `Sector ${raw.current_sector}` : 'Unknown'
    },
    // Canon gap: member ship type is not exposed by the teams API
    shipType: '',
    combatRating: raw.combat_rating
  };
};

const mapPermissions = (raw: TeamPermissionsApiResponse): TeamPermissions => ({
  canInvite: raw.can_invite,
  canKick: raw.can_kick,
  // Only the team leader may change member roles (team_service.update_member_role)
  canPromote: raw.role === 'LEADER',
  canManageTreasury: raw.can_manage_treasury,
  canStartMissions: raw.can_manage_missions,
  // update_team is leader/officer only (team_service.update_team)
  canEditTeamInfo: raw.role === 'LEADER' || raw.role === 'OFFICER',
  canManageAlliances: raw.can_manage_alliances,
  canDeclareWar: raw.can_manage_alliances
});

interface CreateTeamForm {
  name: string;
  tag: string;
  description: string;
  recruitmentStatus: Team['recruitmentStatus'];
  maxMembers: number;
}

const EMPTY_CREATE_FORM: CreateTeamForm = {
  name: '',
  tag: '',
  description: '',
  recruitmentStatus: 'open',
  maxMembers: 4
};

export const TeamManager: React.FC = () => {
  const { playerState, refreshPlayerState } = useGame();
  const teamId = playerState?.team_id ?? null;

  const [team, setTeam] = useState<Team | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [permissions, setPermissions] = useState<TeamPermissions | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'overview' | 'members' | 'treasury' | 'chat' | 'settings'>('overview');
  const [editingInfo, setEditingInfo] = useState(false);
  const [teamInfo, setTeamInfo] = useState<{ description: string; recruitmentStatus: Team['recruitmentStatus'] }>({
    description: '',
    recruitmentStatus: 'open'
  });
  const [saveError, setSaveError] = useState<string | null>(null);
  const [leaveError, setLeaveError] = useState<string | null>(null);
  const [memberActionError, setMemberActionError] = useState<string | null>(null);

  // Create-team modal state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createForm, setCreateForm] = useState<CreateTeamForm>(EMPTY_CREATE_FORM);
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // Two-step inline confirmations (no native dialogs)
  const [confirmingLeave, setConfirmingLeave] = useState(false);
  const [confirmingKickId, setConfirmingKickId] = useState<string | null>(null);

  const loadTeamData = useCallback(async (id: string | null) => {
    if (!id) {
      // Player has no team: render the empty state without firing requests
      setTeam(null);
      setMembers([]);
      setPermissions(null);
      setLoadError(null);
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      setLoadError(null);
      const [teamData, memberData, permData] = await Promise.all([
        teamAPI.getTeam(id) as Promise<TeamApiResponse>,
        teamAPI.getMembers(id) as Promise<TeamMemberApiResponse[]>,
        teamAPI.getPermissions(id) as Promise<TeamPermissionsApiResponse>
      ]);
      const mappedTeam = mapTeam(teamData);
      setTeam(mappedTeam);
      setMembers(memberData.map(mapMember));
      setPermissions(mapPermissions(permData));
      setTeamInfo({
        description: mappedTeam.description,
        recruitmentStatus: mappedTeam.recruitmentStatus
      });
    } catch (error) {
      console.error('Failed to load team data:', error);
      setLoadError(error instanceof Error ? error.message : 'Failed to load team data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTeamData(teamId);
  }, [teamId, loadTeamData]);

  const handleSaveTeamInfo = async () => {
    if (!team || !permissions?.canEditTeamInfo) return;

    try {
      setSaveError(null);
      // Backend UpdateTeamRequest has no `name` field — renaming a team is
      // not supported by the API today, so only the editable fields are sent.
      const updatedRaw = await teamAPI.updateTeam(team.id, {
        description: teamInfo.description,
        recruitment_status: RECRUITMENT_TO_API[teamInfo.recruitmentStatus]
      }) as TeamApiResponse;
      setTeam(mapTeam(updatedRaw));
      setEditingInfo(false);
    } catch (error) {
      console.error('Failed to update team info:', error);
      setSaveError(error instanceof Error ? error.message : 'Failed to update team info');
    }
  };

  const handlePromoteMember = async (memberId: string, newRole: 'OFFICER' | 'MEMBER') => {
    if (!team || !permissions?.canPromote) return;

    try {
      setMemberActionError(null);
      const updatedRaw = await teamAPI.promoteMember(team.id, memberId, newRole) as TeamMemberApiResponse;
      const updated = mapMember(updatedRaw);
      setMembers(members.map(m => m.id === memberId ? updated : m));
    } catch (error) {
      console.error('Failed to update member role:', error);
      setConfirmingKickId(null);
      setMemberActionError(error instanceof Error ? error.message : 'Failed to update member role');
    }
  };

  const handleKickMember = async (memberId: string) => {
    if (!team || !permissions?.canKick) return;

    // First click arms the confirmation; second click executes
    if (confirmingKickId !== memberId) {
      setConfirmingKickId(memberId);
      return;
    }
    setConfirmingKickId(null);
    try {
      setMemberActionError(null);
      await teamAPI.kickMember(team.id, memberId);
      setMembers(members.filter(m => m.id !== memberId));
      setTeam(prev => prev ? { ...prev, memberCount: prev.memberCount - 1 } : prev);
    } catch (error) {
      console.error('Failed to kick member:', error);
      setMemberActionError(error instanceof Error ? error.message : 'Failed to kick member');
    }
  };

  const handleLeaveTeam = async () => {
    // First click arms the confirmation; second click executes
    if (!confirmingLeave) {
      setConfirmingLeave(true);
      return;
    }
    setConfirmingLeave(false);
    try {
      setLeaveError(null);
      await teamAPI.leaveTeam();
      // Refresh clears playerState.team_id; the team_id effect resets local state
      await refreshPlayerState();
    } catch (error) {
      console.error('Failed to leave team:', error);
      setLeaveError(error instanceof Error ? error.message : 'Failed to leave team');
    }
  };

  const validateCreateForm = (): string | null => {
    const name = createForm.name.trim();
    const tag = createForm.tag.trim();
    if (name.length < 3 || name.length > 80) return 'Team name must be 3-80 characters.';
    if (tag.length > 0 && (tag.length < 2 || tag.length > 10)) return 'Team tag must be 2-10 characters, or left blank.';
    if (createForm.description.length > 500) return 'Description must be 500 characters or fewer.';
    if (!Number.isInteger(createForm.maxMembers) || createForm.maxMembers < 2 || createForm.maxMembers > 20) {
      return 'Max members must be between 2 and 20.';
    }
    return null;
  };

  const handleCreateTeam = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const validationError = validateCreateForm();
    if (validationError) {
      setCreateError(validationError);
      return;
    }

    try {
      setCreating(true);
      setCreateError(null);
      const tag = createForm.tag.trim();
      const description = createForm.description.trim();
      const createdRaw = await teamAPI.createTeam({
        name: createForm.name.trim(),
        ...(tag ? { tag } : {}),
        ...(description ? { description } : {}),
        max_members: createForm.maxMembers,
        recruitment_status: RECRUITMENT_TO_API[createForm.recruitmentStatus]
      }) as TeamApiResponse;
      setShowCreateModal(false);
      setCreateForm(EMPTY_CREATE_FORM);

      // Primary path: the create response is authoritative. Render the new
      // team from it immediately so a failed state refresh can't strand a
      // charged player on the "No Team" screen.
      const created = mapTeam(createdRaw);
      setTeam(created);
      setTeamInfo({
        description: created.description,
        recruitmentStatus: created.recruitmentStatus
      });
      try {
        const [memberData, permData] = await Promise.all([
          teamAPI.getMembers(created.id) as Promise<TeamMemberApiResponse[]>,
          teamAPI.getPermissions(created.id) as Promise<TeamPermissionsApiResponse>
        ]);
        setMembers(memberData.map(mapMember));
        setPermissions(mapPermissions(permData));
      } catch (detailError) {
        console.error('Failed to load new team members/permissions:', detailError);
      }

      // Best-effort sync: picks up the new team_id (and the creation charge);
      // the team_id effect then reloads the full team view.
      try {
        await refreshPlayerState();
      } catch (refreshError) {
        console.error('Failed to refresh player state after team creation:', refreshError);
      }
    } catch (error) {
      // Surface backend 400s honestly: duplicate name, insufficient credits
      // for the 10,000-credit creation cost, or already in a team
      setCreateError(error instanceof Error ? error.message : 'Failed to create team');
    } finally {
      setCreating(false);
    }
  };

  const renderCreateModal = () => {
    if (!showCreateModal) return null;

    return (
      <div className="team-modal-overlay" onClick={() => setShowCreateModal(false)}>
        <div className="team-modal" onClick={(e) => e.stopPropagation()}>
          <h3>Found a New Team</h3>
          {/* 10,000 credits is the backend TEAM_CREATION_COST (team_service.py) */}
          <p className="team-modal-cost">Registration fee: 10,000 credits</p>
          <form className="create-team-form" onSubmit={handleCreateTeam}>
            <div className="form-group">
              <label htmlFor="create-team-name">Team Name</label>
              <input
                id="create-team-name"
                type="text"
                value={createForm.name}
                minLength={3}
                maxLength={80}
                required
                onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
              />
            </div>

            <div className="form-group">
              <label htmlFor="create-team-tag">Tag (optional, 2-10 characters)</label>
              <input
                id="create-team-tag"
                type="text"
                value={createForm.tag}
                maxLength={10}
                onChange={(e) => setCreateForm({ ...createForm, tag: e.target.value })}
              />
            </div>

            <div className="form-group">
              <label htmlFor="create-team-description">Description (optional)</label>
              <textarea
                id="create-team-description"
                value={createForm.description}
                maxLength={500}
                rows={4}
                onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })}
              />
            </div>

            <div className="form-group">
              <label htmlFor="create-team-max-members">Max Members (2-20)</label>
              <input
                id="create-team-max-members"
                type="number"
                min={2}
                max={20}
                value={createForm.maxMembers}
                required
                onChange={(e) => setCreateForm({ ...createForm, maxMembers: e.target.valueAsNumber })}
              />
            </div>

            <div className="form-group">
              <label htmlFor="create-team-recruitment">Recruitment</label>
              <select
                id="create-team-recruitment"
                value={createForm.recruitmentStatus}
                onChange={(e) => setCreateForm({
                  ...createForm,
                  recruitmentStatus: e.target.value as Team['recruitmentStatus']
                })}
              >
                <option value="open">Open</option>
                <option value="invite-only">Invite Only</option>
                <option value="closed">Closed</option>
              </select>
            </div>

            {createError && <div className="form-error" role="alert">{createError}</div>}

            <div className="form-actions">
              <button type="submit" disabled={creating}>
                {creating ? 'Registering…' : 'Create Team'}
              </button>
              <button type="button" className="cancel-btn" onClick={() => setShowCreateModal(false)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      </div>
    );
  };

  if (loading || !playerState) {
    return (
      <CrewShell>
        <div className="team-manager loading">
          <LoadingState message="Loading team data..." />
        </div>
      </CrewShell>
    );
  }

  if (loadError) {
    return (
      <CrewShell>
        <div className="team-manager load-error">
          <EmptyState
            icon="⚠️"
            title="Team Data Unavailable"
            message={loadError}
            action={{ label: 'Retry', onClick: () => { void loadTeamData(teamId); } }}
          />
        </div>
      </CrewShell>
    );
  }

  if (!team) {
    return (
      <CrewShell>
        <div className="team-manager no-team">
          <EmptyState
            icon="👥"
            title="No Team"
            message="You are not currently a member of any team. Join forces with other commanders to control sectors and share resources."
          >
            <button
              className="create-team-btn"
              onClick={() => {
                setCreateError(null);
                setShowCreateModal(true);
              }}
            >
              Create Team
            </button>
            {/* Canon gap: no GET /api/v1/teams list endpoint exists yet
                (new API surface, deferred) — browsing is disabled in-fiction */}
            <button className="browse-teams-btn" disabled>Browse Teams</button>
            <p className="registry-offline-note">TEAM REGISTRY OFFLINE — galactic registry uplink unavailable</p>
          </EmptyState>
        </div>
        {renderCreateModal()}
      </CrewShell>
    );
  }

  return (
    <CrewShell>
    <div className="team-manager">
      <div className="team-header">
        <div className="team-identity">
          <h2>{team.tag ? `[${team.tag}] ` : ''}{team.name}</h2>
          <div className="team-stats">
            <span className="members">👥 {team.memberCount}/{team.maxMembers}</span>
            <span className="founded">📅 Founded {new Date(team.founded).toLocaleDateString()}</span>
          </div>
        </div>
        <div className="team-tabs">
          <button
            className={activeTab === 'overview' ? 'active' : ''}
            onClick={() => setActiveTab('overview')}
          >
            Overview
          </button>
          <button
            className={activeTab === 'members' ? 'active' : ''}
            onClick={() => setActiveTab('members')}
          >
            Members
          </button>
          <button
            className={activeTab === 'treasury' ? 'active' : ''}
            onClick={() => setActiveTab('treasury')}
          >
            Treasury
          </button>
          <button
            className={activeTab === 'chat' ? 'active' : ''}
            onClick={() => setActiveTab('chat')}
          >
            Chat
          </button>
          <button
            className={activeTab === 'settings' ? 'active' : ''}
            onClick={() => setActiveTab('settings')}
          >
            Settings
          </button>
        </div>
      </div>

      <div className="team-content">
        {activeTab === 'overview' && (
          <div className="team-overview">
            <div className="team-description">
              <h3>Description</h3>
              <p>{team.description || 'No description set.'}</p>
            </div>

            <div className="team-recruitment">
              <h3>Recruitment Status</h3>
              <div className={`recruitment-status ${team.recruitmentStatus}`}>
                {team.recruitmentStatus === 'open' && '🟢 Open - Accepting new members'}
                {team.recruitmentStatus === 'invite-only' && '🟡 Invite Only - By invitation'}
                {team.recruitmentStatus === 'closed' && '🔴 Closed - Not recruiting'}
              </div>
            </div>

            <div className="team-quick-stats">
              <h3>Team Statistics</h3>
              <div className="stat-grid">
                <div className="stat-item">
                  <label>Planets Held</label>
                  <span className="stat-value">{team.totalPlanets}</span>
                </div>
                <div className="stat-item">
                  <label>Combat Rating</label>
                  <span className="stat-value">{team.combatRating.toFixed(1)}</span>
                </div>
                <div className="stat-item">
                  <label>Trade Rating</label>
                  <span className="stat-value">{team.tradeRating.toFixed(1)}</span>
                </div>
                <div className="stat-item">
                  <label>Members</label>
                  <span className="stat-value">{team.memberCount}/{team.maxMembers}</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'members' && (
          <div className="team-members">
            <div className="members-header">
              <h3>Team Members ({members.length})</h3>
            </div>

            {memberActionError && <div className="form-error" role="alert">{memberActionError}</div>}

            <div className="members-list">
              {members.map(member => (
                <div key={member.id} className="member-item">
                  <div className="member-info">
                    <div className="member-name">
                      <span className={`role-badge ${member.role}`}>{member.role}</span>
                      {member.playerName}
                    </div>
                    <div className="member-details">
                      <span>📍 {member.location.sectorName}</span>
                      <span>⚔️ Rating: {member.combatRating}</span>
                    </div>
                  </div>

                  <div className="member-contributions">
                    <div className="contribution-item">
                      <label>Credits</label>
                      <value>{member.contributions.credits.toLocaleString()}</value>
                    </div>
                    <div className="contribution-item">
                      <label>Resources</label>
                      <value>{member.contributions.resources.toLocaleString()}</value>
                    </div>
                  </div>

                  {member.playerId !== playerState.id && permissions && (
                    <div className="member-actions">
                      {permissions.canPromote && member.role === 'member' && (
                        <button onClick={() => handlePromoteMember(member.id, 'OFFICER')}>
                          Promote
                        </button>
                      )}
                      {permissions.canPromote && member.role === 'officer' && (
                        <button onClick={() => handlePromoteMember(member.id, 'MEMBER')}>
                          Demote
                        </button>
                      )}
                      {permissions.canKick && (
                        <button className="kick-btn" onClick={() => handleKickMember(member.id)}>
                          {confirmingKickId === member.id ? 'Confirm Kick?' : 'Kick'}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === 'treasury' && (
          <ResourceSharing
            teamId={team.id}
            playerId={playerState.id}
            members={members}
            playerCredits={playerState.credits}
            canManageTreasury={permissions?.canManageTreasury ?? false}
            onChanged={() => {
              // Treasury balance refreshes inside ResourceSharing; only the
              // player's wallet (header credits) needs a top-level refresh.
              // Avoid loadTeamData here — it flips global `loading` and would
              // flash the whole panel + reset the op selector after each op.
              void refreshPlayerState();
            }}
          />
        )}

        {activeTab === 'chat' && (
          <TeamChat
            teamId={team.id}
            playerId={playerState.id}
            members={members}
          />
        )}

        {activeTab === 'settings' && (
          <div className="team-settings">
            <h3>Team Settings</h3>

            {editingInfo ? (
              <div className="edit-team-info">
                <div className="form-group">
                  <label>Team Name</label>
                  {/* Canon gap: backend UpdateTeamRequest has no name field —
                      teams cannot be renamed via the API today */}
                  <input type="text" value={team.name} disabled />
                  <p className="field-note">Team names are fixed at registration.</p>
                </div>

                <div className="form-group">
                  <label>Description</label>
                  <textarea
                    value={teamInfo.description}
                    maxLength={500}
                    onChange={(e) => setTeamInfo({...teamInfo, description: e.target.value})}
                    disabled={!permissions?.canEditTeamInfo}
                    rows={4}
                  />
                </div>

                <div className="form-group">
                  <label>Recruitment Status</label>
                  <select
                    value={teamInfo.recruitmentStatus}
                    onChange={(e) => setTeamInfo({
                      ...teamInfo,
                      recruitmentStatus: e.target.value as Team['recruitmentStatus']
                    })}
                    disabled={!permissions?.canEditTeamInfo}
                  >
                    <option value="open">Open</option>
                    <option value="invite-only">Invite Only</option>
                    <option value="closed">Closed</option>
                  </select>
                </div>

                {saveError && <div className="form-error" role="alert">{saveError}</div>}

                <div className="form-actions">
                  <button onClick={handleSaveTeamInfo} disabled={!permissions?.canEditTeamInfo}>
                    Save Changes
                  </button>
                  <button onClick={() => setEditingInfo(false)} className="cancel-btn">
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="team-info-display">
                <div className="info-item">
                  <label>Team Name</label>
                  <value>{team.name}</value>
                </div>
                <div className="info-item">
                  <label>Team Tag</label>
                  <span className="stat-value">{team.tag ? `[${team.tag}]` : '—'}</span>
                </div>
                <div className="info-item">
                  <label>Description</label>
                  <span className="stat-value">{team.description || 'No description set.'}</span>
                </div>
                <div className="info-item">
                  <label>Recruitment</label>
                  <value>{team.recruitmentStatus}</value>
                </div>

                {permissions?.canEditTeamInfo && (
                  <button onClick={() => setEditingInfo(true)} className="edit-btn">
                    Edit Team Info
                  </button>
                )}
              </div>
            )}

            <div className="danger-zone">
              <h4>Danger Zone</h4>
              <button onClick={handleLeaveTeam} className="leave-team-btn">
                {confirmingLeave ? 'Confirm Leave?' : 'Leave Team'}
              </button>
              {leaveError && <div className="form-error" role="alert">{leaveError}</div>}
            </div>
          </div>
        )}
      </div>
    </div>
    </CrewShell>
  );
};

import React, { useState, useEffect } from 'react';
import { gameAPI, teamAPI } from '../../services/api';
import type { Team, TeamMember, TeamPermissions } from '../../types/team';
import GameLayout from '../layouts/GameLayout';
import EmptyState from '../common/EmptyState';
import LoadingState from '../common/LoadingState';
import './team-manager.css';

interface TeamManagerProps {
  playerId: string;
}

export const TeamManager: React.FC<TeamManagerProps> = ({ playerId }) => {
  const [team, setTeam] = useState<Team | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [permissions, setPermissions] = useState<TeamPermissions | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'overview' | 'members' | 'treasury' | 'settings'>('overview');
  const [editingInfo, setEditingInfo] = useState(false);
  const [teamInfo, setTeamInfo] = useState({ name: '', description: '', recruitmentStatus: '' });

  useEffect(() => {
    loadTeamData();
  }, []);

  const loadTeamData = async () => {
    try {
      setLoading(true);
      const [teamData, memberData, permData] = await Promise.all([
        gameAPI.team.getTeam('team-1'),
        gameAPI.team.getMembers('team-1'),
        gameAPI.team.getPermissions('team-1')
      ]);
      setTeam(teamData);
      setMembers(memberData.members || memberData || []);
      setPermissions(permData);
      setTeamInfo({
        name: teamData.name,
        description: teamData.description,
        recruitmentStatus: teamData.recruitmentStatus
      });
    } catch (error) {
      console.error('Failed to load team data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveTeamInfo = async () => {
    if (!team || !permissions?.canEditTeamInfo) return;
    
    try {
      const updatedTeam = await gameAPI.team.updateTeam(team.id, {
        name: teamInfo.name,
        description: teamInfo.description,
        recruitmentStatus: teamInfo.recruitmentStatus as Team['recruitmentStatus']
      });
      setTeam(updatedTeam);
      setEditingInfo(false);
    } catch (error) {
      console.error('Failed to update team info:', error);
    }
  };

  const handlePromoteMember = async (memberId: string, newRole: 'officer' | 'member') => {
    if (!permissions?.canPromote) return;
    
    try {
      const updatedMember = await teamAPI.promoteMember(team!.id, memberId, newRole);
      setMembers(members.map(m => m.id === memberId ? updatedMember : m));
    } catch (error) {
      console.error('Failed to promote member:', error);
    }
  };

  const handleKickMember = async (memberId: string) => {
    if (!permissions?.canKick) return;
    
    if (window.confirm('Are you sure you want to kick this member?')) {
      try {
        await teamAPI.kickMember(team!.id, memberId);
        setMembers(members.filter(m => m.id !== memberId));
      } catch (error) {
        console.error('Failed to kick member:', error);
      }
    }
  };

  const handleLeaveTeam = async () => {
    if (window.confirm('Are you sure you want to leave the team?')) {
      try {
        await teamAPI.leaveTeam(team!.id);
        setTeam(null);
      } catch (error) {
        console.error('Failed to leave team:', error);
      }
    }
  };

  if (loading) {
    return (
      <div className="team-manager loading">
        <LoadingState message="Loading team data..." />
      </div>
    );
  }

  if (!team) {
    return (
      <GameLayout>
        <div className="team-manager no-team">
          <EmptyState
            icon="👥"
            title="No Team"
            message="You are not currently a member of any team. Join forces with other commanders to control sectors and share resources."
          >
            <button className="create-team-btn">Create Team</button>
            <button className="browse-teams-btn">Browse Teams</button>
          </EmptyState>
        </div>
      </GameLayout>
    );
  }

  return (
    <GameLayout>
    <div className="team-manager">
      <div className="team-header">
        <div className="team-identity">
          <h2>[{team.tag}] {team.name}</h2>
          <div className="team-stats">
            <span className="reputation">⭐ {team.reputation.toLocaleString()}</span>
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
              <p>{team.description}</p>
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
                  <label>Total Kills</label>
                  <value>1,247</value>
                </div>
                <div className="stat-item">
                  <label>Sectors Controlled</label>
                  <value>8</value>
                </div>
                <div className="stat-item">
                  <label>Active Missions</label>
                  <value>3</value>
                </div>
                <div className="stat-item">
                  <label>Alliance Status</label>
                  <value>Independent</value>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'members' && (
          <div className="team-members">
            <div className="members-header">
              <h3>Team Members ({members.length})</h3>
              {permissions?.canInvite && (
                <button className="invite-btn">Invite Player</button>
              )}
            </div>

            <div className="members-list">
              {members.map(member => (
                <div key={member.id} className={`member-item ${member.online ? 'online' : 'offline'}`}>
                  <div className="member-info">
                    <div className="member-name">
                      <span className={`role-badge ${member.role}`}>{member.role}</span>
                      {member.playerName}
                      {member.online && <span className="online-indicator">●</span>}
                    </div>
                    <div className="member-details">
                      <span>📍 {member.location.sectorName}</span>
                      <span>🚀 {member.shipType}</span>
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
                    <div className="contribution-item">
                      <label>Kills</label>
                      <value>{member.contributions.combatKills}</value>
                    </div>
                  </div>

                  {member.playerId !== playerId && permissions && (
                    <div className="member-actions">
                      {permissions.canPromote && member.role === 'member' && (
                        <button onClick={() => handlePromoteMember(member.id, 'officer')}>
                          Promote
                        </button>
                      )}
                      {permissions.canPromote && member.role === 'officer' && (
                        <button onClick={() => handlePromoteMember(member.id, 'member')}>
                          Demote
                        </button>
                      )}
                      {permissions.canKick && (
                        <button className="kick-btn" onClick={() => handleKickMember(member.id)}>
                          Kick
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
          <div className="team-treasury">
            <h3>Team Treasury</h3>
            
            <div className="treasury-balance">
              <div className="resource-item">
                <label>Credits</label>
                <value>{team.treasury.credits.toLocaleString()}</value>
              </div>
              <div className="resource-item">
                <label>Fuel</label>
                <value>{team.treasury.fuel.toLocaleString()}</value>
              </div>
              <div className="resource-item">
                <label>Organics</label>
                <value>{team.treasury.organics.toLocaleString()}</value>
              </div>
              <div className="resource-item">
                <label>Equipment</label>
                <value>{team.treasury.equipment.toLocaleString()}</value>
              </div>
            </div>

            {permissions?.canManageTreasury && (
              <div className="treasury-actions">
                <button className="deposit-btn">Deposit Resources</button>
                <button className="withdraw-btn">Withdraw Resources</button>
              </div>
            )}

            <div className="treasury-history">
              <h4>Recent Transactions</h4>
              <div className="transaction-list">
                <div className="transaction-item deposit">
                  <span className="transaction-player">Captain Rodriguez</span>
                  <span className="transaction-amount">+50,000 credits</span>
                  <span className="transaction-time">1 hour ago</span>
                </div>
                <div className="transaction-item withdrawal">
                  <span className="transaction-player">Admiral Thompson</span>
                  <span className="transaction-amount">-10,000 fuel</span>
                  <span className="transaction-time">3 hours ago</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'settings' && (
          <div className="team-settings">
            <h3>Team Settings</h3>
            
            {editingInfo ? (
              <div className="edit-team-info">
                <div className="form-group">
                  <label>Team Name</label>
                  <input
                    type="text"
                    value={teamInfo.name}
                    onChange={(e) => setTeamInfo({...teamInfo, name: e.target.value})}
                    disabled={!permissions?.canEditTeamInfo}
                  />
                </div>
                
                <div className="form-group">
                  <label>Description</label>
                  <textarea
                    value={teamInfo.description}
                    onChange={(e) => setTeamInfo({...teamInfo, description: e.target.value})}
                    disabled={!permissions?.canEditTeamInfo}
                    rows={4}
                  />
                </div>

                <div className="form-group">
                  <label>Recruitment Status</label>
                  <select
                    value={teamInfo.recruitmentStatus}
                    onChange={(e) => setTeamInfo({...teamInfo, recruitmentStatus: e.target.value})}
                    disabled={!permissions?.canEditTeamInfo}
                  >
                    <option value="open">Open</option>
                    <option value="invite-only">Invite Only</option>
                    <option value="closed">Closed</option>
                  </select>
                </div>

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
                  <value>[{team.tag}]</value>
                </div>
                <div className="info-item">
                  <label>Description</label>
                  <value>{team.description}</value>
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
                Leave Team
              </button>
              {permissions?.canEditTeamInfo && members.length === 1 && (
                <button className="disband-team-btn">
                  Disband Team
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
    </GameLayout>
  );
};
import React, { useState, useEffect } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import './team-management.css';
import './team-management-override.css';

// Shape of GET /api/v1/admin/teams (admin.py get_all_teams) — snake_case,
// and deliberately narrow: the backend does NOT provide tag, max members,
// reputation, last activity, or combat ratings. Missing data renders as
// an em-dash rather than crashing or inventing values.
interface Team {
  id: string;
  name: string;
  leader_id: string | null;
  leader_name: string;
  member_count: number;
  total_credits: number;
  created_at: string | null;
  is_active: boolean;
}

// Shape of GET /api/v1/admin/alliances (admin.py get_all_alliances)
interface Alliance {
  id: string;
  name: string;
  type: string;
  team1Id: string;
  team2Id: string;
  status: string;
  created_at: string | null;
}

// Shape of GET /api/v1/admin/teams/analytics (camelCase from backend)
interface TeamStatsTeamRef {
  id: string;
  name: string;
  memberCount?: number;
  totalCombatRating?: number;
}

interface TeamStats {
  totalTeams: number;
  activeTeams: number;
  totalMembers: number;
  averageTeamSize: number;
  totalAlliances: number;
  mostPowerfulTeam: TeamStatsTeamRef | null;
  largestTeam: TeamStatsTeamRef | null;
}

const EM_DASH = '—';

const formatDate = (iso: string | null): string => {
  if (!iso) return EM_DASH;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? EM_DASH : d.toLocaleDateString();
};

export const TeamManagement: React.FC = () => {
  const [teams, setTeams] = useState<Team[]>([]);
  const [alliances, setAlliances] = useState<Alliance[]>([]);
  const [teamStats, setTeamStats] = useState<TeamStats | null>(null);
  const [selectedTeam, setSelectedTeam] = useState<Team | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [minSize, setMinSize] = useState<number | undefined>();
  const [maxSize, setMaxSize] = useState<number | undefined>();
  const [activeOnly, setActiveOnly] = useState(false);
  const [activeTab, setActiveTab] = useState<'overview' | 'alliances' | 'admin'>('overview');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      // Fetch teams. The backend endpoint takes no filter params;
      // search/size/active filtering is applied client-side below.
      const teamsResponse = await api.get('/api/v1/admin/teams');
      const teamsData = teamsResponse.data as { teams: Team[] };
      setTeams(teamsData.teams || []);
      
      // Fetch team statistics
      const statsResponse = await api.get('/api/v1/admin/teams/analytics');
      setTeamStats(statsResponse.data as TeamStats);
      
      // Fetch alliances
      const alliancesResponse = await api.get('/api/v1/admin/alliances');
      const alliancesData = alliancesResponse.data as { alliances: Alliance[] };
      setAlliances(alliancesData.alliances || []);

      if (teamsData.teams && teamsData.teams.length > 0 && !selectedTeam) {
        setSelectedTeam(teamsData.teams[0]);
      }
    } catch (error: any) {
      console.error('Failed to load team data:', error);
      setError(error.response?.data?.detail || 'Failed to load team data. Please check if the gameserver is running.');
      // Clear data on error
      setTeams([]);
      setTeamStats(null);
      setAlliances([]);
    } finally {
      setLoading(false);
    }
  };

  // Client-side filtering (the list endpoint has no server-side filters)
  const filteredTeams = teams.filter((team) => {
    if (searchTerm && !team.name.toLowerCase().includes(searchTerm.toLowerCase())) return false;
    if (minSize !== undefined && team.member_count < minSize) return false;
    if (maxSize !== undefined && team.member_count > maxSize) return false;
    if (activeOnly && !team.is_active) return false;
    return true;
  });

  // Team admin actions are disabled: the backend endpoint
  // POST /api/v1/admin/teams/{id}/action does not exist. The controls
  // below stay visible to document intent but are inert until the
  // endpoint is implemented.
  const TEAM_ACTION_ENDPOINT = 'POST /api/v1/admin/teams/{id}/action';

  return (
    <div className="team-management">
      <PageHeader title="Team Management" />
      
      {/* Error Notice */}
      {error && (
        <div className="alert error" style={{ marginBottom: '20px' }}>
          <span className="alert-icon">❌</span>
          <span className="alert-message">
            {error}
          </span>
        </div>
      )}
      
      {/* Search and Filter Controls */}
      <div className="team-controls">
        <div className="search-box">
          <input
            type="text"
            placeholder="Search teams..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
        <div className="filter-controls">
          <input
            type="number"
            placeholder="Min size"
            value={minSize || ''}
            onChange={(e) => setMinSize(e.target.value ? parseInt(e.target.value) : undefined)}
          />
          <input
            type="number"
            placeholder="Max size"
            value={maxSize || ''}
            onChange={(e) => setMaxSize(e.target.value ? parseInt(e.target.value) : undefined)}
          />
          <label>
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(e) => setActiveOnly(e.target.checked)}
            />
            Active only
          </label>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="tab-nav">
        <button
          className={`tab ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          Team Overview
        </button>
        <button
          className={`tab ${activeTab === 'alliances' ? 'active' : ''}`}
          onClick={() => setActiveTab('alliances')}
        >
          Alliance Network
        </button>
        <button
          className={`tab ${activeTab === 'admin' ? 'active' : ''}`}
          onClick={() => setActiveTab('admin')}
        >
          Admin Actions
        </button>
      </div>

      {/* Main Content Area */}
      <div className="team-content">
        {loading ? (
          <div className="loading-container">
            <div className="loading-spinner"></div>
            <p>Loading team data...</p>
            <p>Please wait while we fetch team information from the server.</p>
          </div>
        ) : (
          <>
            {activeTab === 'overview' && (
              <div className="team-overview">
                <div className="team-list-section">
                  <h3>Teams ({filteredTeams.length})</h3>
                  {filteredTeams.length === 0 ? (
                    <div className="empty-state">
                      <h3>No Teams Found</h3>
                      <p>{teams.length === 0
                        ? 'There are currently no teams in the system. Teams will appear here once players create them in the game.'
                        : 'No teams match the current filters.'}</p>
                    </div>
                  ) : (
                    <div className="team-list">
                      {filteredTeams.map((team) => (
                        <div
                          key={team.id}
                          className={`team-card ${selectedTeam?.id === team.id ? 'selected' : ''}`}
                          onClick={() => setSelectedTeam(team)}
                        >
                          <div className="team-header">
                            <span className="team-name">{team.name}</span>
                          </div>
                          <div className="team-info">
                            <span>Members: {team.member_count ?? EM_DASH}</span>
                            <span>Credits: {typeof team.total_credits === 'number' ? team.total_credits.toLocaleString() : EM_DASH}</span>
                            <span className={`status ${team.is_active ? 'active' : 'inactive'}`}>
                              {team.is_active ? 'Active' : 'Inactive'}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="team-details-section">
                  {selectedTeam && (
                    <>
                      <h3>Team Details: {selectedTeam.name}</h3>
                      <div className="team-details">
                        <div className="detail-row">
                          <span>Leader:</span>
                          <span>{selectedTeam.leader_name || EM_DASH}</span>
                        </div>
                        <div className="detail-row">
                          <span>Founded:</span>
                          <span>{formatDate(selectedTeam.created_at)}</span>
                        </div>
                        <div className="detail-row">
                          <span>Members:</span>
                          <span>{selectedTeam.member_count ?? EM_DASH}</span>
                        </div>
                        <div className="detail-row">
                          <span>Total Credits:</span>
                          <span>{typeof selectedTeam.total_credits === 'number' ? selectedTeam.total_credits.toLocaleString() : EM_DASH}</span>
                        </div>
                        <div className="detail-row">
                          <span>Status:</span>
                          <span>{selectedTeam.is_active ? 'Active' : 'Inactive'}</span>
                        </div>
                      </div>

                      <div className="team-strength-chart">
                        <h3>Team Credits Comparison</h3>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '12px' }}>
                          {teams.slice(0, 10).map(team => {
                            const maxCredits = Math.max(...teams.map(t => t.total_credits || 0), 1);
                            const widthPct = ((team.total_credits || 0) / maxCredits) * 100;
                            return (
                              <div key={team.id} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <span style={{ minWidth: '100px', fontSize: '0.85rem', color: team.id === selectedTeam.id ? '#60a5fa' : '#9ca3af' }}>
                                  {team.name}
                                </span>
                                <div style={{ flex: 1, height: '16px', backgroundColor: '#1f2937', borderRadius: '4px', overflow: 'hidden' }}>
                                  <div style={{
                                    width: `${widthPct}%`,
                                    height: '100%',
                                    backgroundColor: team.id === selectedTeam.id ? '#3b82f6' : '#4b5563',
                                    borderRadius: '4px',
                                    transition: 'width 0.3s'
                                  }} />
                                </div>
                                <span style={{ minWidth: '60px', textAlign: 'right', fontSize: '0.8rem', color: '#9ca3af' }}>
                                  {(team.total_credits || 0).toLocaleString()}
                                </span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </>
                  )}
                </div>
              </div>
            )}

            {activeTab === 'alliances' && (
              <div className="alliance-section">
                <h3>Alliance Network ({alliances.length} alliances)</h3>
                {alliances.length === 0 ? (
                  <div className="empty-state">
                    <h3>No Alliances Found</h3>
                    <p>There are currently no alliances between teams.</p>
                    <p>Alliances will appear here once teams form diplomatic agreements.</p>
                  </div>
                ) : (
                  <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '12px' }}>
                    <thead>
                      <tr>
                        <th style={{ textAlign: 'left', padding: '10px 12px', borderBottom: '1px solid #374151', color: '#9ca3af' }}>Alliance Name</th>
                        <th style={{ textAlign: 'left', padding: '10px 12px', borderBottom: '1px solid #374151', color: '#9ca3af' }}>Type</th>
                        <th style={{ textAlign: 'left', padding: '10px 12px', borderBottom: '1px solid #374151', color: '#9ca3af' }}>Member Teams</th>
                        <th style={{ textAlign: 'left', padding: '10px 12px', borderBottom: '1px solid #374151', color: '#9ca3af' }}>Founded</th>
                        <th style={{ textAlign: 'left', padding: '10px 12px', borderBottom: '1px solid #374151', color: '#9ca3af' }}>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {alliances.map(alliance => {
                        const isActive = alliance.status === 'active';
                        const memberNames = [alliance.team1Id, alliance.team2Id]
                          .filter(Boolean)
                          .map(teamId => teams.find(t => t.id === teamId)?.name || teamId);
                        return (
                          <tr key={alliance.id}>
                            <td style={{ padding: '10px 12px', borderBottom: '1px solid #1f2937' }}><strong>{alliance.name}</strong></td>
                            <td style={{ padding: '10px 12px', borderBottom: '1px solid #1f2937' }}>
                              <span style={{
                                padding: '2px 8px', borderRadius: '4px', fontSize: '0.8rem',
                                backgroundColor: alliance.type === 'mutual-defense' ? 'rgba(239, 68, 68, 0.2)' :
                                               alliance.type === 'trade' ? 'rgba(34, 197, 94, 0.2)' : 'rgba(59, 130, 246, 0.2)',
                                color: alliance.type === 'mutual-defense' ? '#ef4444' :
                                       alliance.type === 'trade' ? '#22c55e' : '#3b82f6'
                              }}>
                                {(alliance.type || 'unknown').replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                              </span>
                            </td>
                            <td style={{ padding: '10px 12px', borderBottom: '1px solid #1f2937' }}>
                              {memberNames.length > 0 ? memberNames.join(', ') : EM_DASH}
                            </td>
                            <td style={{ padding: '10px 12px', borderBottom: '1px solid #1f2937', color: '#9ca3af' }}>
                              {formatDate(alliance.created_at)}
                            </td>
                            <td style={{ padding: '10px 12px', borderBottom: '1px solid #1f2937' }}>
                              <span style={{
                                padding: '2px 8px', borderRadius: '4px', fontSize: '0.8rem',
                                backgroundColor: isActive ? 'rgba(34, 197, 94, 0.2)' : 'rgba(156, 163, 175, 0.2)',
                                color: isActive ? '#22c55e' : '#9ca3af'
                              }}>
                                {isActive ? 'Active' : alliance.status || 'Unknown'}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            )}

            {activeTab === 'admin' && selectedTeam && (
              <div className="admin-section">
                <h3>Admin Actions: {selectedTeam.name}</h3>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginTop: '16px' }}>
                  <div style={{ padding: '16px', background: '#1f2937', borderRadius: '8px', border: '1px solid #374151' }}>
                    <h4 style={{ margin: '0 0 8px 0', color: '#e5e7eb' }}>Team Details</h4>
                    <div style={{ fontSize: '0.9rem', color: '#9ca3af', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                      <div>Team ID: <span style={{ color: '#e5e7eb' }}>{selectedTeam.id}</span></div>
                      <div>Leader: <span style={{ color: '#e5e7eb' }}>{selectedTeam.leader_name || EM_DASH}</span></div>
                      <div>Members: <span style={{ color: '#e5e7eb' }}>{selectedTeam.member_count ?? EM_DASH}</span></div>
                      <div>Total Credits: <span style={{ color: '#e5e7eb' }}>{typeof selectedTeam.total_credits === 'number' ? selectedTeam.total_credits.toLocaleString() : EM_DASH}</span></div>
                      <div>Status: <span style={{ color: selectedTeam.is_active ? '#22c55e' : '#ef4444' }}>{selectedTeam.is_active ? 'Active' : 'Inactive'}</span></div>
                    </div>
                  </div>

                  <div style={{ padding: '16px', background: '#1f2937', borderRadius: '8px', border: '1px solid #374151' }}>
                    <h4 style={{ margin: '0 0 12px 0', color: '#e5e7eb' }}>Actions</h4>
                    <div
                      role="note"
                      style={{
                        margin: 0, padding: '10px 12px',
                        background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
                        borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
                      }}
                    >
                      Team admin actions are unavailable: the backend endpoint{' '}
                      <code style={{ color: '#fde68a' }}>{TEAM_ACTION_ENDPOINT}</code> is not implemented.
                      This panel does not invent disabled Activate / Leader / Reputation / Dissolve controls.
                    </div>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </div>
      {/* Team Statistics Dashboard */}
      {teamStats && (
        <div className="team-stats-grid">
          <div className="stat-card primary">
            <h3>Total Teams</h3>
            <div className="stat-value">{teamStats.totalTeams}</div>
            <div className="stat-change">{teamStats.activeTeams} active</div>
          </div>
          <div className="stat-card">
            <h3>Total Members</h3>
            <div className="stat-value">{teamStats.totalMembers}</div>
            <div className="stat-label">across all teams</div>
          </div>
          <div className="stat-card">
            <h3>Average Team Size</h3>
            <div className="stat-value">
              {Number.isFinite(teamStats.averageTeamSize) ? teamStats.averageTeamSize.toFixed(1) : EM_DASH}
            </div>
            <div className="stat-label">members per team</div>
          </div>
          <div className="stat-card">
            <h3>Active Alliances</h3>
            <div className="stat-value">{teamStats.totalAlliances}</div>
            <div className="stat-label">diplomatic agreements</div>
          </div>
          <div className="stat-card highlight">
            <h3>Most Powerful</h3>
            <div className="stat-value">{teamStats.mostPowerfulTeam?.name || 'N/A'}</div>
            <div className="stat-label">by combat rating</div>
          </div>
          <div className="stat-card highlight">
            <h3>Largest Team</h3>
            <div className="stat-value">{teamStats.largestTeam?.name || 'N/A'}</div>
            <div className="stat-label">
              {teamStats.largestTeam?.memberCount !== undefined
                ? `${teamStats.largestTeam.memberCount} members`
                : EM_DASH}
            </div>
          </div>
        </div>
      )}


    </div>
  );
};

export default TeamManagement;
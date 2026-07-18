import React, { useState, useEffect, useMemo } from 'react';
import { Alliance, DiplomaticRelation, Team } from '../../types/team';
import { gameAPI } from '../../services/api';
import { InputValidator } from '../../utils/security/inputValidation';
import './alliance-manager.css';

interface AllianceManagerProps {
  team: Team;
  currentPlayerId: string;
  isLeader: boolean;
  isOfficer: boolean;
}

export const AllianceManager: React.FC<AllianceManagerProps> = ({
  team,
  currentPlayerId,
  isLeader,
  isOfficer
}) => {
  const [activeTab, setActiveTab] = useState<'alliances' | 'relations' | 'proposals'>('alliances');
  const [alliances, setAlliances] = useState<Alliance[]>([]);
  const [relations, setRelations] = useState<DiplomaticRelation[]>([]);
  const [availableTeams, setAvailableTeams] = useState<Team[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showProposalForm, setShowProposalForm] = useState(false);
  const [proposalType, setProposalType] = useState<'alliance' | 'treaty'>('alliance');
  const [selectedTeamId, setSelectedTeamId] = useState('');
  const [proposalTerms, setProposalTerms] = useState<string[]>(['']);
  const [allianceType, setAllianceType] = useState<'mutual-defense' | 'trade' | 'non-aggression'>('trade');
  const [treatyType, setTreatyType] = useState<'peace' | 'trade' | 'defense' | 'non-aggression'>('trade');
  const [message, setMessage] = useState('');

  const canManageAlliances = isLeader || isOfficer;

  useEffect(() => {
    loadAllianceData();
  }, [team.id]);

  const loadAllianceData = async () => {
    setIsLoading(true);
    try {
      const [alliancesData, relationsData, teamsData] = await Promise.all([
        gameAPI.team.getAlliances(team.id),
        gameAPI.team.getDiplomaticRelations(team.id),
        gameAPI.team.getAvailableTeams()
      ]);
      setAlliances(alliancesData.alliances || alliancesData || []);
      setRelations(relationsData.relations || relationsData || []);
      setAvailableTeams(teamsData.filter(t => t.id !== team.id));
    } catch (error) {
      console.error('Failed to load alliance data:', error);
      setMessage('Failed to load alliance data');
    } finally {
      setIsLoading(false);
    }
  };

  const handleProposeAlliance = async () => {
    if (!selectedTeamId || proposalTerms.some(term => !term.trim())) {
      setMessage('Please select a team and enter all terms');
      return;
    }

    try {
      const sanitizedTerms = proposalTerms
        .map(term => InputValidator.sanitizeText(term))
        .filter(term => term.length > 0);

      if (proposalType === 'alliance') {
        // proposeAlliance/proposeTreaty take (teamId, data) — bundle the
        // target/type/terms into the data payload (WO-PUX-FE-ORPHANS parks
        // whether this dead-code call site survives disposition).
        await gameAPI.team.proposeAlliance(team.id, {
          target_team_id: selectedTeamId,
          type: allianceType,
          terms: sanitizedTerms
        });
        setMessage('Alliance proposal sent successfully');
      } else {
        await gameAPI.team.proposeTreaty(team.id, {
          target_team_id: selectedTeamId,
          type: treatyType,
          terms: sanitizedTerms
        });
        setMessage('Treaty proposal sent successfully');
      }
      
      setShowProposalForm(false);
      resetProposalForm();
      loadAllianceData();
    } catch (error) {
      console.error('Failed to send proposal:', error);
      setMessage('Failed to send proposal');
    }
  };

  const handleLeaveAlliance = async (allianceId: string) => {
    if (!window.confirm('Are you sure you want to leave this alliance?')) {
      return;
    }

    try {
      await gameAPI.team.leaveAlliance(team.id, allianceId);
      setMessage('Left alliance successfully');
      loadAllianceData();
    } catch (error) {
      console.error('Failed to leave alliance:', error);
      setMessage('Failed to leave alliance');
    }
  };

  const handleChangeRelation = async (targetTeamId: string, newType: DiplomaticRelation['type']) => {
    if (newType === 'war' && !window.confirm('Are you sure you want to declare war?')) {
      return;
    }

    try {
      await gameAPI.team.changeDiplomaticRelation(team.id, targetTeamId, newType);
      setMessage(`Diplomatic relation updated to ${newType}`);
      loadAllianceData();
    } catch (error) {
      console.error('Failed to change relation:', error);
      setMessage('Failed to change diplomatic relation');
    }
  };

  const handleAddTerm = () => {
    setProposalTerms([...proposalTerms, '']);
  };

  const handleUpdateTerm = (index: number, value: string) => {
    const updated = [...proposalTerms];
    updated[index] = value;
    setProposalTerms(updated);
  };

  const handleRemoveTerm = (index: number) => {
    setProposalTerms(proposalTerms.filter((_, i) => i !== index));
  };

  const resetProposalForm = () => {
    setSelectedTeamId('');
    setProposalTerms(['']);
    setAllianceType('trade');
    setTreatyType('trade');
  };

  const getRelationColor = (type: DiplomaticRelation['type']) => {
    switch (type) {
      case 'ally': return '#00ff00';
      case 'neutral': return '#999999';
      case 'hostile': return '#ff9900';
      case 'war': return '#ff0000';
    }
  };

  const getAllianceIcon = (type: Alliance['type']) => {
    switch (type) {
      case 'mutual-defense': return '🛡️';
      case 'trade': return '💰';
      case 'non-aggression': return '🤝';
    }
  };

  const activeAlliances = useMemo(() => 
    alliances.filter(a => !a.expiresAt || new Date(a.expiresAt) > new Date()),
    [alliances]
  );

  const expiredAlliances = useMemo(() => 
    alliances.filter(a => a.expiresAt && new Date(a.expiresAt) <= new Date()),
    [alliances]
  );

  if (isLoading) {
    return <div className="alliance-manager loading">Loading alliance data...</div>;
  }

  return (
    <div className="alliance-manager">
      <div className="alliance-header">
        <h2>Alliance & Diplomacy</h2>
        <div className="header-stats">
          <div className="stat">
            <span className="label">Active Alliances:</span>
            <span className="value">{activeAlliances.length}</span>
          </div>
          <div className="stat">
            <span className="label">Relations:</span>
            <span className="value">{relations.length}</span>
          </div>
        </div>
      </div>

      {message && (
        <div className={`message ${message.includes('Failed') ? 'error' : 'success'}`}>
          {message}
        </div>
      )}

      <div className="alliance-tabs">
        <button
          className={`tab ${activeTab === 'alliances' ? 'active' : ''}`}
          onClick={() => setActiveTab('alliances')}
        >
          Alliances ({activeAlliances.length})
        </button>
        <button
          className={`tab ${activeTab === 'relations' ? 'active' : ''}`}
          onClick={() => setActiveTab('relations')}
        >
          Relations ({relations.length})
        </button>
        <button
          className={`tab ${activeTab === 'proposals' ? 'active' : ''}`}
          onClick={() => setActiveTab('proposals')}
        >
          New Proposal
        </button>
      </div>

      <div className="alliance-content">
        {activeTab === 'alliances' && (
          <div className="alliances-panel">
            <h3>Active Alliances</h3>
            {activeAlliances.length === 0 ? (
              <div className="empty-state">
                <p>No active alliances</p>
                {canManageAlliances && (
                  <button onClick={() => setActiveTab('proposals')}>
                    Create Alliance
                  </button>
                )}
              </div>
            ) : (
              <div className="alliance-list">
                {activeAlliances.map(alliance => (
                  <div key={alliance.id} className="alliance-card">
                    <div className="alliance-header">
                      <h4>
                        {getAllianceIcon(alliance.type)} {alliance.name}
                      </h4>
                      <span className="alliance-type">{alliance.type}</span>
                    </div>
                    <div className="alliance-teams">
                      <strong>Members:</strong>
                      {alliance.teams.map(t => (
                        <span key={t.teamId} className="team-tag">
                          [{t.teamTag}] {t.teamName}
                        </span>
                      ))}
                    </div>
                    <div className="alliance-terms">
                      <strong>Terms:</strong>
                      <ul>
                        {alliance.terms.map((term, idx) => (
                          <li key={idx}>{term}</li>
                        ))}
                      </ul>
                    </div>
                    <div className="alliance-footer">
                      <span className="created">
                        Created: {new Date(alliance.createdAt).toLocaleDateString()}
                      </span>
                      {alliance.expiresAt && (
                        <span className="expires">
                          Expires: {new Date(alliance.expiresAt).toLocaleDateString()}
                        </span>
                      )}
                      {canManageAlliances && (
                        <button 
                          className="leave-btn"
                          onClick={() => handleLeaveAlliance(alliance.id)}
                        >
                          Leave Alliance
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {expiredAlliances.length > 0 && (
              <>
                <h3>Expired Alliances</h3>
                <div className="alliance-list expired">
                  {expiredAlliances.map(alliance => (
                    <div key={alliance.id} className="alliance-card expired">
                      <h4>{alliance.name} (Expired)</h4>
                      <span className="expired-date">
                        Expired: {new Date(alliance.expiresAt!).toLocaleDateString()}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {activeTab === 'relations' && (
          <div className="relations-panel">
            <h3>Diplomatic Relations</h3>
            {relations.length === 0 ? (
              <div className="empty-state">
                <p>No established diplomatic relations</p>
              </div>
            ) : (
              <div className="relations-grid">
                {relations.map(relation => {
                  const isFrom = relation.fromTeamId === team.id;
                  const otherTeam = isFrom ? relation.toTeamName : relation.fromTeamName;
                  const otherTeamId = isFrom ? relation.toTeamId : relation.fromTeamId;

                  return (
                    <div key={relation.id} className="relation-card">
                      <div className="relation-header">
                        <h4>{otherTeam}</h4>
                        <span 
                          className="relation-type"
                          style={{ color: getRelationColor(relation.type) }}
                        >
                          {relation.type.toUpperCase()}
                        </span>
                      </div>
                      {relation.treaty && (
                        <div className="treaty-info">
                          <strong>Treaty:</strong> {relation.treaty.type}
                          {relation.treaty.expiresAt && (
                            <span className="expires">
                              Expires: {new Date(relation.treaty.expiresAt).toLocaleDateString()}
                            </span>
                          )}
                        </div>
                      )}
                      <div className="relation-actions">
                        <span className="established">
                          Since: {new Date(relation.establishedAt).toLocaleDateString()}
                        </span>
                        {canManageAlliances && (
                          <select
                            value={relation.type}
                            onChange={(e) => handleChangeRelation(
                              otherTeamId, 
                              e.target.value as DiplomaticRelation['type']
                            )}
                            className="relation-select"
                          >
                            <option value="ally">Ally</option>
                            <option value="neutral">Neutral</option>
                            <option value="hostile">Hostile</option>
                            <option value="war">War</option>
                          </select>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {activeTab === 'proposals' && (
          <div className="proposals-panel">
            {!canManageAlliances ? (
              <div className="permission-denied">
                <p>Only team leaders and officers can propose alliances and treaties</p>
              </div>
            ) : !showProposalForm ? (
              <div className="proposal-options">
                <h3>Create New Proposal</h3>
                <div className="proposal-buttons">
                  <button
                    className="proposal-btn alliance"
                    onClick={() => {
                      setProposalType('alliance');
                      setShowProposalForm(true);
                    }}
                  >
                    <span className="icon">🤝</span>
                    <span className="title">Propose Alliance</span>
                    <span className="desc">Form a multi-team alliance</span>
                  </button>
                  <button
                    className="proposal-btn treaty"
                    onClick={() => {
                      setProposalType('treaty');
                      setShowProposalForm(true);
                    }}
                  >
                    <span className="icon">📜</span>
                    <span className="title">Propose Treaty</span>
                    <span className="desc">Bilateral agreement with another team</span>
                  </button>
                </div>
              </div>
            ) : (
              <div className="proposal-form">
                <h3>
                  {proposalType === 'alliance' ? 'Propose Alliance' : 'Propose Treaty'}
                </h3>
                
                <div className="form-group">
                  <label>Target Team</label>
                  <select
                    value={selectedTeamId}
                    onChange={(e) => setSelectedTeamId(e.target.value)}
                  >
                    <option value="">Select a team...</option>
                    {availableTeams.map(t => (
                      <option key={t.id} value={t.id}>
                        [{t.tag}] {t.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label>Type</label>
                  {proposalType === 'alliance' ? (
                    <select
                      value={allianceType}
                      onChange={(e) => setAllianceType(e.target.value as Alliance['type'])}
                    >
                      <option value="trade">Trade Alliance</option>
                      <option value="mutual-defense">Mutual Defense</option>
                      <option value="non-aggression">Non-Aggression Pact</option>
                    </select>
                  ) : (
                    <select
                      value={treatyType}
                      onChange={(e) => setTreatyType(e.target.value as 'peace' | 'trade' | 'defense' | 'non-aggression')}
                    >
                      <option value="trade">Trade Agreement</option>
                      <option value="defense">Defense Treaty</option>
                      <option value="non-aggression">Non-Aggression Treaty</option>
                      <option value="peace">Peace Treaty</option>
                    </select>
                  )}
                </div>

                <div className="form-group">
                  <label>Terms & Conditions</label>
                  {proposalTerms.map((term, idx) => (
                    <div key={idx} className="term-input">
                      <input
                        type="text"
                        value={term}
                        onChange={(e) => handleUpdateTerm(idx, e.target.value)}
                        placeholder="Enter term..."
                        maxLength={200}
                      />
                      {proposalTerms.length > 1 && (
                        <button 
                          className="remove-term"
                          onClick={() => handleRemoveTerm(idx)}
                        >
                          ✕
                        </button>
                      )}
                    </div>
                  ))}
                  <button className="add-term" onClick={handleAddTerm}>
                    + Add Term
                  </button>
                </div>

                <div className="form-actions">
                  <button className="cancel-btn" onClick={() => {
                    setShowProposalForm(false);
                    resetProposalForm();
                  }}>
                    Cancel
                  </button>
                  <button 
                    className="submit-btn"
                    onClick={handleProposeAlliance}
                    disabled={!selectedTeamId || proposalTerms.some(t => !t.trim())}
                  >
                    Send Proposal
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
import React, { useState, useEffect } from 'react';
import { DiplomaticRelation, Team } from '../../types/team';
import { gameAPI } from '../../services/api';
import { InputValidator } from '../../utils/security/inputValidation';
import './diplomacy-interface.css';

interface DiplomacyInterfaceProps {
  team: Team;
  currentPlayerId: string;
  isLeader: boolean;
  isOfficer: boolean;
}

interface Treaty {
  id: string;
  type: 'peace' | 'trade' | 'defense' | 'non-aggression';
  withTeam: string;
  terms: string[];
  status: 'proposed' | 'active' | 'expired' | 'rejected';
  proposedBy: string;
  proposedAt: string;
  expiresAt?: string;
}

export const DiplomacyInterface: React.FC<DiplomacyInterfaceProps> = ({
  team,
  currentPlayerId,
  isLeader,
  isOfficer
}) => {
  const [activeTab, setActiveTab] = useState<'overview' | 'treaties' | 'history'>('overview');
  const [relations, setRelations] = useState<DiplomaticRelation[]>([]);
  const [treaties, setTreaties] = useState<Treaty[]>([]);
  const [availableTeams, setAvailableTeams] = useState<Team[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedTeam, setSelectedTeam] = useState<Team | null>(null);
  const [showTreatyForm, setShowTreatyForm] = useState(false);
  const [treatyType, setTreatyType] = useState<Treaty['type']>('trade');
  const [treatyTerms, setTreatyTerms] = useState<string[]>(['']);
  const [treatyDuration, setTreatyDuration] = useState<number>(90); // days
  const [message, setMessage] = useState('');

  const canManageDiplomacy = isLeader || isOfficer;

  useEffect(() => {
    loadDiplomacyData();
  }, [team.id]);

  const loadDiplomacyData = async () => {
    setIsLoading(true);
    try {
      const [relationsData, teamsData] = await Promise.all([
        gameAPI.team.getDiplomaticRelations(team.id),
        gameAPI.team.getAvailableTeams()
      ]);
      
      setRelations(relationsData);
      setAvailableTeams(teamsData.filter(t => t.id !== team.id));
      
      // Fetch treaties from API
      try {
        // gameAPI.team has no getTreaties — no GET /treaties endpoint exists
        // (only proposeTreaty). Typed placeholder pending WO-PUX-FE-ORPHANS
        // disposition of this dead-code component.
        const getTreaties = async (_teamId: string): Promise<Treaty[]> => [];
        const treatiesData = await getTreaties(team.id);
        setTreaties(Array.isArray(treatiesData) ? treatiesData : []);
      } catch {
        // Treaty endpoint may not be implemented yet - show empty state
        setTreaties([]);
      }
    } catch (error) {
      console.error('Failed to load diplomacy data:', error);
      setMessage('Failed to load diplomacy data');
    } finally {
      setIsLoading(false);
    }
  };

  const getRelationColor = (type: DiplomaticRelation['type']) => {
    switch (type) {
      case 'ally': return '#00ff00';
      case 'neutral': return '#999999';
      case 'hostile': return '#ff9900';
      case 'war': return '#ff0000';
    }
  };

  const getRelationIcon = (type: DiplomaticRelation['type']) => {
    switch (type) {
      case 'ally': return '🤝';
      case 'neutral': return '😐';
      case 'hostile': return '⚠️';
      case 'war': return '⚔️';
    }
  };

  const getTreatyIcon = (type: Treaty['type']) => {
    switch (type) {
      case 'peace': return '☮️';
      case 'trade': return '💰';
      case 'defense': return '🛡️';
      case 'non-aggression': return '✋';
    }
  };

  const getStatusColor = (status: Treaty['status']) => {
    switch (status) {
      case 'active': return '#00ff00';
      case 'proposed': return '#ffff00';
      case 'expired': return '#666666';
      case 'rejected': return '#ff0000';
    }
  };

  const handleSelectTeam = (teamId: string) => {
    const selected = availableTeams.find(t => t.id === teamId);
    setSelectedTeam(selected || null);
  };

  const handleProposeTreaty = async () => {
    if (!selectedTeam || treatyTerms.some(term => !term.trim())) {
      setMessage('Please select a team and enter all terms');
      return;
    }

    try {
      const sanitizedTerms = treatyTerms
        .map(term => InputValidator.sanitizeText(term))
        .filter(term => term.length > 0);

      // proposeTreaty takes (teamId, data) — bundle target/type/terms into
      // the data payload.
      await gameAPI.team.proposeTreaty(team.id, {
        target_team_id: selectedTeam.id,
        type: treatyType,
        terms: sanitizedTerms
      });

      setMessage(`Treaty proposal sent to ${selectedTeam.name}`);
      setShowTreatyForm(false);
      resetTreatyForm();
      loadDiplomacyData();
    } catch (error) {
      console.error('Failed to propose treaty:', error);
      setMessage('Failed to propose treaty');
    }
  };

  const handleAcceptTreaty = async (treatyId: string) => {
    try {
      // In real implementation, this would accept the treaty
      setMessage('Treaty accepted');
      loadDiplomacyData();
    } catch (error) {
      console.error('Failed to accept treaty:', error);
      setMessage('Failed to accept treaty');
    }
  };

  const handleRejectTreaty = async (treatyId: string) => {
    try {
      // In real implementation, this would reject the treaty
      setMessage('Treaty rejected');
      loadDiplomacyData();
    } catch (error) {
      console.error('Failed to reject treaty:', error);
      setMessage('Failed to reject treaty');
    }
  };

  const handleCancelTreaty = async (treatyId: string) => {
    if (!window.confirm('Are you sure you want to cancel this treaty?')) {
      return;
    }

    try {
      // In real implementation, this would cancel the treaty
      setMessage('Treaty cancelled');
      loadDiplomacyData();
    } catch (error) {
      console.error('Failed to cancel treaty:', error);
      setMessage('Failed to cancel treaty');
    }
  };

  const handleDeclareWar = async (targetTeamId: string) => {
    if (!window.confirm('Are you sure you want to declare war? This action cannot be undone easily.')) {
      return;
    }

    try {
      await gameAPI.team.changeDiplomaticRelation(team.id, targetTeamId, 'war');
      setMessage('War declared');
      loadDiplomacyData();
    } catch (error) {
      console.error('Failed to declare war:', error);
      setMessage('Failed to declare war');
    }
  };

  const handleAddTerm = () => {
    setTreatyTerms([...treatyTerms, '']);
  };

  const handleUpdateTerm = (index: number, value: string) => {
    const updated = [...treatyTerms];
    updated[index] = value;
    setTreatyTerms(updated);
  };

  const handleRemoveTerm = (index: number) => {
    setTreatyTerms(treatyTerms.filter((_, i) => i !== index));
  };

  const resetTreatyForm = () => {
    setSelectedTeam(null);
    setTreatyType('trade');
    setTreatyTerms(['']);
    setTreatyDuration(90);
  };

  if (isLoading) {
    return <div className="diplomacy-interface loading">Loading diplomacy data...</div>;
  }

  return (
    <div className="diplomacy-interface">
      <div className="diplomacy-header">
        <h2>Diplomacy & Treaties</h2>
        <div className="header-stats">
          <div className="stat">
            <span className="label">Relations:</span>
            <span className="value">{relations.length}</span>
          </div>
          <div className="stat">
            <span className="label">Active Treaties:</span>
            <span className="value">{treaties.filter(t => t.status === 'active').length}</span>
          </div>
          <div className="stat">
            <span className="label">At War:</span>
            <span className="value war">{relations.filter(r => r.type === 'war').length}</span>
          </div>
        </div>
      </div>

      {message && (
        <div className={`message ${message.includes('Failed') ? 'error' : 'success'}`}>
          {message}
        </div>
      )}

      <div className="diplomacy-tabs">
        <button
          className={`tab ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          Relations Overview
        </button>
        <button
          className={`tab ${activeTab === 'treaties' ? 'active' : ''}`}
          onClick={() => setActiveTab('treaties')}
        >
          Treaties ({treaties.length})
        </button>
        <button
          className={`tab ${activeTab === 'history' ? 'active' : ''}`}
          onClick={() => setActiveTab('history')}
        >
          Diplomatic History
        </button>
      </div>

      <div className="diplomacy-content">
        {activeTab === 'overview' && (
          <div className="overview-panel">
            <div className="relations-overview">
              <h3>Current Relations</h3>
              <div className="relations-grid">
                {relations.map(relation => {
                  const isFrom = relation.fromTeamId === team.id;
                  const otherTeam = isFrom ? relation.toTeamName : relation.fromTeamName;
                  const otherTeamId = isFrom ? relation.toTeamId : relation.fromTeamId;

                  return (
                    <div key={relation.id} className={`relation-card ${relation.type}`}>
                      <div className="relation-header">
                        <span className="relation-icon">{getRelationIcon(relation.type)}</span>
                        <h4>{otherTeam}</h4>
                      </div>
                      <div className="relation-status">
                        <span 
                          className="status-badge"
                          style={{ backgroundColor: getRelationColor(relation.type) }}
                        >
                          {relation.type.toUpperCase()}
                        </span>
                        <span className="established">
                          Since {new Date(relation.establishedAt).toLocaleDateString()}
                        </span>
                      </div>
                      {relation.treaty && (
                        <div className="active-treaty">
                          <span className="treaty-type">
                            {getTreatyIcon(relation.treaty.type)} {relation.treaty.type} treaty
                          </span>
                        </div>
                      )}
                      {canManageDiplomacy && (
                        <div className="relation-actions">
                          {relation.type !== 'war' && (
                            <button
                              className="action-btn declare-war"
                              onClick={() => handleDeclareWar(otherTeamId)}
                            >
                              Declare War
                            </button>
                          )}
                          {relation.type === 'neutral' && (
                            <button
                              className="action-btn propose-treaty"
                              onClick={() => {
                                handleSelectTeam(otherTeamId);
                                setShowTreatyForm(true);
                              }}
                            >
                              Propose Treaty
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {canManageDiplomacy && (
              <div className="diplomatic-actions">
                <h3>Diplomatic Actions</h3>
                <button
                  className="action-btn new-treaty"
                  onClick={() => setShowTreatyForm(true)}
                >
                  Propose New Treaty
                </button>
              </div>
            )}
          </div>
        )}

        {activeTab === 'treaties' && (
          <div className="treaties-panel">
            <h3>Treaties & Agreements</h3>
            {treaties.length === 0 ? (
              <div className="empty-state">
                <p>No active treaties. Use diplomacy to forge alliances with other teams.</p>
              </div>
            ) : (
              <div className="treaties-list">
                {treaties.map(treaty => (
                  <div key={treaty.id} className={`treaty-card ${treaty.status}`}>
                    <div className="treaty-header">
                      <span className="treaty-icon">{getTreatyIcon(treaty.type)}</span>
                      <h4>{treaty.type.charAt(0).toUpperCase() + treaty.type.slice(1)} Treaty</h4>
                      <span 
                        className="status-badge"
                        style={{ backgroundColor: getStatusColor(treaty.status) }}
                      >
                        {treaty.status.toUpperCase()}
                      </span>
                    </div>
                    <div className="treaty-details">
                      <div className="treaty-parties">
                        <strong>With:</strong> {treaty.withTeam}
                      </div>
                      <div className="treaty-proposer">
                        <strong>Proposed by:</strong> {treaty.proposedBy}
                      </div>
                      <div className="treaty-date">
                        <strong>Date:</strong> {new Date(treaty.proposedAt).toLocaleDateString()}
                      </div>
                      {treaty.expiresAt && (
                        <div className="treaty-expiry">
                          <strong>Expires:</strong> {new Date(treaty.expiresAt).toLocaleDateString()}
                        </div>
                      )}
                    </div>
                    <div className="treaty-terms">
                      <strong>Terms:</strong>
                      <ul>
                        {treaty.terms.map((term, idx) => (
                          <li key={idx}>{term}</li>
                        ))}
                      </ul>
                    </div>
                    {canManageDiplomacy && (
                      <div className="treaty-actions">
                        {treaty.status === 'proposed' && treaty.proposedBy !== team.name && (
                          <>
                            <button
                              className="action-btn accept"
                              onClick={() => handleAcceptTreaty(treaty.id)}
                            >
                              Accept
                            </button>
                            <button
                              className="action-btn reject"
                              onClick={() => handleRejectTreaty(treaty.id)}
                            >
                              Reject
                            </button>
                          </>
                        )}
                        {treaty.status === 'active' && (
                          <button
                            className="action-btn cancel"
                            onClick={() => handleCancelTreaty(treaty.id)}
                          >
                            Cancel Treaty
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === 'history' && (
          <div className="history-panel">
            <h3>Diplomatic History</h3>
            <div className="history-timeline">
              <div className="timeline-item">
                <div className="timeline-date">2102-04-10</div>
                <div className="timeline-event hostile">
                  Relations with Black Raiders turned hostile
                </div>
              </div>
              <div className="timeline-item">
                <div className="timeline-date">2102-03-01</div>
                <div className="timeline-event treaty">
                  Trade treaty signed with Merchant Guild
                </div>
              </div>
              <div className="timeline-item">
                <div className="timeline-date">2102-02-15</div>
                <div className="timeline-event alliance">
                  Joined defensive alliance with Iron Guard
                </div>
              </div>
              <div className="timeline-item">
                <div className="timeline-date">2102-01-20</div>
                <div className="timeline-event neutral">
                  Established neutral relations with Neutral Zone
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {showTreatyForm && (
        <div className="treaty-modal">
          <div className="treaty-form">
            <h3>Propose Treaty</h3>
            
            <div className="form-group">
              <label>Target Team</label>
              <select
                value={selectedTeam?.id || ''}
                onChange={(e) => handleSelectTeam(e.target.value)}
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
              <label>Treaty Type</label>
              <select
                value={treatyType}
                onChange={(e) => setTreatyType(e.target.value as Treaty['type'])}
              >
                <option value="trade">Trade Agreement</option>
                <option value="defense">Defense Treaty</option>
                <option value="non-aggression">Non-Aggression Pact</option>
                <option value="peace">Peace Treaty</option>
              </select>
            </div>

            <div className="form-group">
              <label>Duration (days)</label>
              <input
                type="number"
                value={treatyDuration}
                onChange={(e) => setTreatyDuration(Math.max(1, parseInt(e.target.value) || 90))}
                min="1"
                max="365"
              />
            </div>

            <div className="form-group">
              <label>Terms & Conditions</label>
              {treatyTerms.map((term, idx) => (
                <div key={idx} className="term-input">
                  <input
                    type="text"
                    value={term}
                    onChange={(e) => handleUpdateTerm(idx, e.target.value)}
                    placeholder="Enter term..."
                    maxLength={200}
                  />
                  {treatyTerms.length > 1 && (
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
                setShowTreatyForm(false);
                resetTreatyForm();
              }}>
                Cancel
              </button>
              <button 
                className="submit-btn"
                onClick={handleProposeTreaty}
                disabled={!selectedTeam || treatyTerms.some(t => !t.trim())}
              >
                Send Proposal
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
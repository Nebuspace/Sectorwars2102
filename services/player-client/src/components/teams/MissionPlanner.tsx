import React, { useState, useEffect } from 'react';
import { teamAPI } from '../../services/api';
import type { TeamMission, MissionObjective, TeamMember } from '../../types/team';
import './mission-planner.css';

interface MissionPlannerProps {
  teamId: string;
  playerId: string;
  canStartMissions: boolean;
}

export const MissionPlanner: React.FC<MissionPlannerProps> = ({ 
  teamId, 
  playerId, 
  canStartMissions 
}) => {
  const [missions, setMissions] = useState<TeamMission[]>([]);
  const [selectedMission, setSelectedMission] = useState<TeamMission | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [creatingMission, setCreatingMission] = useState(false);
  const [loading, setLoading] = useState(true);
  
  const [newMission, setNewMission] = useState({
    name: '',
    description: '',
    type: 'combat' as TeamMission['type'],
    objectives: [] as Partial<MissionObjective>[]
  });

  useEffect(() => {
    loadData();
  }, [teamId]);

  const loadData = async () => {
    try {
      setLoading(true);
      const [missionData, memberData] = await Promise.all([
        teamAPI.getMissions(teamId),
        teamAPI.getMembers(teamId)
      ]);
      setMissions(missionData);
      setMembers(memberData);
      if (missionData.length > 0) {
        setSelectedMission(missionData[0]);
      }
    } catch (error) {
      console.error('Failed to load data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateMission = async () => {
    if (!newMission.name || !newMission.description) {
      alert('Please fill in mission name and description');
      return;
    }

    try {
      const mission = await teamAPI.createMission(teamId, {
        ...newMission,
        objectives: newMission.objectives.map((obj, index) => ({
          id: `obj-${Date.now()}-${index}`,
          description: obj.description || '',
          type: obj.type || 'destroy',
          completed: false
        }))
      });
      
      setMissions([...missions, mission]);
      setSelectedMission(mission);
      setCreatingMission(false);
      setNewMission({
        name: '',
        description: '',
        type: 'combat',
        objectives: []
      });
    } catch (error) {
      console.error('Failed to create mission:', error);
    }
  };

  const handleJoinMission = async (missionId: string) => {
    try {
      await teamAPI.joinMission(teamId, missionId);
      const updatedMission = missions.find(m => m.id === missionId);
      if (updatedMission && !updatedMission.participants.includes(playerId)) {
        updatedMission.participants.push(playerId);
        setMissions([...missions]);
      }
    } catch (error) {
      console.error('Failed to join mission:', error);
    }
  };

  const handleLeaveMission = async (missionId: string) => {
    try {
      await teamAPI.leaveMission(teamId, missionId);
      const updatedMission = missions.find(m => m.id === missionId);
      if (updatedMission) {
        updatedMission.participants = updatedMission.participants.filter(p => p !== playerId);
        setMissions([...missions]);
      }
    } catch (error) {
      console.error('Failed to leave mission:', error);
    }
  };

  const handleStartMission = async (missionId: string) => {
    if (!canStartMissions) return;
    
    try {
      const mission = missions.find(m => m.id === missionId);
      if (!mission) return;
      
      const updatedMission = await teamAPI.updateMission(teamId, missionId, {
        status: 'active',
        startTime: new Date().toISOString()
      });
      
      setMissions(missions.map(m => m.id === missionId ? updatedMission : m));
      setSelectedMission(updatedMission);
    } catch (error) {
      console.error('Failed to start mission:', error);
    }
  };

  const addObjective = () => {
    setNewMission({
      ...newMission,
      objectives: [...newMission.objectives, {
        description: '',
        type: 'destroy'
      }]
    });
  };

  const updateObjective = (index: number, field: keyof MissionObjective, value: any) => {
    const updatedObjectives = [...newMission.objectives];
    updatedObjectives[index] = {
      ...updatedObjectives[index],
      [field]: value
    };
    setNewMission({
      ...newMission,
      objectives: updatedObjectives
    });
  };

  const removeObjective = (index: number) => {
    setNewMission({
      ...newMission,
      objectives: newMission.objectives.filter((_, i) => i !== index)
    });
  };

  const getMissionIcon = (type: TeamMission['type']) => {
    const icons = {
      combat: '⚔️',
      trading: '📦',
      exploration: '🔍',
      defense: '🛡️',
      siege: '🔥'
    };
    return icons[type] || '📋';
  };

  const getMissionStatusColor = (status: TeamMission['status']) => {
    const colors = {
      planning: 'planning',
      active: 'active',
      completed: 'completed',
      failed: 'failed'
    };
    return colors[status] || 'planning';
  };

  if (loading) {
    return <div className="mission-planner loading">Loading missions...</div>;
  }

  return (
    <div className="mission-planner">
      <div className="planner-header">
        <h3>Mission Planner</h3>
        {canStartMissions && (
          <button 
            className="create-mission-btn"
            onClick={() => setCreatingMission(true)}
          >
            Create New Mission
          </button>
        )}
      </div>

      {creatingMission && (
        <div className="create-mission-form">
          <h4>Create New Mission</h4>
          
          <div className="form-group">
            <label>Mission Name</label>
            <input
              type="text"
              value={newMission.name}
              onChange={(e) => setNewMission({...newMission, name: e.target.value})}
              placeholder="e.g., Secure Sector 99"
              maxLength={50}
            />
          </div>

          <div className="form-group">
            <label>Description</label>
            <textarea
              value={newMission.description}
              onChange={(e) => setNewMission({...newMission, description: e.target.value})}
              placeholder="Describe the mission objectives and strategy..."
              rows={3}
              maxLength={200}
            />
          </div>

          <div className="form-group">
            <label>Mission Type</label>
            <select
              value={newMission.type}
              onChange={(e) => setNewMission({...newMission, type: e.target.value as TeamMission['type']})}
            >
              <option value="combat">Combat</option>
              <option value="trading">Trading</option>
              <option value="exploration">Exploration</option>
              <option value="defense">Defense</option>
              <option value="siege">Siege</option>
            </select>
          </div>

          <div className="objectives-section">
            <div className="objectives-header">
              <label>Objectives</label>
              <button className="add-objective-btn" onClick={addObjective}>
                Add Objective
              </button>
            </div>
            
            {newMission.objectives.map((obj, index) => (
              <div key={index} className="objective-input">
                <input
                  type="text"
                  value={obj.description || ''}
                  onChange={(e) => updateObjective(index, 'description', e.target.value)}
                  placeholder="Objective description"
                />
                <select
                  value={obj.type || 'destroy'}
                  onChange={(e) => updateObjective(index, 'type', e.target.value)}
                >
                  <option value="destroy">Destroy</option>
                  <option value="capture">Capture</option>
                  <option value="deliver">Deliver</option>
                  <option value="defend">Defend</option>
                  <option value="explore">Explore</option>
                </select>
                <button 
                  className="remove-btn"
                  onClick={() => removeObjective(index)}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>

          <div className="form-actions">
            <button onClick={handleCreateMission}>Create Mission</button>
            <button className="cancel-btn" onClick={() => setCreatingMission(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="missions-container">
        <div className="missions-list">
          <h4>Team Missions</h4>
          {missions.length === 0 ? (
            <div className="no-missions">
              <p>No active missions</p>
              {canStartMissions && (
                <p className="hint">Create a mission to coordinate team efforts</p>
              )}
            </div>
          ) : (
            missions.map(mission => (
              <div
                key={mission.id}
                className={`mission-item ${selectedMission?.id === mission.id ? 'selected' : ''} ${getMissionStatusColor(mission.status)}`}
                onClick={() => setSelectedMission(mission)}
                role="button"
                tabIndex={0}
                aria-pressed={selectedMission?.id === mission.id}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setSelectedMission(mission);
                  }
                }}
              >
                <div className="mission-header">
                  <span className="mission-icon">{getMissionIcon(mission.type)}</span>
                  <span className="mission-name">{mission.name}</span>
                </div>
                <div className="mission-stats">
                  <span className={`status ${mission.status}`}>{mission.status}</span>
                  <span className="participants">👥 {mission.participants.length}</span>
                </div>
              </div>
            ))
          )}
        </div>

        {selectedMission && (
          <div className="mission-details">
            <div className="detail-header">
              <h4>{getMissionIcon(selectedMission.type)} {selectedMission.name}</h4>
              <span className={`mission-status ${selectedMission.status}`}>
                {selectedMission.status.toUpperCase()}
              </span>
            </div>

            <div className="detail-content">
              <div className="mission-description">
                <p>{selectedMission.description}</p>
              </div>

              <div className="mission-info">
                <div className="info-item">
                  <label>Created by:</label>
                  <value>{members.find(m => m.playerId === selectedMission.createdBy)?.playerName || 'Unknown'}</value>
                </div>
                <div className="info-item">
                  <label>Created:</label>
                  <value>{new Date(selectedMission.createdAt).toLocaleDateString()}</value>
                </div>
                {selectedMission.startTime && (
                  <div className="info-item">
                    <label>Started:</label>
                    <value>{new Date(selectedMission.startTime).toLocaleString()}</value>
                  </div>
                )}
              </div>

              <div className="mission-objectives">
                <h5>Objectives</h5>
                {selectedMission.objectives.length === 0 ? (
                  <p className="no-objectives">No specific objectives set</p>
                ) : (
                  <div className="objectives-list">
                    {selectedMission.objectives.map(obj => (
                      <div key={obj.id} className={`objective ${obj.completed ? 'completed' : ''}`}>
                        <span className="objective-check">
                          {obj.completed ? '✓' : '○'}
                        </span>
                        <span className="objective-text">{obj.description}</span>
                        {obj.requiredAmount && (
                          <span className="objective-progress">
                            {obj.currentAmount || 0}/{obj.requiredAmount}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="mission-participants">
                <h5>Participants ({selectedMission.participants.length})</h5>
                <div className="participants-list">
                  {selectedMission.participants.map(participantId => {
                    const member = members.find(m => m.playerId === participantId);
                    return member ? (
                      <div key={participantId} className="participant">
                        <span className={`participant-name ${member.online ? 'online' : 'offline'}`}>
                          {member.playerName}
                          {member.online && <span className="online-dot">●</span>}
                        </span>
                        <span className="participant-role">{member.role}</span>
                      </div>
                    ) : null;
                  })}
                </div>
              </div>

              {selectedMission.rewards && (
                <div className="mission-rewards">
                  <h5>Rewards</h5>
                  <div className="rewards-list">
                    {selectedMission.rewards.credits && (
                      <div className="reward-item">
                        <span>Credits:</span>
                        <value>{selectedMission.rewards.credits.toLocaleString()}</value>
                      </div>
                    )}
                    {selectedMission.rewards.reputation && (
                      <div className="reward-item">
                        <span>Reputation:</span>
                        <value>+{selectedMission.rewards.reputation}</value>
                      </div>
                    )}
                  </div>
                </div>
              )}

              <div className="mission-actions">
                {selectedMission.status === 'planning' && (
                  <>
                    {!selectedMission.participants.includes(playerId) ? (
                      <button 
                        className="join-btn"
                        onClick={() => handleJoinMission(selectedMission.id)}
                      >
                        Join Mission
                      </button>
                    ) : (
                      <button 
                        className="leave-btn"
                        onClick={() => handleLeaveMission(selectedMission.id)}
                      >
                        Leave Mission
                      </button>
                    )}
                    {canStartMissions && selectedMission.participants.length > 0 && (
                      <button 
                        className="start-btn"
                        onClick={() => handleStartMission(selectedMission.id)}
                      >
                        Start Mission
                      </button>
                    )}
                  </>
                )}
                {selectedMission.status === 'active' && selectedMission.participants.includes(playerId) && (
                  <p className="active-hint">Mission in progress - complete objectives!</p>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
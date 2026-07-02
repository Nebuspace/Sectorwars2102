import React, { useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
import UserProfile from '../auth/UserProfile';
import { resourceIcon } from '../../services/resourceCatalog';
import './pages.css';

interface DashboardProps {
  apiStatus: string;
  apiMessage: string;
  apiEnvironment: string;
}

const Dashboard: React.FC<DashboardProps> = ({
  apiStatus,
  apiMessage,
  apiEnvironment
}) => {
  const { user } = useAuth();
  const { playerState, ships, currentShip, currentSector, isLoading } = useGame();
  const [activeTab, setActiveTab] = useState('overview');
  
  // Build cargo resource list from current ship
  const cargoResources = currentShip?.cargo
    ? Object.entries(currentShip.cargo)
        .filter(([, amount]) => (amount as number) > 0)
        .map(([type, amount]) => ({ type, amount: amount as number }))
    : [];

  if (isLoading && !playerState) {
    return (
      <div className="dashboard">
        <div className="dashboard-header">
          <div className="dashboard-title">
            <h2>Command Center</h2>
          </div>
        </div>
        <div className="coming-soon">
          <div className="coming-soon-content">
            <p>Loading player data...</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <div className="dashboard-title">
          <h2>Command Center</h2>
          <div className="sector-indicator">
            <span className="sector-label">Current Sector:</span>
            <span className="sector-value">{currentSector?.name || `Sector ${playerState?.current_sector_id ?? '---'}`}</span>
          </div>
        </div>
        <UserProfile />
      </div>

      <div className="resource-bar">
        <div className="resource-item credits">
          <span className="resource-icon">💰</span>
          <div className="resource-details">
            <span className="resource-label">Credits</span>
            <span className="resource-value">{(playerState?.credits ?? 0).toLocaleString()}</span>
          </div>
        </div>

        <div className="resource-item turns">
          <span className="resource-icon">⏱️</span>
          <div className="resource-details">
            <span className="resource-label">Turns</span>
            <span className="resource-value">{playerState?.turns ?? 0}</span>
          </div>
        </div>

        {cargoResources.length > 0 ? (
          cargoResources.map((resource, index) => (
            <div key={index} className={`resource-item ${resource.type.toLowerCase()}`}>
              <span className="resource-icon">{resourceIcon(resource.type)}</span>
              <div className="resource-details">
                <span className="resource-label">{resource.type.charAt(0).toUpperCase() + resource.type.slice(1)}</span>
                <span className="resource-value">{resource.amount}</span>
              </div>
            </div>
          ))
        ) : (
          <div className="resource-item">
            <span className="resource-icon">📦</span>
            <div className="resource-details">
              <span className="resource-label">Cargo</span>
              <span className="resource-value">Empty</span>
            </div>
          </div>
        )}
      </div>
      
      <div className="dashboard-tabs">
        <button 
          className={`tab-button ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          Overview
        </button>
        <button 
          className={`tab-button ${activeTab === 'ships' ? 'active' : ''}`}
          onClick={() => setActiveTab('ships')}
        >
          Ships
        </button>
        <button 
          className={`tab-button ${activeTab === 'navigation' ? 'active' : ''}`}
          onClick={() => setActiveTab('navigation')}
        >
          Navigation
        </button>
        <button 
          className={`tab-button ${activeTab === 'trading' ? 'active' : ''}`}
          onClick={() => setActiveTab('trading')}
        >
          Trading
        </button>
        <button 
          className={`tab-button ${activeTab === 'missions' ? 'active' : ''}`}
          onClick={() => setActiveTab('missions')}
        >
          Missions
        </button>
      </div>
      
      <div className="dashboard-content">
        {activeTab === 'overview' && (
          <>
            <section className="welcome-section">
              <div className="section-header">
                <h3>Welcome, {user?.username}!</h3>
                <div className="status-indicator">
                  <span className={`status-dot ${apiStatus.includes('Connected') ? 'connected' : 'disconnected'}`}></span>
                  <span className="status-text">{apiStatus}</span>
                </div>
              </div>
              <p className="welcome-message">Welcome to Sector Wars 2102, where you can navigate the galaxy, trade valuable resources, and build your own space empire across the stars.</p>
              <div className="server-info">
                <div className="server-info-item">
                  <span className="info-label">Server:</span>
                  <span className="info-value">{apiEnvironment}</span>
                </div>
                <div className="server-info-item">
                  <span className="info-label">Status:</span>
                  <span className="info-value">{apiMessage}</span>
                </div>
              </div>
            </section>
            
            <div className="overview-grid">
              <section className="ships-overview">
                <h3>Your Fleet</h3>
                <div className="ship-list">
                  {ships.length > 0 ? (
                    ships.map(ship => {
                      const isCurrent = ship.id === currentShip?.id;
                      return (
                        <div key={ship.id} className={`ship-item ${isCurrent ? 'selected' : ''}`}>
                          <div className="ship-icon">🚀</div>
                          <div className="ship-details">
                            <div className="ship-name">{ship.name}{isCurrent ? ' (Active)' : ''}</div>
                            <div className="ship-type">{ship.type}</div>
                            <div className="ship-health">
                              <div className="health-bar">
                                <div
                                  className="health-fill"
                                  style={{ width: '100%', backgroundColor: '#10b981' }}
                                />
                              </div>
                              <span className="health-text">Cargo: {Object.values(ship.cargo || {}).reduce((a: number, b: number) => a + b, 0)}/{ship.cargo_capacity}</span>
                            </div>
                          </div>
                        </div>
                      );
                    })
                  ) : (
                    <div className="ship-item">
                      <div className="ship-icon">🚀</div>
                      <div className="ship-details">
                        <div className="ship-name">No ships available</div>
                        <div className="ship-type">Visit a space dock to acquire a ship</div>
                      </div>
                    </div>
                  )}
                </div>
              </section>

              <section className="notifications">
                <h3>Status</h3>
                <div className="notification-list">
                  <div className="notification-item discovery">
                    <div className="notification-icon">📍</div>
                    <div className="notification-content">
                      <div className="notification-message">Current sector: {currentSector?.name || `Sector ${playerState?.current_sector_id ?? '---'}`}</div>
                      <div className="notification-time">{currentSector?.type || 'Unknown type'}</div>
                    </div>
                  </div>
                  {playerState?.is_docked && (
                    <div className="notification-item trade">
                      <div className="notification-icon">🔗</div>
                      <div className="notification-content">
                        <div className="notification-message">Currently docked at a station</div>
                      </div>
                    </div>
                  )}
                  {playerState?.is_landed && (
                    <div className="notification-item trade">
                      <div className="notification-icon">🌍</div>
                      <div className="notification-content">
                        <div className="notification-message">Currently on a planet surface</div>
                      </div>
                    </div>
                  )}
                  <div className="notification-item warning">
                    <div className="notification-icon">🤖</div>
                    <div className="notification-content">
                      <div className="notification-message">Drones: {playerState?.attack_drones ?? 0} attack / {playerState?.defense_drones ?? 0} defense</div>
                    </div>
                  </div>
                </div>
              </section>

              <section className="recent-activity">
                <h3>Player Info</h3>
                <div className="activity-timeline">
                  <div className="activity-item">
                    <div className="activity-content">
                      <div className="activity-action">Reputation: {playerState?.reputation_tier || 'Unknown'}</div>
                      <div className="activity-details">
                        <span className="activity-location">Score: {playerState?.personal_reputation ?? 0}</span>
                      </div>
                    </div>
                  </div>
                  <div className="activity-item">
                    <div className="activity-content">
                      <div className="activity-action">Military Rank: {playerState?.military_rank || 'Civilian'}</div>
                    </div>
                  </div>
                  {playerState?.team_id && (
                    <div className="activity-item">
                      <div className="activity-content">
                        <div className="activity-action">Team Member</div>
                      </div>
                    </div>
                  )}
                </div>
              </section>
              
              <section className="game-actions">
                <h3>Quick Actions</h3>
                <div className="action-buttons">
                  <button className="action-button explore">
                    <span className="action-icon">🔭</span>
                    <span className="action-text">Explore Sector</span>
                  </button>
                  <button className="action-button trade">
                    <span className="action-icon">💹</span>
                    <span className="action-text">Trade Resources</span>
                  </button>
                  <button className="action-button upgrade">
                    <span className="action-icon">⬆️</span>
                    <span className="action-text">Upgrade Ship</span>
                  </button>
                  <button className="action-button scan">
                    <span className="action-icon">📡</span>
                    <span className="action-text">Scan Nearby</span>
                  </button>
                </div>
              </section>
            </div>
          </>
        )}
        
        {/* Other tabs would be implemented here */}
        {activeTab !== 'overview' && (
          <div className="coming-soon">
            <h3>{activeTab.charAt(0).toUpperCase() + activeTab.slice(1)} Module</h3>
            <div className="coming-soon-content">
              <div className="coming-soon-icon">🚧</div>
              <p>This feature is currently under development and will be available soon!</p>
              <button className="back-button" onClick={() => setActiveTab('overview')}>
                Return to Overview
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default Dashboard;
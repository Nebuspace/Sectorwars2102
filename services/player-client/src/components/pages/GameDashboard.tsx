import React, { useEffect, useRef, useState, useMemo } from 'react';
import { useGame } from '../../contexts/GameContext';
import { useFirstLogin } from '../../contexts/FirstLoginContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
// import { useTheme } from '../../themes/ThemeProvider'; // Available for future use
import GameLayout from '../layouts/GameLayout';
import TradingInterface from '../trading/TradingInterface';
import SpaceDockInterface from '../spacedock/SpaceDockInterface';
import EnhancedAIAssistant from '../ai/EnhancedAIAssistant';
import TacticalCard from '../tactical/TacticalCard';
import SectorViewport from '../tactical/SectorViewport';
import PlanetPortPair from '../tactical/PlanetPortPair';
import NavigationMap from '../tactical/NavigationMap';
import './game-dashboard.css';
import './cockpit.css';
import '../tactical/tactical-layout.css';

const GameDashboard: React.FC = () => {
  const {
    playerState,
    currentSector,
    planetsInSector,
    stationsInSector,
    availableMoves,
    moveToSector,
    dockAtStation,
    undockFromStation,
    claimPlanet,
    landOnPlanet,
    leavePlanet,
    renamePlanet,
    exploreCurrentLocation,
    error
  } = useGame();
  
  const { requiresFirstLogin } = useFirstLogin();
  const { sectorPlayers, isConnected } = useWebSocket();

  const [movementResult, setMovementResult] = useState<any>(null);
  const [dockingResult, setDockingResult] = useState<any>(null);
  const [landingResult, setLandingResult] = useState<any>(null);

  // Production allocation state (must total 100%)
  const [allocations, setAllocations] = useState({
    fuel: 20,
    organics: 20,
    equipment: 20,
    ore: 20,
    terraform: 20
  });

  // Handle allocation slider change with equilibrium
  const handleAllocationChange = (resource: keyof typeof allocations, newValue: number) => {
    const oldValue = allocations[resource];
    const diff = newValue - oldValue;

    if (diff === 0) return;

    // Get other resources to distribute the difference
    const otherResources = (Object.keys(allocations) as Array<keyof typeof allocations>)
      .filter(r => r !== resource);

    // Calculate total of other resources
    const otherTotal = otherResources.reduce((sum, r) => sum + allocations[r], 0);

    if (otherTotal === 0 && diff > 0) {
      // Can't increase if others are all 0
      return;
    }

    // Distribute the difference proportionally among other resources
    const newAllocations = { ...allocations, [resource]: newValue };

    otherResources.forEach(r => {
      if (otherTotal > 0) {
        const proportion = allocations[r] / otherTotal;
        const adjustment = Math.round(diff * proportion);
        newAllocations[r] = Math.max(0, Math.min(100, allocations[r] - adjustment));
      }
    });

    // Ensure total is exactly 100
    const total = Object.values(newAllocations).reduce((a, b) => a + b, 0);
    if (total !== 100) {
      // Find the resource with highest value (other than the one being changed) to adjust
      const adjustResource = otherResources.reduce((max, r) =>
        newAllocations[r] > newAllocations[max] ? r : max, otherResources[0]);
      newAllocations[adjustResource] += (100 - total);
      newAllocations[adjustResource] = Math.max(0, Math.min(100, newAllocations[adjustResource]));
    }

    setAllocations(newAllocations);
  };

  // Determine if player is docked at a SpaceDock (has special services like genesis_dealer)
  const isDockedAtSpaceDock = useMemo(() => {
    if (!playerState?.is_docked || !playerState?.current_port_id || !stationsInSector) {
      return false;
    }

    const dockedStation = stationsInSector.find(
      (s: any) => s.id === playerState.current_port_id
    );

    if (!dockedStation) {
      return false;
    }

    // Check for SpaceDock indicators
    const services = dockedStation.services || {};
    return (
      services.genesis_dealer === true ||
      services.ship_dealer === true ||
      dockedStation.type?.toLowerCase() === 'shipyard' ||
      dockedStation.name?.toLowerCase().includes('spacedock') ||
      dockedStation.name?.toLowerCase().includes('tradedock')
    );
  }, [playerState?.is_docked, playerState?.current_port_id, stationsInSector]);
  
  useEffect(() => {
    // Clear results when sector changes
    setMovementResult(null);
    setDockingResult(null);
    setLandingResult(null);
  }, [currentSector?.id]);

  // Auto-dismiss cockpit alerts after 5 seconds
  const movementTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dockingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const landingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (movementTimerRef.current) clearTimeout(movementTimerRef.current);
    if (movementResult) {
      movementTimerRef.current = setTimeout(() => setMovementResult(null), 5000);
    }
    return () => { if (movementTimerRef.current) clearTimeout(movementTimerRef.current); };
  }, [movementResult]);

  useEffect(() => {
    if (dockingTimerRef.current) clearTimeout(dockingTimerRef.current);
    if (dockingResult) {
      dockingTimerRef.current = setTimeout(() => setDockingResult(null), 5000);
    }
    return () => { if (dockingTimerRef.current) clearTimeout(dockingTimerRef.current); };
  }, [dockingResult]);

  useEffect(() => {
    if (landingTimerRef.current) clearTimeout(landingTimerRef.current);
    if (landingResult) {
      landingTimerRef.current = setTimeout(() => setLandingResult(null), 5000);
    }
    return () => { if (landingTimerRef.current) clearTimeout(landingTimerRef.current); };
  }, [landingResult]);


  const handleMove = async (sectorId: number) => {
    try {
      const result = await moveToSector(sectorId);
      setMovementResult(result);
    } catch (error) {
      console.error('Error moving to sector:', error);
    }
  };
  
  const handleDock = async (stationId: string) => {
    try {
      const result = await dockAtStation(stationId);
      setDockingResult(result);
    } catch (error) {
      console.error('Error docking at port:', error);
    }
  };

  const handleLand = async (planetId: string) => {
    try {
      const result = await landOnPlanet(planetId);
      setLandingResult(result);
    } catch (error) {
      console.error('Error landing on planet:', error);
    }
  };

  const handleClaim = async (planetId: string) => {
    try {
      const result = await claimPlanet(planetId);
      setLandingResult(result);
    } catch (error) {
      console.error('Error claiming planet:', error);
    }
  };

  const handleRenamePlanet = async (planetId: string, newName: string) => {
    try {
      await renamePlanet(planetId, newName);
      // Refresh the sector data to show the new name
      await exploreCurrentLocation();
    } catch (error) {
      console.error('Error renaming planet:', error);
      alert('Failed to rename planet. Please try again.');
    }
  };

  const handleLeavePlanet = async () => {
    try {
      const result = await leavePlanet();
      setLandingResult(null); // Clear landing result on successful departure
      setMovementResult({
        message: result.message || 'Successfully departed from planet'
      });
    } catch (error) {
      console.error('Error leaving planet:', error);
    }
  };

  const handleUndock = async () => {
    try {
      await undockFromStation();
    } catch (error) {
      console.error('Error undocking:', error);
    }
  };
  
  // If the player needs to complete the first login experience, the FirstLoginContainer
  // component will be shown by the App component, so we don't need to render the dashboard
  if (requiresFirstLogin) {
    return null;
  }

  return (
    <GameLayout>
      <div className="game-dashboard cockpit-mode">
        {/* System Alerts - Float over cockpit */}
        {error && (
          <div className="cockpit-alert error">
            <div className="alert-header">⚠️ SYSTEM ALERT</div>
            <div className="alert-message">{error}</div>
          </div>
        )}

        {movementResult && (
          <div className="cockpit-alert success">
            <div className="alert-header">✅ NAVIGATION COMPLETE</div>
            <div className="alert-message">{movementResult.message}</div>
            {movementResult.encounters && movementResult.encounters.length > 0 && (
              <div className="encounter-log">
                <div className="log-header">ENCOUNTER LOG:</div>
                <ul className="encounter-list">
                  {movementResult.encounters.map((encounter: any, index: number) => (
                    <li key={`encounter-${encounter.type}-${index}`} className="encounter-item">
                      {encounter.type === 'players' && (
                        <span>
                          👥 PLAYERS DETECTED: {encounter.players.length}
                        </span>
                      )}
                      {encounter.type === 'sector_hazard' && (
                        <span>
                          ⚠️ HAZARD: {encounter.hazard.toUpperCase()} (THREAT: {encounter.threat_level})
                        </span>
                      )}
                      {encounter.type === 'drones' && (
                        <span>
                          🤖 DEFENSE DRONES: {encounter.count} (THREAT: {encounter.threat_level})
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {dockingResult && (
          <div className="cockpit-alert success">
            <div className="alert-header">🚀 DOCKING SUCCESSFUL</div>
            <div className="alert-message">{dockingResult.message}</div>
          </div>
        )}

        {landingResult && (
          <div className="cockpit-alert success">
            <div className="alert-header">🪐 PLANETARY LANDING</div>
            <div className="alert-message">{landingResult.message}</div>
            <div className="landing-details">
              <span>🌍 {landingResult.planet_name}</span>
              <span>🏷️ {landingResult.planet_type?.toUpperCase()}</span>
              <span>👥 Pop: {landingResult.population?.toLocaleString()}</span>
            </div>
          </div>
        )}

        {/* WINDSHIELD - Full immersive viewport with HUD overlays */}
        <div className="cockpit-windshield">
          {/* LANDED STATE - Show planetary surface view */}
          {playerState?.is_landed && !playerState?.is_docked && (() => {
            const landedPlanet = planetsInSector?.find((p: any) => p.id === playerState?.current_planet_id);
            const isMyPlanet = landedPlanet?.owner_id === playerState?.id;

            return (
            <div className="landed-viewport">
              <div className="planet-surface">
                <div className="planet-header">
                  <div className="planet-icon">🪐</div>
                  <h2>LANDED ON PLANET</h2>
                  <p className="planet-name">
                    {landedPlanet?.name || 'Unknown Planet'}
                    {isMyPlanet && (
                      <button
                        className="rename-planet-btn"
                        onClick={() => {
                          const newName = prompt('Enter new planet name:', landedPlanet?.name);
                          if (newName && newName.trim() && newName !== landedPlanet?.name) {
                            handleRenamePlanet(landedPlanet.id, newName.trim());
                          }
                        }}
                        title="Rename your planet"
                      >
                        ✏️
                      </button>
                    )}
                  </p>
                  <p className="planet-type">
                    {landedPlanet?.type?.toUpperCase() || 'UNKNOWN TYPE'}
                  </p>
                  <p className="planet-owner">
                    {isMyPlanet ? (
                      <span className="owner-you">👤 OWNER: YOU</span>
                    ) : landedPlanet?.owner_name ? (
                      <span className="owner-other">👤 OWNER: {landedPlanet.owner_name}</span>
                    ) : landedPlanet?.owner_id ? (
                      <span className="owner-other">👤 OWNED</span>
                    ) : (
                      <span className="owner-unclaimed">○ UNCLAIMED</span>
                    )}
                  </p>
                </div>
                <div className="planetary-surface-visual">
                  <div className="surface-lights">
                    <span className="light green"></span>
                    <span className="light green"></span>
                    <span className="light green"></span>
                  </div>
                  <div className="surface-message">LANDING GEAR DEPLOYED</div>
                  <div className="surface-status">
                    Surface operations available - Manage colony, collect resources, or load colonists
                  </div>
                  {landedPlanet && (
                    <div className="planet-stats">
                      <div className="stat-row">
                        <span className="stat-label">Population:</span>
                        <span className="stat-value">{landedPlanet.population?.toLocaleString() || 0}</span>
                      </div>
                      <div className="stat-row">
                        <span className="stat-label">Habitability:</span>
                        <span className="stat-value">{landedPlanet.habitability_score || 0}%</span>
                      </div>
                      <div className="stat-row">
                        <span className="stat-label">Status:</span>
                        <span className="stat-value">{landedPlanet.status?.toUpperCase() || 'UNKNOWN'}</span>
                      </div>
                    </div>
                  )}
                </div>
                <button className="liftoff-button" onClick={handleLeavePlanet}>
                  🚀 LIFT OFF &amp; DEPART
                </button>
              </div>
            </div>
            );
          })()}

          {/* DOCKED STATE - Show station interior view */}
          {playerState?.is_docked && (
            <div className="docked-viewport">
              <div className={`station-interior ${isDockedAtSpaceDock ? 'spacedock' : ''}`}>
                <div className="station-header">
                  <div className="station-icon">{isDockedAtSpaceDock ? '🚀' : '🏪'}</div>
                  <h2>{isDockedAtSpaceDock ? 'DOCKED AT SPACEDOCK' : 'DOCKED AT STATION'}</h2>
                  <p className="station-name">
                    {stationsInSector?.find((s: any) => s.id === playerState?.current_port_id)?.name ||
                      stationsInSector?.[0]?.name ||
                      (isDockedAtSpaceDock ? 'SpaceDock' : 'Trading Station')}
                  </p>
                </div>
                <div className="docking-bay-visual">
                  <div className="bay-lights">
                    <span className={`light ${isDockedAtSpaceDock ? 'blue' : 'green'}`}></span>
                    <span className={`light ${isDockedAtSpaceDock ? 'blue' : 'green'}`}></span>
                    <span className={`light ${isDockedAtSpaceDock ? 'blue' : 'green'}`}></span>
                  </div>
                  <div className="bay-message">DOCKING CLAMPS ENGAGED</div>
                  <div className="bay-status">
                    {isDockedAtSpaceDock
                      ? 'Welcome to SpaceDock - Access shipyard, armory, and genesis store below'
                      : 'All systems nominal - Ready for trading operations'}
                  </div>
                </div>
                <button className="undock-button" onClick={handleUndock}>
                  🚀 UNDOCK &amp; LAUNCH
                </button>
              </div>
            </div>
          )}

          {/* SPACE VIEW - Normal flight mode */}
          {!playerState?.is_docked && !playerState?.is_landed && currentSector && (
            <>
              {/* Space viewport - edge to edge */}
              <SectorViewport
                sectorType={currentSector.type?.toLowerCase() || 'normal'}
                sectorName={currentSector.name}
                hazardLevel={currentSector.hazard_level}
                radiationLevel={currentSector.radiation_level}
                stations={stationsInSector}
                planets={planetsInSector}
                width={Math.floor(window.innerWidth - 320)}
                height={Math.floor((window.innerHeight - 80) * 0.40)}
                onEntityClick={(entity) => {
                  if (entity.type === 'planet') {
                    handleLand(entity.id);
                  } else if (entity.type === 'station') {
                    handleDock(entity.id);
                  }
                }}
              />

              {/* Cockpit frame vignette */}
              <div className="cockpit-frame">
                <div className="frame-corner top-left"></div>
                <div className="frame-corner top-right"></div>
                <div className="frame-corner bottom-left"></div>
                <div className="frame-corner bottom-right"></div>
              </div>

              {/* HUD Overlays */}
              <div className="hud-overlay top-left">
                <div className="hud-label">LOCATION</div>
                <div className="hud-value">
                  {currentSector.region_name && currentSector.region_name.toUpperCase()}
                  {currentSector.region_name && ' - '}
                  SECTOR {currentSector.sector_number || currentSector.sector_id}
                </div>
                <div className="hud-value-secondary">
                  {currentSector.type ? currentSector.type.replace(/_/g, ' ').toUpperCase() : 'STANDARD'}
                </div>
                {playerState && (
                  <div className="hud-pilot">
                    <span className="status-indicator online"></span>
                    <span style={{ color: playerState.name_color || '#FFFFFF' }}>
                      {playerState.military_rank.toUpperCase()} {playerState.username.toUpperCase()}
                    </span>
                    <div className="hud-reputation-tier" style={{ fontSize: '0.7em', opacity: 0.8 }}>
                      {playerState.reputation_tier} ({playerState.personal_reputation >= 0 ? '+' : ''}{playerState.personal_reputation})
                    </div>
                  </div>
                )}
              </div>

              {currentSector.hazard_level > 0 && (
                <div className="hud-overlay top-right hazard">
                  <div className="hud-label">⚠️ HAZARD</div>
                  <div className="hud-value danger">{currentSector.hazard_level}/10</div>
                  <div className="hud-bar">
                    <div className="hud-bar-fill danger" style={{ width: `${currentSector.hazard_level * 10}%` }}></div>
                  </div>
                </div>
              )}

              {currentSector.radiation_level > 0 && (
                <div className="hud-overlay bottom-right radiation">
                  <div className="hud-label">☢️ RADIATION</div>
                  <div className="hud-value warning">{(currentSector.radiation_level * 100).toFixed(1)}%</div>
                  <div className="hud-bar">
                    <div className="hud-bar-fill warning" style={{ width: `${currentSector.radiation_level * 100}%` }}></div>
                  </div>
                </div>
              )}

              {currentSector.special_features && currentSector.special_features.length > 0 && (
                <div className="hud-overlay bottom-left features" style={{ display: 'none' }}>
                  <div className="hud-label">ANOMALIES</div>
                  <div className="hud-features">
                    {currentSector.special_features.map(feature => (
                      <span key={feature} className="hud-badge">
                        {feature.replace(/_/g, ' ').toUpperCase()}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {currentSector.description && (
                <div className="hud-overlay bottom-center description" style={{ display: 'none' }}>
                  <div className="hud-description-text">{currentSector.description}</div>
                </div>
              )}
            </>
          )}
        </div>

        {/* CONSOLE - Metal panel with embedded monitors */}
        <div className="cockpit-console">
          {/* DOCKED STATE: Show SpaceDock or Trading Interface */}
          {playerState?.is_docked ? (
            <div className="console-monitor trading-monitor full-width">
              <div className="monitor-bezel">
                <div className="bezel-corner tl"></div>
                <div className="bezel-corner tr"></div>
                <div className="bezel-corner bl"></div>
                <div className="bezel-corner br"></div>
              </div>
              <div className="monitor-screen">
                <div className="screen-hud-header">
                  {isDockedAtSpaceDock ? 'SPACEDOCK TERMINAL' : 'TRADING TERMINAL'}
                </div>
                <div className="screen-hud-content trading-content">
                  {isDockedAtSpaceDock ? <SpaceDockInterface /> : <TradingInterface onClose={() => {}} />}
                </div>
              </div>
            </div>
          ) : playerState?.is_landed ? (
            /* LANDED STATE: Show Comprehensive Planetary Operations Terminal */
            <div className="console-monitor planetary-ops-monitor full-width">
              <div className="monitor-bezel">
                <div className="bezel-corner tl"></div>
                <div className="bezel-corner tr"></div>
                <div className="bezel-corner bl"></div>
                <div className="bezel-corner br"></div>
              </div>
              <div className="monitor-screen">
                <div className="screen-hud-header">PLANETARY OPERATIONS COMMAND</div>
                <div className="screen-hud-content planetary-ops-content">
                  {(() => {
                    const currentPlanet = planetsInSector?.find((p: any) => p.id === playerState?.current_planet_id);

                    // Citadel level names and population caps
                    const citadelLevels = [
                      { name: 'Outpost', maxPop: 1000, storage: 1000, drones: 10 },
                      { name: 'Settlement', maxPop: 5000, storage: 5000, drones: 25 },
                      { name: 'Colony', maxPop: 15000, storage: 15000, drones: 50 },
                      { name: 'Major Colony', maxPop: 50000, storage: 50000, drones: 100 },
                      { name: 'Planetary Capital', maxPop: 200000, storage: 150000, drones: 200 }
                    ];

                    // Mock data for display (backend will provide real data)
                    const citadelLevel = (currentPlanet as any)?.citadel_level || 1;
                    const citadelInfo = citadelLevels[Math.min(citadelLevel - 1, 4)];
                    const population = currentPlanet?.population || 0;
                    const shieldLevel = (currentPlanet as any)?.shield_level || 0;
                    const shieldStrength = [0, 1000, 2500, 5000, 10000, 15000, 20000, 30000, 40000, 50000, 75000][shieldLevel] || 0;
                    const droneCount = (currentPlanet as any)?.drones || 0;
                    const defenseRating = Math.min(100, Math.floor((shieldLevel * 5) + (droneCount / 2) + (citadelLevel * 10)));

                    // Planet type icons
                    const planetTypeIcons: Record<string, string> = {
                      'TERRA': '🌍', 'M_CLASS': '🌎', 'terran': '🌍', 'oceanic': '🌊',
                      'L_CLASS': '🏔️', 'mountainous': '🏔️', 'O_CLASS': '🌊',
                      'K_CLASS': '🏜️', 'desert': '🏜️', 'H_CLASS': '🌋', 'volcanic': '🌋',
                      'D_CLASS': '🌑', 'barren': '🌑', 'C_CLASS': '❄️', 'frozen': '❄️',
                      'ice': '🧊', 'jungle': '🌴', 'gas_giant': '🪐'
                    };
                    const planetIcon = planetTypeIcons[currentPlanet?.type?.toLowerCase() || ''] || '🪐';

                    // Terraform values for header
                    const terraformLevel = (currentPlanet as any)?.terraform_level || 0;
                    const terraformBonuses = [0, 5, 15, 30, 50, 75];
                    const terraformNames = ['Barren', 'Stabilized', 'Atmospheric', 'Regulated', 'Engineered', 'Paradise'];
                    const terraformDescs = [
                      'Unmodified planet surface',
                      'Basic life support active',
                      'Breathable atmosphere',
                      'Climate controlled zones',
                      'Fully terraformed biomes',
                      'Perfect living conditions'
                    ];
                    const isTerraMaxed = terraformLevel >= 5;

                    return (
                      <div className="planet-ui">
                        {/* Header with planet name, terraform, and key stats */}
                        <div className="planet-header">
                          <div className="planet-title">
                            <span className="planet-icon-lg">{planetIcon}</span>
                            <div className="planet-name-block">
                              <span className="planet-name">{currentPlanet?.name || 'Unknown Planet'}</span>
                              <span className="planet-meta">{currentPlanet?.type?.toUpperCase().replace('_', ' ') || 'UNKNOWN'} • Hab: {currentPlanet?.habitability_score || 0}%</span>
                            </div>
                          </div>
                          <div className="header-terraform">
                            <div className="header-terra-top">
                              <span className="header-terra-icon">🌱</span>
                              <span className="header-terra-name">{terraformNames[terraformLevel]}</span>
                              <span className="header-terra-level">L{terraformLevel}</span>
                              {isTerraMaxed && <span className="paradise-badge">✨</span>}
                            </div>
                            <div className="header-terra-bar">
                              {[0, 1, 2, 3, 4].map((level) => (
                                <div
                                  key={level}
                                  className={`header-terra-seg ${level < terraformLevel ? 'filled' : ''} ${level === terraformLevel && !isTerraMaxed ? 'current' : ''}`}
                                />
                              ))}
                            </div>
                            <div className="header-terra-desc">{terraformDescs[terraformLevel]}</div>
                            <div className="header-terra-bonus">+{terraformBonuses[terraformLevel]}% Growth</div>
                          </div>
                          <div className="planet-stats">
                            <div className="stat"><span className="label">Population</span><span className="value green">{population.toLocaleString()}</span></div>
                            <div className="stat"><span className="label">Defense</span><span className="value">{defenseRating}%</span></div>
                          </div>
                        </div>

                        {/* Main content: 2 columns on top, full-width safe at bottom */}
                        <div className="planet-content">
                          {/* Top Row: Defense & Production side by side */}
                          <div className="planet-top-row">
                            <div className="planet-section defense">
                              <h4>🛡️ Citadel Defense Systems</h4>
                              <div className="citadel-banner">
                                <span className="citadel-icon">🏰</span>
                                <span className="citadel-name">{citadelInfo.name}</span>
                                <span className="citadel-level">Level {citadelLevel}</span>
                              </div>
                              <div className="defense-grid">
                                <div className="defense-item"><span>🛡️</span><span>{shieldStrength.toLocaleString()}</span><span className="sublabel">Shields L{shieldLevel}</span></div>
                                <div className="defense-item"><span>🤖</span><span>{droneCount} / {citadelInfo.drones}</span><span className="sublabel">Drones</span></div>
                                <div className="defense-item"><span>🔫</span><span>{citadelLevel >= 2 ? (citadelLevel * 4) : 0}</span><span className="sublabel">Turrets</span></div>
                              </div>
                              <div className="section-actions">
                                <button className="section-btn" disabled>🛡️ Upgrade Shields</button>
                                <button className="section-btn" disabled>🤖 Deploy Drones</button>
                                <button className="section-btn upgrade" disabled>🏗️ Upgrade Citadel</button>
                              </div>
                            </div>
                            <div className="planet-section production">
                              <h4>👥 Population & Production</h4>

                              {/* Colonist Transfer */}
                              <div className="colonist-transfer">
                                <div className="transfer-info">
                                  <span className="pop-count">{Math.floor(population / 1000)}K</span>
                                  <span className="pop-label">Population</span>
                                </div>
                                <div className="transfer-actions">
                                  <button className="transfer-btn disembark" disabled title="Ship → Planet">
                                    <span>📥</span> Disembark
                                  </button>
                                  <button className="transfer-btn embark" disabled title="Planet → Ship">
                                    <span>📤</span> Embark
                                  </button>
                                </div>
                                <div className="transfer-note">1 cargo = 1,000 colonists</div>
                              </div>

                              {/* Production Allocation Sliders */}
                              <div className="allocation-section">
                                <div className="allocation-header">Production Allocation</div>
                                <div className="allocation-sliders">
                                  <div className="alloc-row">
                                    <span className="alloc-icon">⛽</span>
                                    <span className="alloc-name">Fuel</span>
                                    <input
                                      type="range"
                                      min="0"
                                      max="100"
                                      value={allocations.fuel}
                                      onChange={(e) => handleAllocationChange('fuel', parseInt(e.target.value))}
                                      className="alloc-slider fuel"
                                    />
                                    <span className="alloc-pct">{allocations.fuel}%</span>
                                    <span className="alloc-rate">+{Math.floor(population * (allocations.fuel / 100) * 0.01)}/hr</span>
                                  </div>
                                  <div className="alloc-row">
                                    <span className="alloc-icon">🌿</span>
                                    <span className="alloc-name">Organics</span>
                                    <input
                                      type="range"
                                      min="0"
                                      max="100"
                                      value={allocations.organics}
                                      onChange={(e) => handleAllocationChange('organics', parseInt(e.target.value))}
                                      className="alloc-slider organics"
                                    />
                                    <span className="alloc-pct">{allocations.organics}%</span>
                                    <span className="alloc-rate">+{Math.floor(population * (allocations.organics / 100) * 0.01)}/hr</span>
                                  </div>
                                  <div className="alloc-row">
                                    <span className="alloc-icon">⚙️</span>
                                    <span className="alloc-name">Equipment</span>
                                    <input
                                      type="range"
                                      min="0"
                                      max="100"
                                      value={allocations.equipment}
                                      onChange={(e) => handleAllocationChange('equipment', parseInt(e.target.value))}
                                      className="alloc-slider equipment"
                                    />
                                    <span className="alloc-pct">{allocations.equipment}%</span>
                                    <span className="alloc-rate">+{Math.floor(population * (allocations.equipment / 100) * 0.01)}/hr</span>
                                  </div>
                                  <div className="alloc-row">
                                    <span className="alloc-icon">🪨</span>
                                    <span className="alloc-name">Ore</span>
                                    <input
                                      type="range"
                                      min="0"
                                      max="100"
                                      value={allocations.ore}
                                      onChange={(e) => handleAllocationChange('ore', parseInt(e.target.value))}
                                      className="alloc-slider ore"
                                    />
                                    <span className="alloc-pct">{allocations.ore}%</span>
                                    <span className="alloc-rate">+{Math.floor(population * (allocations.ore / 100) * 0.01)}/hr</span>
                                  </div>
                                  <div className="alloc-row">
                                    <span className="alloc-icon">🌱</span>
                                    <span className="alloc-name">Terraform</span>
                                    <input
                                      type="range"
                                      min="0"
                                      max="100"
                                      value={allocations.terraform}
                                      onChange={(e) => handleAllocationChange('terraform', parseInt(e.target.value))}
                                      className="alloc-slider terraform"
                                    />
                                    <span className="alloc-pct">{allocations.terraform}%</span>
                                    <span className="alloc-rate">+{Math.floor(population * (allocations.terraform / 100) * 0.01)}/hr</span>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>

                          {/* Bottom Row: Full-width Citadel Safe */}
                          <div className="planet-section storage full-width">
                            <div className="safe-header">
                              <h4>🔐 Citadel Safe</h4>
                              <div className="safe-credits">
                                <span className="credits-label">💰</span>
                                <span className="credits-value">0</span>
                                <span className="credits-text">credits</span>
                              </div>
                              <span className="safe-cap">{[100000, 500000, 2000000, 10000000, 50000000][citadelLevel - 1]?.toLocaleString()} units</span>
                              <div className="safe-header-actions">
                                <button className="safe-btn deposit" disabled>📥 Deposit</button>
                                <button className="safe-btn withdraw" disabled>📤 Withdraw</button>
                              </div>
                            </div>
                            <div className="safe-grid-horizontal">
                              {/* Core Commodities */}
                              <div className="safe-item"><span>⛽</span><span>0</span><span className="item-label">Fuel</span></div>
                              <div className="safe-item"><span>🌿</span><span>0</span><span className="item-label">Organics</span></div>
                              <div className="safe-item"><span>⚙️</span><span>0</span><span className="item-label">Equipment</span></div>
                              <div className="safe-item"><span>🪨</span><span>0</span><span className="item-label">Ore</span></div>
                              {/* Luxury Commodities */}
                              <div className="safe-item luxury"><span>💎</span><span>0</span><span className="item-label">Luxury</span></div>
                              <div className="safe-item luxury"><span>🍷</span><span>0</span><span className="item-label">Gourmet</span></div>
                              <div className="safe-item luxury"><span>🔬</span><span>0</span><span className="item-label">Exotic Tech</span></div>
                              {/* Strategic Resources */}
                              <div className="safe-item quantum"><span>💠</span><span>0</span><span className="item-label">Q.Shards</span></div>
                              <div className="safe-item quantum"><span>🔮</span><span>0</span><span className="item-label">Q.Crystal</span></div>
                              {/* Rare Materials */}
                              <div className="safe-item rare"><span>✨</span><span>0</span><span className="item-label">Prismatic</span></div>
                              <div className="safe-item rare"><span>💫</span><span>0</span><span className="item-label">Photonic</span></div>
                              {/* Military Equipment */}
                              <div className="safe-item military"><span>🚀</span><span>0</span><span className="item-label">Fighters</span></div>
                              <div className="safe-item military"><span>⚔️</span><span>0</span><span className="item-label">Atk Drones</span></div>
                              <div className="safe-item military"><span>🛡️</span><span>0</span><span className="item-label">Def Drones</span></div>
                              <div className="safe-item military"><span>💣</span><span>0</span><span className="item-label">Mines</span></div>
                              {/* Special Items */}
                              <div className="safe-item genesis"><span>🌍</span><span>0</span><span className="item-label">Genesis</span></div>
                            </div>
                          </div>
                        </div>

                      </div>
                    );
                  })()}
                </div>
              </div>
            </div>
          ) : (
            <>
              {/* LEFT MONITOR: Navigation */}
              <div className="console-monitor nav-monitor">
                <div className="monitor-bezel">
                  <div className="bezel-corner tl"></div>
                  <div className="bezel-corner tr"></div>
                  <div className="bezel-corner bl"></div>
                  <div className="bezel-corner br"></div>
                </div>
                <div className="monitor-screen">
                  <div className="screen-hud-header">NAV</div>
                  <div className="screen-hud-content">
                  {currentSector && (
                    <NavigationMap
                      currentSectorId={currentSector.sector_id}
                      sectors={[
                        // Current sector
                        {
                          id: currentSector.sector_id,
                          name: `Sector ${currentSector.sector_number || currentSector.sector_id}`,
                          type: currentSector.type,
                          connected_sectors: [
                            ...availableMoves.warps.map(w => w.sector_id),
                            ...availableMoves.tunnels.map(t => t.sector_id)
                          ]
                        },
                        // Available warp destinations
                        ...availableMoves.warps.map(warp => {
                          // Show region name if different from current region
                          const showRegion = warp.region_id && warp.region_id !== currentSector.region_id;
                          const displayName = showRegion
                            ? `${warp.region_name} - Sector ${warp.sector_number || warp.sector_id}`
                            : `Sector ${warp.sector_number || warp.sector_id}`;

                          return {
                            id: warp.sector_id,
                            name: displayName,
                            type: warp.type,
                            connected_sectors: [currentSector.sector_id]
                          };
                        }),
                        // Available tunnel destinations
                        ...availableMoves.tunnels.map(tunnel => {
                          // Show region name if different from current region
                          const showRegion = tunnel.region_id && tunnel.region_id !== currentSector.region_id;
                          const displayName = showRegion
                            ? `${tunnel.region_name} - Sector ${tunnel.sector_number || tunnel.sector_id}`
                            : `Sector ${tunnel.sector_number || tunnel.sector_id}`;

                          return {
                            id: tunnel.sector_id,
                            name: displayName,
                            type: 'nebula',
                            connected_sectors: [currentSector.sector_id]
                          };
                        })
                      ]}
                      availableMoves={[
                        ...availableMoves.warps.filter(w => w.can_afford).map(w => w.sector_id),
                        ...availableMoves.tunnels.filter(t => t.can_afford).map(t => t.sector_id)
                      ]}
                      onNavigate={handleMove}
                      width={440}
                      height={300}
                    />
                  )}
                  </div>
                </div>
              </div>

              {/* CENTER MONITOR: Planetary Systems */}
              <div className="console-monitor planetary-monitor">
                <div className="monitor-bezel">
                  <div className="bezel-corner tl"></div>
                  <div className="bezel-corner tr"></div>
                  <div className="bezel-corner bl"></div>
                  <div className="bezel-corner br"></div>
                </div>
                <div className="monitor-screen">
                  <div className="screen-hud-header">PLANETARY</div>
                  <div className="screen-hud-content">
                  {/* Show planets paired with stations (by index) */}
                  {planetsInSector.map((planet, index) => (
                    <PlanetPortPair
                      key={planet.id}
                      planet={planet}
                      station={stationsInSector?.[index] || null}
                      onLandOnPlanet={handleLand}
                      onClaimPlanet={handleClaim}
                      onDockAtStation={handleDock}
                      isLanded={playerState?.is_landed || false}
                      isDocked={playerState?.is_docked || false}
                    />
                  ))}
                  {/* Show any extra stations not paired with planets */}
                  {stationsInSector.slice(planetsInSector.length).map((station) => (
                    <PlanetPortPair
                      key={station.id}
                      planet={null}
                      station={station}
                      onLandOnPlanet={handleLand}
                      onDockAtStation={handleDock}
                      isLanded={playerState?.is_landed || false}
                      isDocked={playerState?.is_docked || false}
                    />
                  ))}
                  {/* Empty state when neither planets nor stations */}
                  {planetsInSector.length === 0 && stationsInSector.length === 0 && (
                    <div className="empty-state">No planetary bodies or stations detected</div>
                  )}
                  </div>
                </div>
              </div>

              {/* RIGHT MONITOR: Contacts */}
              <div className="console-monitor comms-monitor">
                <div className="monitor-bezel">
                  <div className="bezel-corner tl"></div>
                  <div className="bezel-corner tr"></div>
                  <div className="bezel-corner bl"></div>
                  <div className="bezel-corner br"></div>
                </div>
                <div className="monitor-screen">
                  <div className="screen-hud-header">COMMS</div>
                  <div className="screen-hud-content">
                  {sectorPlayers.length > 0 ? (
                    <div className="contacts-compact-list">
                      {sectorPlayers.map((player: any) => (
                        <div key={player.user_id} className="contact-list-item">
                          <span className="status-indicator online"></span>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                            <span
                              className="contact-list-name"
                              style={{ color: player.name_color || '#FFFFFF' }}
                            >
                              {player.military_rank ? `${player.military_rank.toUpperCase()} ` : ''}
                              {player.username}
                            </span>
                            <span style={{ fontSize: '0.7em', opacity: 0.7 }}>
                              {player.reputation_tier || 'Neutral'} ({player.personal_reputation >= 0 ? '+' : ''}{player.personal_reputation || 0})
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="empty-state">No signals detected</div>
                  )}
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Enhanced AI Assistant - ARIA */}
        {playerState?.id && (
          <EnhancedAIAssistant
            theme="dark"
          />
        )}
      </div>
    </GameLayout>
  );
};

export default GameDashboard;
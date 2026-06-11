import React, { useEffect, useRef, useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useGame } from '../../contexts/GameContext';
import { useFirstLogin } from '../../contexts/FirstLoginContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
// import { useTheme } from '../../themes/ThemeProvider'; // Available for future use
import GameLayout from '../layouts/GameLayout';
import TradingInterface from '../trading/TradingInterface';
import SpaceDockInterface from '../spacedock/SpaceDockInterface';
import PortOfficeVenue from '../spacedock/PortOfficeVenue';
import TacticalCard from '../tactical/TacticalCard';
import SectorViewport from '../tactical/SectorViewport';
import PlanetPortPair from '../tactical/PlanetPortPair';
import NavigationMap from '../tactical/NavigationMap';
import './game-dashboard.css';
import './cockpit.css';
import '../tactical/tactical-layout.css';

// Planet type icons (shared by the landed console and the claim ceremony)
const PLANET_TYPE_ICONS: Record<string, string> = {
  'terra': '🌍', 'm_class': '🌎', 'terran': '🌍', 'oceanic': '🌊',
  'l_class': '🏔️', 'mountainous': '🏔️', 'o_class': '🌊',
  'k_class': '🏜️', 'desert': '🏜️', 'h_class': '🌋', 'volcanic': '🌋',
  'd_class': '🌑', 'barren': '🌑', 'c_class': '❄️', 'frozen': '❄️',
  'ice': '🧊', 'jungle': '🌴', 'gas_giant': '🪐'
};

const getPlanetIcon = (type?: string): string =>
  PLANET_TYPE_ICONS[type?.toLowerCase() || ''] || '🪐';

// One accent class per planet type — the colors live in cockpit.css
const getPlanetTintClass = (type?: string): string =>
  `planet-tint-${(type || 'unknown').toLowerCase().replace(/[^a-z_]+/g, '_')}`;

const GameDashboard: React.FC = () => {
  const {
    playerState,
    currentShip,
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
    getPlanetDetails,
    transferColonists,
    exploreCurrentLocation,
    getAvailableMoves,
    refreshPlayerState,
    error
  } = useGame();
  
  const { requiresFirstLogin } = useFirstLogin();
  const { sectorPlayers, isConnected } = useWebSocket();

  const [movementResult, setMovementResult] = useState<any>(null);
  const [dockingResult, setDockingResult] = useState<any>(null);
  const [landingResult, setLandingResult] = useState<any>(null);

  // Docked trading-station terminal: trade desk or the Port Office registry.
  // SpaceDocks/TradeDocks reach the Port Office through their own venue hub.
  const [stationTerminal, setStationTerminal] = useState<'trade' | 'portoffice'>('trade');
  useEffect(() => {
    setStationTerminal('trade');
  }, [playerState?.current_port_id]);

  // NAV scan loading state: show a spinner while sector telemetry loads,
  // then fall back to a retry affordance if nothing arrives within 10s.
  const [navScanTimedOut, setNavScanTimedOut] = useState(false);
  const [navScanAttempt, setNavScanAttempt] = useState(0);

  useEffect(() => {
    if (currentSector) {
      setNavScanTimedOut(false);
      return;
    }
    const timer = setTimeout(() => setNavScanTimedOut(true), 10000);
    return () => clearTimeout(timer);
  }, [currentSector, navScanAttempt]);

  const handleRetryScan = async () => {
    setNavScanTimedOut(false);
    setNavScanAttempt(attempt => attempt + 1); // re-arm the 10s timeout
    await Promise.allSettled([exploreCurrentLocation(), getAvailableMoves()]);
  };

  // COMMS contacts: merge live WebSocket presence with the sector snapshot
  // from the API (players_present), excluding ourselves, de-duplicated.
  const sectorContacts = useMemo(() => {
    const contacts = new Map<string, any>();
    const addContact = (contact: any) => {
      if (!contact) return;
      const key = String(contact.user_id || contact.id || contact.username || '');
      if (!key) return;
      const isSelf = playerState && (
        key === String(playerState.id) ||
        (contact.username && contact.username === playerState.username)
      );
      if (isSelf) return;
      if (!contacts.has(key)) contacts.set(key, contact);
    };
    sectorPlayers.forEach(addContact);
    (currentSector?.players_present || []).forEach(addContact);
    return Array.from(contacts.values());
  }, [sectorPlayers, currentSector?.players_present, playerState]);

  // Landed planet — used by the windshield viewport and the colonist transfer UI
  const landedPlanet = useMemo(() => (
    playerState?.is_landed
      ? planetsInSector?.find((p: any) => p.id === playerState?.current_planet_id) || null
      : null
  ), [playerState?.is_landed, playerState?.current_planet_id, planetsInSector]);

  const isLandedPlanetMine = !!(landedPlanet && playerState && landedPlanet.owner_id === playerState.id);

  // Colonists riding in the current ship's cargo.
  // Cargo shape from /player/ships is {used, capacity, contents: {colonists: N}}
  // (the legacy flat shape is kept as a fallback).
  const shipColonists = useMemo(() => {
    const cargo = currentShip?.cargo as any;
    const aboard = cargo?.contents?.colonists ?? cargo?.colonists;
    return typeof aboard === 'number' && aboard > 0 ? Math.floor(aboard) : 0;
  }, [currentShip]);

  // Detailed planet data (colonists / maxColonists) — the detail endpoint only
  // answers for planets the player owns, so render gracefully when absent
  const [landedPlanetDetail, setLandedPlanetDetail] = useState<any>(null);
  useEffect(() => {
    let cancelled = false;
    if (!landedPlanet || !isLandedPlanetMine) {
      setLandedPlanetDetail(null);
      return;
    }
    getPlanetDetails(landedPlanet.id)
      .then((detail: any) => { if (!cancelled) setLandedPlanetDetail(detail); })
      .catch(() => { if (!cancelled) setLandedPlanetDetail(null); });
    return () => { cancelled = true; };
  }, [landedPlanet?.id, isLandedPlanetMine]);

  // Colonists on the landed planet (detail when owned, sector snapshot otherwise)
  const landedPlanetColonists: number =
    typeof landedPlanetDetail?.colonists === 'number'
      ? landedPlanetDetail.colonists
      : landedPlanet?.population || 0;

  // --- Colonist transfer modal (quantity pattern mirrors the trading modal) ---
  const [transferModal, setTransferModal] = useState<'disembark' | 'embark' | null>(null);
  const [transferQuantity, setTransferQuantity] = useState(1);
  const [isTransferring, setIsTransferring] = useState(false);
  const [transferNotice, setTransferNotice] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const transferMax = useMemo(() => {
    if (transferModal === 'disembark') {
      let max = shipColonists;
      if (typeof landedPlanetDetail?.maxColonists === 'number' && typeof landedPlanetDetail?.colonists === 'number') {
        max = Math.min(max, Math.max(0, landedPlanetDetail.maxColonists - landedPlanetDetail.colonists));
      }
      return max;
    }
    if (transferModal === 'embark') {
      return landedPlanetColonists;
    }
    return 0;
  }, [transferModal, shipColonists, landedPlanetDetail, landedPlanetColonists]);

  // Default the modal to a full load when it opens
  useEffect(() => {
    if (transferModal) setTransferQuantity(Math.max(1, transferMax));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transferModal]);

  // Auto-dismiss transfer notices
  useEffect(() => {
    if (!transferNotice) return;
    const timer = setTimeout(() => setTransferNotice(null), 8000);
    return () => clearTimeout(timer);
  }, [transferNotice]);

  const clampTransferQuantity = (value: number) =>
    Math.max(1, Math.min(Math.max(1, transferMax), value));

  const openTransferModal = (action: 'disembark' | 'embark') => {
    setTransferNotice(null);
    setTransferModal(action);
  };

  const handleTransferConfirm = async () => {
    if (!landedPlanet || !transferModal || transferQuantity < 1 || isTransferring) return;
    const action = transferModal;
    setIsTransferring(true);
    try {
      const result = await transferColonists(landedPlanet.id, action, transferQuantity);
      setTransferNotice({
        type: 'success',
        message: result?.message || (action === 'disembark'
          ? `${transferQuantity.toLocaleString()} colonists disembarked to ${landedPlanet.name}`
          : `${transferQuantity.toLocaleString()} colonists embarked from ${landedPlanet.name}`)
      });
      // Sync the detail panel from the authoritative server response
      setLandedPlanetDetail((prev: any) => prev ? {
        ...prev,
        colonists: typeof result?.planet_colonists === 'number' ? result.planet_colonists : prev.colonists,
        maxColonists: typeof result?.max_colonists === 'number' ? result.max_colonists : prev.maxColonists
      } : prev);
      setTransferModal(null);
    } catch (error: any) {
      setTransferNotice({
        type: 'error',
        message: error?.response?.data?.detail || error?.response?.data?.message || 'Colonist transfer failed'
      });
      setTransferModal(null);
    } finally {
      setIsTransferring(false);
    }
  };

  // --- Claim ceremony / refusal notice ---
  const [claimCelebration, setClaimCelebration] = useState<{
    planetName: string;
    planetType: string;
    colonistsSettled?: number;
    creditsSpent?: number;
  } | null>(null);
  const [claimNotice, setClaimNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!claimCelebration) return;
    const timer = setTimeout(() => setClaimCelebration(null), 10000);
    return () => clearTimeout(timer);
  }, [claimCelebration]);

  useEffect(() => {
    if (!claimNotice) return;
    const timer = setTimeout(() => setClaimNotice(null), 10000);
    return () => clearTimeout(timer);
  }, [claimNotice]);

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
      // Full station: dockAtStation surfaces the 409 payload as
      // {full: true, detail, queue_position, ...} — we were auto-enqueued,
      // NOT docked. Don't render the success banner for it.
      if (result?.full) {
        setDockingResult({
          full: true,
          message:
            (result.detail || 'All docking slips are occupied.') +
            (result.queue_position
              ? ` You are #${result.queue_position} in the docking queue.`
              : ''),
        });
        return;
      }
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
    setClaimNotice(null);
    // Capture the planet before the claim refreshes the sector snapshot
    const planet = planetsInSector?.find((p: any) => p.id === planetId);
    try {
      const result = await claimPlanet(planetId);
      setClaimCelebration({
        planetName: result?.planet_name || planet?.name || 'Unknown Planet',
        planetType: result?.planet_type || planet?.type || '',
        colonistsSettled: typeof result?.colonists_settled === 'number' ? result.colonists_settled : undefined,
        creditsSpent: typeof result?.credits_spent === 'number' ? result.credits_spent : undefined
      });
    } catch (error: any) {
      console.error('Error claiming planet:', error);
      // 403 carries the population-hub refusal fiction; 400 carries the
      // requirements message — show the server's words inline. Other
      // failures (500/network) already surface via the global cockpit
      // error alert; don't double-display them here.
      const statusCode = error?.response?.status;
      if (statusCode === 400 || statusCode === 403) {
        const detail = error?.response?.data?.detail || error?.response?.data?.message;
        setClaimNotice(typeof detail === 'string' && detail
          ? detail
          : 'Claim refused — 10,000 credits and at least 100 colonists aboard are required.');
      }
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

        {claimNotice && (
          <div className="cockpit-alert claim-denied" role="alert">
            <div className="alert-header">
              <span>🛑 CLAIM DENIED</span>
              <button
                className="alert-dismiss"
                onClick={() => setClaimNotice(null)}
                aria-label="Dismiss claim notice"
              >
                ×
              </button>
            </div>
            <div className="alert-message">{claimNotice}</div>
          </div>
        )}

        {claimCelebration && (
          <div
            className="claim-celebration-overlay"
            role="dialog"
            aria-label="Colony established"
            onClick={() => setClaimCelebration(null)}
          >
            <div className="claim-celebration-card">
              <div className="claim-scanline" aria-hidden="true"></div>
              <div className="claim-banner">🏴 COLONY ESTABLISHED</div>
              <div className="claim-planet-icon">{getPlanetIcon(claimCelebration.planetType)}</div>
              <div className="claim-planet-name">{claimCelebration.planetName}</div>
              {typeof claimCelebration.colonistsSettled === 'number' && (
                <div className="claim-detail">👥 {claimCelebration.colonistsSettled.toLocaleString()} colonists settled</div>
              )}
              {typeof claimCelebration.creditsSpent === 'number' && (
                <div className="claim-detail">💰 {claimCelebration.creditsSpent.toLocaleString()} credits invested</div>
              )}
              <div className="claim-dismiss-hint">Click anywhere to continue</div>
            </div>
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
          <div className={`cockpit-alert ${dockingResult.full ? 'claim-denied' : 'success'}`}>
            <div className="alert-header">
              {dockingResult.full ? '🛑 DOCKING QUEUE' : '🚀 DOCKING SUCCESSFUL'}
            </div>
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
            const isMyPlanet = isLandedPlanetMine;
            const habitability = Math.max(0, Math.min(100, landedPlanet?.habitability_score ?? 0));
            const population = landedPlanet?.population ?? 0;
            const maxPopulation = landedPlanet?.max_population ?? 0;
            const detailColonists = typeof landedPlanetDetail?.colonists === 'number' ? landedPlanetDetail.colonists : null;
            const detailMaxColonists = typeof landedPlanetDetail?.maxColonists === 'number' ? landedPlanetDetail.maxColonists : null;

            return (
            <div className="landed-viewport">
              <div className={`planet-surface ${getPlanetTintClass(landedPlanet?.type)}`}>
                <div className="planet-header">
                  <div className="planet-icon">{getPlanetIcon(landedPlanet?.type)}</div>
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
                    <div className="planet-vitals">
                      <div
                        className="vital-dial"
                        style={{ '--dial-pct': habitability } as React.CSSProperties}
                        title={`Habitability: ${habitability}%`}
                      >
                        <div className="dial-face">
                          <span className="dial-value">{habitability}%</span>
                        </div>
                        <span className="dial-label">Habitability</span>
                      </div>
                      <div className="vital-bars">
                        <div className="vital-bar-row" title="Planetary population">
                          <span className="vital-bar-label">Population</span>
                          <div className="vital-bar-track">
                            <div
                              className="vital-bar-fill population"
                              style={{ width: `${maxPopulation > 0 ? Math.min(100, (population / maxPopulation) * 100) : (population > 0 ? 100 : 0)}%` }}
                            ></div>
                          </div>
                          <span className="vital-bar-value">
                            {population.toLocaleString()}{maxPopulation > 0 ? ` / ${maxPopulation.toLocaleString()}` : ''}
                          </span>
                        </div>
                        {detailColonists !== null && (
                          <div className="vital-bar-row" title="Colonist workforce">
                            <span className="vital-bar-label">Colonists</span>
                            <div className="vital-bar-track">
                              <div
                                className="vital-bar-fill colonists"
                                style={{ width: `${detailMaxColonists && detailMaxColonists > 0 ? Math.min(100, (detailColonists / detailMaxColonists) * 100) : (detailColonists > 0 ? 100 : 0)}%` }}
                              ></div>
                            </div>
                            <span className="vital-bar-value">
                              {detailColonists.toLocaleString()}{detailMaxColonists && detailMaxColonists > 0 ? ` / ${detailMaxColonists.toLocaleString()}` : ''}
                            </span>
                          </div>
                        )}
                        <div className="vital-bar-row status">
                          <span className="vital-bar-label">Status</span>
                          <span className="vital-bar-value">{landedPlanet.status?.toUpperCase() || 'UNKNOWN'}</span>
                        </div>
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
                height={Math.floor((window.innerHeight - 80) * 0.35)}
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
                  SECTOR {currentSector.sector_number || currentSector.sector_id}
                </div>
                {currentSector.region_name && (
                  <div className="hud-region-name" title={currentSector.region_name}>
                    {currentSector.region_name}
                  </div>
                )}
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
                  {isDockedAtSpaceDock
                    ? 'SPACEDOCK TERMINAL'
                    : stationTerminal === 'portoffice' ? 'PORT OFFICE' : 'TRADING TERMINAL'}
                  {!isDockedAtSpaceDock && (
                    <button
                      className="hud-header-toggle"
                      style={{
                        float: 'right', background: 'transparent', color: 'inherit',
                        border: '1px solid currentColor', borderRadius: '3px',
                        font: 'inherit', fontSize: '0.85em', padding: '0 8px', cursor: 'pointer'
                      }}
                      onClick={() => setStationTerminal(prev => prev === 'trade' ? 'portoffice' : 'trade')}
                    >
                      {stationTerminal === 'trade' ? '🏛️ Port Office' : '📈 Trade Desk'}
                    </button>
                  )}
                </div>
                <div className="screen-hud-content trading-content">
                  {isDockedAtSpaceDock ? (
                    <SpaceDockInterface />
                  ) : stationTerminal === 'portoffice' && playerState?.current_port_id ? (
                    <PortOfficeVenue
                      stationId={playerState.current_port_id}
                      stationName={
                        stationsInSector?.find((s: any) => s.id === playerState?.current_port_id)?.name ||
                        'Trading Station'
                      }
                      credits={playerState?.credits ?? 0}
                      onCreditsSet={() => { refreshPlayerState(); }}
                      onBack={() => setStationTerminal('trade')}
                    />
                  ) : (
                    <TradingInterface onClose={() => {}} />
                  )}
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

                    const planetIcon = getPlanetIcon(currentPlanet?.type);

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
                                <button className="section-btn" disabled title="Coming soon — shield upgrades are managed from the Colonies page">🛡️ Upgrade Shields</button>
                                <button className="section-btn" disabled title="Coming soon — drone deployment is managed from the Colonies page">🤖 Deploy Drones</button>
                                <button className="section-btn upgrade" disabled title="Coming soon — citadel upgrades are managed from the Colonies page">🏗️ Upgrade Citadel</button>
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
                                  <button
                                    className="transfer-btn disembark"
                                    disabled={!isLandedPlanetMine || shipColonists === 0}
                                    title={
                                      !isLandedPlanetMine
                                        ? 'Disembark requires landing on a planet you own'
                                        : shipColonists === 0
                                          ? 'No colonists aboard your ship'
                                          : 'Ship → Planet'
                                    }
                                    onClick={() => openTransferModal('disembark')}
                                  >
                                    <span>📥</span> Disembark
                                  </button>
                                  <button
                                    className="transfer-btn embark"
                                    disabled={!isLandedPlanetMine || landedPlanetColonists === 0}
                                    title={
                                      !isLandedPlanetMine
                                        ? 'You can only embark colonists from a planet you own'
                                        : landedPlanetColonists === 0
                                          ? 'No colonists on this planet to embark'
                                          : 'Planet → Ship'
                                    }
                                    onClick={() => openTransferModal('embark')}
                                  >
                                    <span>📤</span> Embark
                                  </button>
                                </div>
                              </div>
                              {shipColonists > 0 && (
                                <div className="colonists-aboard">
                                  👥 {shipColonists.toLocaleString()} colonists aboard
                                </div>
                              )}
                              {transferNotice && (
                                <div className={`transfer-notice ${transferNotice.type}`} role="status">
                                  {transferNotice.message}
                                </div>
                              )}

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
                  {!currentSector && (
                    <div className="nav-scan-state">
                      {!navScanTimedOut ? (
                        <>
                          <div className="nav-scan-spinner" aria-hidden="true"></div>
                          <span className="nav-scan-text">SCANNING SECTOR...</span>
                        </>
                      ) : (
                        <>
                          <span className="nav-scan-text warning">SECTOR SCAN TIMED OUT — NO TELEMETRY</span>
                          <button className="nav-scan-retry" onClick={handleRetryScan}>
                            ⟳ RETRY SCAN
                          </button>
                        </>
                      )}
                    </div>
                  )}
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
                          // Sector number first so label truncation keeps it;
                          // region suffix only when crossing regions
                          const showRegion = warp.region_id && warp.region_id !== currentSector.region_id;
                          const displayName = showRegion
                            ? `Sector ${warp.sector_number || warp.sector_id} · ${warp.region_name}`
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
                          // Sector number first so label truncation keeps it;
                          // region suffix only when crossing regions
                          const showRegion = tunnel.region_id && tunnel.region_id !== currentSector.region_id;
                          const displayName = showRegion
                            ? `Sector ${tunnel.sector_number || tunnel.sector_id} · ${tunnel.region_name}`
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
                  {sectorContacts.length > 0 ? (
                    <div className="contacts-compact-list">
                      {sectorContacts.map((player: any) => (
                        <div key={player.user_id || player.id || player.username} className="contact-list-item">
                          <span className="status-indicator online"></span>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                            <span
                              className="contact-list-name"
                              style={{ color: player.name_color || '#FFFFFF' }}
                            >
                              {player.military_rank ? `${player.military_rank.toUpperCase()} ` : ''}
                              {player.username || player.name || 'UNKNOWN CONTACT'}
                            </span>
                            {(player.reputation_tier || typeof player.personal_reputation === 'number') && (
                              <span style={{ fontSize: '0.7em', opacity: 0.7 }}>
                                {player.reputation_tier || 'Neutral'} ({(player.personal_reputation ?? 0) >= 0 ? '+' : ''}{player.personal_reputation ?? 0})
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="empty-state">No other contacts in sector</div>
                  )}
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Colonist transfer quantity modal — portal escapes the cockpit stacking context */}
        {transferModal && landedPlanet && createPortal(
          <div
            className="colonist-modal-overlay"
            onClick={() => { if (!isTransferring) setTransferModal(null); }}
          >
            <div className="colonist-modal" onClick={(e) => e.stopPropagation()}>
              <div className="colonist-modal-header">
                <h3>{transferModal === 'disembark' ? '📥 DISEMBARK COLONISTS' : '📤 EMBARK COLONISTS'}</h3>
                <button
                  className="colonist-modal-close"
                  onClick={() => setTransferModal(null)}
                  aria-label="Close colonist transfer"
                >
                  ×
                </button>
              </div>
              <div className="colonist-modal-route">
                {transferModal === 'disembark'
                  ? <>🚀 {currentShip?.name || 'Your ship'} → 🪐 {landedPlanet.name}</>
                  : <>🪐 {landedPlanet.name} → 🚀 {currentShip?.name || 'Your ship'}</>}
              </div>

              <div className="colonist-qty-section">
                <label className="colonist-qty-label" htmlFor="colonist-qty-input">Colonists</label>
                <input
                  type="range"
                  min="1"
                  max={Math.max(1, transferMax)}
                  value={transferQuantity}
                  onChange={(e) => setTransferQuantity(clampTransferQuantity(parseInt(e.target.value) || 1))}
                  className="colonist-qty-slider"
                  disabled={transferMax < 1}
                />
                <div className="colonist-qty-input-group">
                  <button
                    className="qty-step"
                    onClick={() => setTransferQuantity(clampTransferQuantity(transferQuantity - 1))}
                    disabled={transferQuantity <= 1}
                  >
                    −
                  </button>
                  <input
                    id="colonist-qty-input"
                    type="number"
                    min="1"
                    max={Math.max(1, transferMax)}
                    value={transferQuantity}
                    onChange={(e) => setTransferQuantity(clampTransferQuantity(parseInt(e.target.value) || 1))}
                    className="colonist-qty-input"
                  />
                  <button
                    className="qty-step"
                    onClick={() => setTransferQuantity(clampTransferQuantity(transferQuantity + 1))}
                    disabled={transferQuantity >= transferMax}
                  >
                    +
                  </button>
                </div>
                <div className="colonist-qty-presets">
                  {[0.25, 0.5, 0.75].map((fraction) => (
                    <button
                      key={fraction}
                      onClick={() => setTransferQuantity(clampTransferQuantity(Math.floor(transferMax * fraction)))}
                      disabled={transferMax < 1}
                    >
                      {fraction * 100}% ({Math.max(1, Math.floor(transferMax * fraction)).toLocaleString()})
                    </button>
                  ))}
                  <button
                    onClick={() => setTransferQuantity(Math.max(1, transferMax))}
                    disabled={transferMax < 1}
                  >
                    Max ({transferMax.toLocaleString()})
                  </button>
                </div>
              </div>

              <div className="colonist-transfer-summary">
                <div className="summary-line">
                  <span>Aboard ship</span>
                  <span className="value">
                    {shipColonists.toLocaleString()} → {(transferModal === 'disembark'
                      ? Math.max(0, shipColonists - transferQuantity)
                      : shipColonists + transferQuantity).toLocaleString()}
                  </span>
                </div>
                <div className="summary-line">
                  <span>On planet</span>
                  <span className="value">
                    {landedPlanetColonists.toLocaleString()} → {(transferModal === 'disembark'
                      ? landedPlanetColonists + transferQuantity
                      : Math.max(0, landedPlanetColonists - transferQuantity)).toLocaleString()}
                  </span>
                </div>
              </div>

              <div className="colonist-modal-actions">
                <button
                  className="colonist-modal-cancel"
                  onClick={() => setTransferModal(null)}
                  disabled={isTransferring}
                >
                  Cancel
                </button>
                <button
                  className="colonist-modal-confirm"
                  onClick={handleTransferConfirm}
                  disabled={isTransferring || transferMax < 1 || transferQuantity < 1 || transferQuantity > transferMax}
                >
                  {isTransferring
                    ? 'TRANSFERRING…'
                    : transferModal === 'disembark' ? 'CONFIRM DISEMBARK' : 'CONFIRM EMBARK'}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

        {/* ARIA assistant is mounted globally in GameLayout for all /game routes */}
      </div>
    </GameLayout>
  );
};

export default GameDashboard;
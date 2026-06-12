import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
// import { useTheme } from '../../themes/ThemeProvider'; // Available for future use
import UserProfile from '../auth/UserProfile';
import LogoutButton from '../auth/LogoutButton';
import EnhancedAIAssistant from '../ai/EnhancedAIAssistant';
import './game-layout.css';
import '../../styles/themes/cockpit-animations.css';
import '../../styles/themes/cockpit-components.css';

interface GameLayoutProps {
  children: React.ReactNode;
}

const GameLayout: React.FC<GameLayoutProps> = ({ children }) => {
  const { user } = useAuth();
  const { playerState, currentShip, currentSector, isLoading, refreshPlayerState } = useGame();
  // const { currentTheme } = useTheme(); // Available for future use
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // ── Cockpit stability ────────────────────────────────────────────────
  // GameContext toggles the shared `isLoading` flag on EVERY background
  // refresh (after each scan/jump/move/dock). The viewport children must
  // therefore NEVER be unmounted on `isLoading` — doing so resets bearing
  // dials, races the ARM timer, destroys canvas state, kills scroll
  // positions and refetches the minimap. Instead:
  //   • full loading overlay ONLY during the true initial load (we have
  //     never seen player state), rendered absolutely OVER the viewport;
  //   • background refreshes get at most a subtle SYNC indicator that
  //     appears only if loading persists beyond ~300ms (no flicker).
  // State (not just a ref) so the SYNC-indicator effect below re-runs the
  // moment the latch flips. A pure ref flip during render does not retrigger
  // effects, leaving a dead window where a refresh that begins right as the
  // latch flips mid-load never starts the SYNC timer.
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  if (playerState && !hasLoadedOnce) {
    // Idempotent render-time latch: flips false→true exactly once,
    // safe under StrictMode double-render (setState during render with an
    // already-true value is a no-op).
    setHasLoadedOnce(true);
  }
  const isInitialLoad = isLoading && !hasLoadedOnce;

  const [showSyncIndicator, setShowSyncIndicator] = useState(false);
  React.useEffect(() => {
    if (isLoading && hasLoadedOnce) {
      const timer = window.setTimeout(() => setShowSyncIndicator(true), 300);
      return () => window.clearTimeout(timer);
    }
    setShowSyncIndicator(false);
    return undefined;
  }, [isLoading, hasLoadedOnce]);

  // Try to refresh player state on mount if we don't have it
  const hasAttemptedRefresh = React.useRef(false);
  React.useEffect(() => {
    if (user && !playerState && !isLoading && !hasAttemptedRefresh.current) {
      hasAttemptedRefresh.current = true;
      refreshPlayerState();
    }
    // Reset the ref if we get player state (allows retry on logout/login)
    if (playerState) {
      hasAttemptedRefresh.current = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, playerState, isLoading]); // Remove refreshPlayerState from deps to prevent loop
  
  const toggleSidebar = () => {
    setSidebarOpen(!sidebarOpen);
  };
  
  return (
    <div className="game-layout-wrapper">
      <div className="game-layout">
        <header className="game-header hud-panel">
          <div className="game-header-left">
            <button
              className="cockpit-btn sidebar-toggle"
              onClick={toggleSidebar}
              aria-label={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
              title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
            >
              <span className="toggle-icon">{sidebarOpen ? '◀' : '▶'}</span>
            </button>
            <h1 className="game-title">
              <span className="title-main">SECTOR WARS</span>
              <span className="title-year">2102</span>
            </h1>
          </div>
          <div className="header-commander-bar">
            <div className="header-commander-name">
              {user?.username || '—'}
              {!playerState && !isLoading && (
                <button
                  onClick={refreshPlayerState}
                  className="refresh-btn header-refresh-btn"
                  title="Refresh player state"
                  aria-label="Refresh"
                >
                  ⟳
                </button>
              )}
            </div>
            <div className="header-stat">
              <span className="header-stat-label">CRED</span>
              <span className="data-readout credits">{playerState?.credits?.toLocaleString() || '0'}</span>
            </div>
            <div className="header-stat">
              <span className="header-stat-label">TURN</span>
              <span className="data-readout turns">{playerState?.turns?.toLocaleString() || '0'}</span>
            </div>
            <div className="header-stat">
              <span className="header-stat-label">DRONE</span>
              <span className="data-readout">{playerState?.defense_drones || '0'}</span>
            </div>
          </div>
          <div className="game-header-right">
            <UserProfile />
          </div>
        </header>

        <div className="game-container">
          <aside className={`game-sidebar hud-panel ${sidebarOpen ? 'open' : 'closed'}`}>
            <div className="cockpit-card ship-info">
              <div className="cockpit-card-header">
                <h3 className="cockpit-card-title">VESSEL STATUS</h3>
              </div>
              {currentShip ? (
                <div className="current-ship">
                  <div className="ship-name">{currentShip.name || 'UNNAMED VESSEL'}</div>
                  <div className="ship-type">{currentShip.type || 'UNKNOWN CLASS'}</div>
                  <div className="ship-cargo">
                    <h4 className="cargo-header">CARGO BAY</h4>
                    {(() => {
                      // Cargo shape: { used, capacity, contents: { commodity: qty } }
                      // Render the actual goods, not the raw structure
                      const cargo = (currentShip.cargo ?? {}) as Record<string, any>;
                      const contents: Record<string, number> =
                        cargo.contents && typeof cargo.contents === 'object'
                          ? cargo.contents
                          : Object.fromEntries(
                              Object.entries(cargo).filter(([k, v]) =>
                                typeof v === 'number' && !['used', 'capacity'].includes(k))
                            );
                      const used = typeof cargo.used === 'number' ? cargo.used : null;
                      const capacity = typeof cargo.capacity === 'number' ? cargo.capacity : null;
                      const items = Object.entries(contents).filter(([, qty]) => qty > 0);
                      return (
                        <>
                          {used !== null && capacity !== null && (
                            <div className="cargo-item">
                              <span className="resource-name">Hold</span>
                              <span className="data-readout">{used} / {capacity}</span>
                            </div>
                          )}
                          {items.length > 0 ? (
                            <ul className="cargo-list">
                              {items.map(([resource, qty]) => (
                                <li key={resource} className="cargo-item">
                                  <span className="resource-name">
                                    {resource.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                                  </span>
                                  <span className="data-readout">× {qty}</span>
                                </li>
                              ))}
                            </ul>
                          ) : (
                            <p className="empty-cargo">CARGO BAY EMPTY</p>
                          )}
                        </>
                      );
                    })()}
                  </div>

                  {/* Genesis Device Display - Special Items */}
                  {(currentShip.max_genesis_devices ?? 0) > 0 && (
                    <div className="ship-genesis">
                      <h4 className="genesis-header">
                        <span className="genesis-icon">🌍</span>
                        GENESIS BAY
                      </h4>
                      <div className="genesis-status">
                        <div className="genesis-slots">
                          {Array.from({ length: currentShip.max_genesis_devices || 0 }, (_, i) => (
                            <div
                              key={i}
                              className={`genesis-slot ${i < (currentShip.genesis_devices || 0) ? 'loaded' : 'empty'}`}
                              title={i < (currentShip.genesis_devices || 0) ? 'Genesis Device Loaded' : 'Empty Slot'}
                            >
                              {i < (currentShip.genesis_devices || 0) ? '🌍' : '○'}
                            </div>
                          ))}
                        </div>
                        <div className="genesis-count">
                          <span className={`data-readout ${(currentShip.genesis_devices || 0) > 0 ? 'genesis-active' : ''}`}>
                            {currentShip.genesis_devices || 0} / {currentShip.max_genesis_devices || 0}
                          </span>
                        </div>
                      </div>
                      {(currentShip.genesis_devices || 0) > 0 && (
                        <div className="genesis-ready-indicator">
                          <span className="pulse-dot"></span>
                          TERRAFORM READY
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div className="no-ship">NO ACTIVE VESSEL</div>
              )}
            </div>
          
            <div className="cockpit-card location-info">
              <div className="cockpit-card-header">
                <h3 className="cockpit-card-title">NAV COORDS</h3>
              </div>
              {currentSector ? (
                <div className="current-sector">
                  <div className="sector-name">SECTOR {playerState?.current_sector_id || currentSector.id || 'UNKNOWN'}</div>
                  <div className="sector-designation">{currentSector.name || 'UNCHARTED'}</div>
                  <div className="sector-type">{currentSector.type?.toUpperCase() || 'UNKNOWN'}</div>
                  {(currentSector.hazard_level || 0) > 0 && (
                    <div className="sector-hazard">
                      <span className="hazard-label">THREAT LEVEL:</span>
                      <span className="data-readout hazard">{currentSector.hazard_level || 0}</span>
                    </div>
                  )}
                </div>
              ) : playerState?.current_sector_id ? (
                <div className="current-sector">
                  <div className="sector-name">SECTOR {playerState.current_sector_id}</div>
                  <div className="unknown-sector">LOADING SECTOR DATA...</div>
                </div>
              ) : (
                <div className="unknown-sector">COORDINATES UNKNOWN</div>
              )}
            </div>
          
            <nav className="game-nav">
              <div className="nav-header">SHIP SYSTEMS</div>
              <ul className="nav-list">
                <li><Link to="/game" className="nav-link cockpit-btn">🚀 COMMAND</Link></li>
                <li><Link to="/game/map" className="nav-link cockpit-btn">🗺️ NAV CHART</Link></li>
                <li><Link to="/game/ships" className="nav-link cockpit-btn">🛸 HANGAR</Link></li>
                <li><Link to="/game/trading" className="nav-link cockpit-btn">💹 TRADE</Link></li>
                <li><Link to="/game/planets" className="nav-link cockpit-btn">🪐 COLONIES</Link></li>
                <li><Link to="/game/combat" className="nav-link cockpit-btn">⚔️ WEAPONS</Link></li>
                <li><Link to="/game/team" className="nav-link cockpit-btn">👥 CREW</Link></li>
                <li><Link to="/game/ranking" className="nav-link cockpit-btn">🎖️ SERVICE RECORD</Link></li>
              </ul>
              <div className="nav-footer">
                <LogoutButton className="nav-link cockpit-btn logout-btn" />
              </div>
            </nav>
          </aside>
        
          <main className="game-content" aria-busy={isInitialLoad}>
            {/* Children render UNCONDITIONALLY — never unmounted by a
                background refresh (see cockpit-stability note above).
                During the initial-load overlay the viewport is `inert`
                so its controls can't be tab-focused underneath. */}
            <div
              className="main-viewport"
              // `inert` isn't in the installed @types/react (18.x) surface yet,
              // but the DOM supports it and React passes unknown lowercase
              // attrs through. Spread it so hidden controls under the
              // initial-load overlay can't be tab-focused.
              {...(isInitialLoad ? { inert: '' } : {})}
            >
              {children}
            </div>
            {isInitialLoad && (
              <div className="viewport-loading-overlay">
                <div className="loading-spinner"></div>
                <p className="loading-text animate-typing">INITIALIZING SYSTEMS...</p>
              </div>
            )}
            {showSyncIndicator && !isInitialLoad && (
              <div className="sync-indicator" role="status" aria-live="polite" aria-label="Synchronizing">
                <span className="sync-indicator-dot"></span>
                <span className="sync-indicator-label">SYNC</span>
              </div>
            )}
          </main>

          {/* Mount ARIA assistant once for all /game routes when a player session exists */}
          {playerState?.id && (
            <EnhancedAIAssistant theme="dark" />
          )}
        </div>
      </div>
    </div>
  );
};

export default GameLayout;
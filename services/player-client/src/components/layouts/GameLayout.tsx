import React, { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
// import { useTheme } from '../../themes/ThemeProvider'; // Available for future use
import UserProfile from '../auth/UserProfile';
import LogoutButton from '../auth/LogoutButton';
import AriaConsoleStrip from '../aria/AriaConsoleStrip';
import './game-layout.css';
import '../../styles/themes/cockpit-animations.css';
import '../../styles/themes/cockpit-components.css';

interface GameLayoutProps {
  children: React.ReactNode;
}

/* SHIP SYSTEMS nav — one entry per console instrument, each carrying its
   Law-5 accent so the active route highlights in its own system color. */
const NAV_ITEMS: Array<{ to: string; icon: string; label: string; accent: string }> = [
  { to: '/game', icon: '🚀', label: 'COMMAND', accent: '#00D9FF' },
  { to: '/game/map', icon: '🗺️', label: 'NAV CHART', accent: '#00D9FF' },
  { to: '/game/ships', icon: '🛸', label: 'HANGAR', accent: '#9EC5FF' },
  { to: '/game/trading', icon: '💹', label: 'TRADE', accent: '#FFB000' },
  { to: '/game/planets', icon: '🪐', label: 'COLONIES', accent: '#7B2FFF' },
  { to: '/game/combat', icon: '⚔️', label: 'WEAPONS', accent: '#FF4D6D' },
  { to: '/game/team', icon: '👥', label: 'CREW', accent: '#00FF7F' },
  { to: '/game/ranking', icon: '🎖️', label: 'SERVICE RECORD', accent: '#FFD700' },
];

const GameLayout: React.FC<GameLayoutProps> = ({ children }) => {
  const { user } = useAuth();
  const { playerState, currentShip, currentSector, isLoading, isRefreshing, refreshPlayerState } = useGame();
  // const { currentTheme } = useTheme(); // Available for future use
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const location = useLocation();

  // ── Scroll contract (Law 2) ──────────────────────────────────────────
  // On /game routes the DOCUMENT never scrolls: the shell locks html/body
  // overflow while mounted and restores the previous values on unmount
  // (login/landing pages keep their normal scroll behavior). Only monitor
  // interiors (.screen-hud-content) scroll.
  React.useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prevHtmlOverflow = html.style.overflow;
    const prevBodyOverflow = body.style.overflow;
    html.style.overflow = 'hidden';
    body.style.overflow = 'hidden';
    return () => {
      html.style.overflow = prevHtmlOverflow;
      body.style.overflow = prevBodyOverflow;
    };
  }, []);

  // ── Cockpit stability ────────────────────────────────────────────────
  // GameContext semantics after the isLoading split: `isLoading` is true
  // ONLY during initial hydration (playerState still null); background
  // refreshes flip the lightweight `isRefreshing` flag instead and never
  // unmount anything. The viewport children render unconditionally:
  //   • full loading overlay ONLY during the true initial load (we have
  //     never seen player state), rendered absolutely OVER the viewport;
  //   • background refreshes get at most a subtle SYNC indicator (keyed on
  //     isRefreshing) that appears only past ~300ms (no flicker).
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
    if (isRefreshing && hasLoadedOnce) {
      const timer = window.setTimeout(() => setShowSyncIndicator(true), 300);
      return () => window.clearTimeout(timer);
    }
    setShowSyncIndicator(false);
    return undefined;
  }, [isRefreshing, hasLoadedOnce]);

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
                {NAV_ITEMS.map((item) => {
                  const isActive = location.pathname === item.to;
                  return (
                    <li key={item.to}>
                      <Link
                        to={item.to}
                        className={`nav-link cockpit-btn${isActive ? ' active' : ''}`}
                        style={{ '--nav-accent': item.accent } as React.CSSProperties}
                        aria-current={isActive ? 'page' : undefined}
                      >
                        {item.icon} {item.label}
                      </Link>
                    </li>
                  );
                })}
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
            {/* ARIA console fixture (Law 4): the shell reserves a slim
                bottom slot on every /game route. The strip is self-
                contained (props {}) and expands UPWARD over the viewport;
                the slot only reserves the 36px band. The old floating FAB
                is retired. */}
            <div className="aria-console-slot">
              <AriaConsoleStrip />
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
        </div>
      </div>
    </div>
  );
};

export default GameLayout;
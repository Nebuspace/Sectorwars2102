import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useSettings } from '../../contexts/SettingsContext';
import LogoutButton from '../auth/LogoutButton';
import { formatCredits } from '../../utils/formatters';
import { TurnsIcon } from '../icons/TurnsIcon';
import { MineIcon } from '../icons/MineIcon';
import ReputationPage from '../mfd/pages/ReputationPage';
import { ShipSelector } from '../ships/ShipSelector';
import { EmbeddedContext } from '../cockpit/EmbeddedContext';
import ServiceRecordTab from './ServiceRecordTab';
import ColoniesRosterTab from './ColoniesRosterTab';
import TeamSummaryTab from './TeamSummaryTab';
import LocationDropdown from './LocationDropdown';
import RegionOwnerControls from '../governance/RegionOwnerControls';
import './statusbar.css';

/**
 * StatusBar — the persistent 56px one-flex-row status bar
 * (WO-UI0-STATUSBAR; audit/design-briefs/cockpit-redesign-v10-RATIFIED.html:
 * 496-502). SUPERSEDES PlayerVitalsHud's role — mounted into GameLayout's
 * reserved `statusbar` grid row at the serial integration step, which also
 * retired PlayerVitalsHud's mount and GameDashboard's three overlap-defect
 * canvas chips (their location context relocated into LocationDropdown; see
 * that file's own doc-comment).
 *
 * Row order (left→right), matching the ratified brief exactly:
 *   [👤 name ▾ dossier] · [◉ location ▾] · vitals + REP badge · [⚙] · [⏻]
 *
 * Evolved from PlayerVitalsHud.tsx: reuses its data primitives (formatCredits,
 * TurnsIcon, MineIcon, useGame().playerState, useWebSocket().linkStatus,
 * LogoutButton) and the same low-turns/bounty/LINK field logic, but is fresh
 * markup/CSS (`sb-*` classes) for the new single-row layout.
 */
type DossierTab = 'identity' | 'reputation' | 'service' | 'fleet' | 'colonies' | 'crew' | 'settings';

const DOSSIER_TABS: Array<{ id: DossierTab; label: string }> = [
  { id: 'identity', label: 'IDENTITY' },
  { id: 'reputation', label: 'REPUTATION' },
  { id: 'service', label: 'SERVICE RECORD' },
  { id: 'fleet', label: 'FLEET' },
  { id: 'colonies', label: 'COLONIES' },
  { id: 'crew', label: 'CREW' },
  { id: 'settings', label: 'SETTINGS' },
];

/* IdentityTab — a lean local echo of PlayerInfo.tsx's (unexported)
 * IdentitySection, reusing the SAME data (useAuth().user, useGame().
 * playerState) and field set (name/rank/reputation/credits/turns).
 * DELIBERATE deviation from a literal "reuse PlayerInfo.tsx" recon note:
 * PlayerInfo.tsx is itself a full tabbed page (identity/reputation/hangar/
 * colonies) — embedding it whole as one tab's content here would nest a
 * second tab switcher inside this dossier's Identity tab. Flagged in the
 * WO-UI0-STATUSBAR(a) report for review. */
const IdentityTab: React.FC = () => {
  const { user } = useAuth();
  const { playerState } = useGame();
  return (
    <div className="sb-identity-tab">
      <div className="sb-identity-name" style={{ color: playerState?.name_color || '#00D9FF' }}>
        {user?.username || '—'}
      </div>
      <div className="sb-identity-grid">
        <div className="sb-identity-field">
          <span className="sb-identity-k">RANK</span>
          <span className="sb-identity-v">{playerState?.military_rank || '—'}</span>
        </div>
        <div className="sb-identity-field">
          <span className="sb-identity-k">REPUTATION</span>
          <span className="sb-identity-v">{playerState?.reputation_tier || '—'}</span>
        </div>
        <div className="sb-identity-field">
          <span className="sb-identity-k">CREDITS</span>
          <span className="sb-identity-v">{formatCredits(playerState?.credits)}</span>
        </div>
        <div className="sb-identity-field">
          <span className="sb-identity-k">TURNS</span>
          <span className="sb-identity-v">
            {(playerState?.turns ?? 0).toLocaleString()}
            {typeof playerState?.max_turns === 'number'
              ? ` / ${playerState.max_turns.toLocaleString()}`
              : ''}
          </span>
        </div>
      </div>
    </div>
  );
};

// UI scale bounds for the embedded slider — mirrors SettingsPage.tsx's own
// UI_SCALE_MIN/MAX/STEP exactly (SettingsContext.setUiScale clamps to the
// same [0.6, 1.2] range regardless, so a mismatch here couldn't desync the
// two controls, but keeping the numbers identical avoids a confusing
// slider-range difference between the two surfaces).
const UI_SCALE_MIN = 0.6;
const UI_SCALE_MAX = 1.2;
const UI_SCALE_STEP = 0.05;

/* SettingsTab — WO-UI5-DOSSIER: embeds the UI-scale slider directly (was a
 * placeholder link-out to /game/settings). A lean local echo of
 * SettingsPage.tsx's Display section, same deliberate-duplication call as
 * IdentityTab above (SettingsPage is itself a full CockpitInstrument page;
 * only its one live control is embeddable here, not the page). The
 * subscription slot is a DELIBERATE STUB — PayPal/subscription wiring is a
 * payments carve-out (human-gated, out of this WO's scope) — renders a
 * disabled "coming soon" placeholder, zero PayPal imports/calls. */
const SettingsTab: React.FC = () => {
  const { uiScale, setUiScale } = useSettings();

  const handleScaleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const next = parseFloat(e.target.value);
    if (Number.isFinite(next)) setUiScale(next);
  };

  const currentPercent = `${Math.round(uiScale * 100)}%`;

  return (
    <div className="sb-settings-tab">
      <div className="sb-settings-row">
        <label htmlFor="sb-ui-scale-range" className="sb-identity-k">
          UI SCALE
        </label>
        <div className="sb-settings-row-control">
          <input
            id="sb-ui-scale-range"
            type="range"
            className="sb-settings-range"
            min={UI_SCALE_MIN}
            max={UI_SCALE_MAX}
            step={UI_SCALE_STEP}
            value={uiScale}
            onChange={handleScaleChange}
            aria-valuetext={currentPercent}
          />
          <span className="sb-identity-v" aria-live="polite">{currentPercent}</span>
        </div>
      </div>

      <div className="sb-settings-row sb-settings-subscription">
        <span className="sb-identity-k">SUBSCRIPTION</span>
        <span className="sb-settings-subscription-stub" aria-label="Subscription — coming soon">
          Coming soon
        </span>
      </div>

      <Link to="/game/settings" className="sb-settings-link">
        Open full Settings page →
      </Link>
    </div>
  );
};

const StatusBar: React.FC = () => {
  const { user } = useAuth();
  const { playerState } = useGame();
  const { linkStatus } = useWebSocket();

  const [dossierOpen, setDossierOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<DossierTab>('identity');
  const dossierRef = useRef<HTMLDivElement>(null);
  // Pixel a11y REVISE #2 — trigger ref so focus can be RETURNED to it on
  // close (both programmatic closes below and the WAI-ARIA tabs pattern's
  // own dismissal expectations).
  const nameChipRef = useRef<HTMLButtonElement>(null);
  // Pixel a11y REVISE #1 — one ref per rendered tab button (roving
  // tabindex target for keyboard nav + the open-focus destination).
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Dismiss on outside click / Escape — this panel sits over the
  // click-through windshield, so a stray click elsewhere must close it
  // rather than leaving a panel stuck open over the scene.
  useEffect(() => {
    if (!dossierOpen) return;
    const handlePointer = (e: MouseEvent) => {
      if (dossierRef.current && !dossierRef.current.contains(e.target as Node)) {
        setDossierOpen(false);
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDossierOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointer);
      document.removeEventListener('keydown', handleKey);
    };
  }, [dossierOpen]);

  // Pixel a11y REVISE #2 — focus management: on the false→true open EDGE,
  // move focus into the panel (the active tab, per the WAI-ARIA tabs
  // pattern — focus enters a tablist at the currently-selected tab); on the
  // true→false close edge (however triggered — outside click, Escape, or
  // the toggle button itself), return focus to the trigger. Keyed off a
  // ref-tracked previous value (mirrors GameLayout.tsx's own
  // prevIsLandedRef/prevGroundedRef edge-detection idiom) rather than
  // firing on every dossierOpen dependency touch, so it never steals focus
  // on the initial mount (dossierOpen starts false, no prior "true" to
  // transition down from) and doesn't re-fire merely because activeTab
  // changes while already open (a tab click/keyboard-nav already moves
  // focus itself).
  const wasDossierOpenRef = useRef(false);
  useEffect(() => {
    if (dossierOpen && !wasDossierOpenRef.current) {
      const idx = DOSSIER_TABS.findIndex((t) => t.id === activeTab);
      tabRefs.current[idx]?.focus();
    } else if (!dossierOpen && wasDossierOpenRef.current) {
      nameChipRef.current?.focus();
    }
    wasDossierOpenRef.current = dossierOpen;
    // activeTab intentionally excluded — read only on the open edge above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dossierOpen]);

  // Pixel a11y REVISE #1 — WAI-ARIA tabs pattern keyboard nav: Left/Right
  // cycle tabs (wrapping), Home/End jump to first/last; each moves BOTH the
  // active tab (automatic activation — this console has no separate
  // "activate" step) and DOM focus to the newly-active tab (roving
  // tabindex, set below).
  const handleTablistKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const currentIndex = DOSSIER_TABS.findIndex((t) => t.id === activeTab);
    let nextIndex = currentIndex;
    switch (e.key) {
      case 'ArrowRight':
        nextIndex = (currentIndex + 1) % DOSSIER_TABS.length;
        break;
      case 'ArrowLeft':
        nextIndex = (currentIndex - 1 + DOSSIER_TABS.length) % DOSSIER_TABS.length;
        break;
      case 'Home':
        nextIndex = 0;
        break;
      case 'End':
        nextIndex = DOSSIER_TABS.length - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    setActiveTab(DOSSIER_TABS[nextIndex].id);
    tabRefs.current[nextIndex]?.focus();
  };

  // id=145d — turns regen: +N/hr subscript + hover→time-to-full (id=142
  // fields). Mirrors PlayerVitalsHud's own computation exactly (id=142/145
  // canon fields carried over, not reinvented).
  const regenPerHr = playerState?.turn_regen_per_hour ?? 0;
  const turnsNow = playerState?.turns ?? 0;
  const maxTurns = playerState?.max_turns;
  // WO-PROG-TURN-VISIBILITY canon threshold (<50) — carried over from
  // PlayerVitalsHud so the scarcity warning isn't silently dropped.
  const lowTurns = !!playerState && turnsNow < 50;
  const turnsTitle = (() => {
    if (typeof maxTurns !== 'number') return 'Turns';
    if (turnsNow >= maxTurns) return 'Turns — full';
    if (regenPerHr <= 0) return 'Turns';
    const hrs = (maxTurns - turnsNow) / regenPerHr;
    const h = Math.floor(hrs);
    const m = Math.round((hrs - h) * 60);
    return `Turns — ${h > 0 ? `${h}h ${m}m` : `${m}m`} to full (+${Math.round(regenPerHr)}/hr)`;
  })();
  const bounty = playerState?.bounty_total ?? 0;

  const linkLabel = linkStatus === 'up' ? 'OK' : linkStatus === 'reconnecting' ? 'RELINK' : 'DOWN';
  const linkTitle =
    linkStatus === 'up'
      ? 'Uplink connected'
      : linkStatus === 'reconnecting'
        ? 'Uplink lost — reconnecting'
        : 'Uplink down';

  const repTier = playerState?.reputation_tier || 'Neutral';
  const repColor = playerState?.name_color || '#888888';

  return (
    <div className="status-bar">
      {/* [👤 name ▾] — dossier dropdown */}
      <div className="sb-dossier" ref={dossierRef}>
        <button
          type="button"
          ref={nameChipRef}
          className="sb-chip sb-name-chip"
          style={{ '--pilot-color': playerState?.name_color || '#00D9FF' } as React.CSSProperties}
          onClick={() => setDossierOpen((o) => !o)}
          aria-haspopup="dialog"
          aria-expanded={dossierOpen}
          aria-controls="sb-dossier-menu"
        >
          <span className="sb-chip-icon" aria-hidden="true">👤</span>
          <span className="sb-name-text">{user?.username || '—'}</span>
          <span className="sb-chip-caret" aria-hidden="true">▾</span>
        </button>
        {dossierOpen && (
          <div id="sb-dossier-menu" className="sb-dropdown sb-dossier-panel" role="dialog" aria-label="Player dossier">
            {/* A tabbed console that stays OPEN across selection is a tablist,
                not a menu (a menu's items activate-and-close) — corrected from
                role=menu/menuitem at the integration step. Keyboard nav +
                roving tabindex per the WAI-ARIA tabs pattern (Pixel a11y
                REVISE #1). */}
            <div
              className="sb-dossier-tabs"
              role="tablist"
              aria-label="Player dossier"
              onKeyDown={handleTablistKeyDown}
            >
              {DOSSIER_TABS.map((t, i) => (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  id={`sb-dossier-tab-${t.id}`}
                  ref={(el) => { tabRefs.current[i] = el; }}
                  aria-selected={activeTab === t.id}
                  aria-controls="sb-dossier-tabpanel"
                  tabIndex={activeTab === t.id ? 0 : -1}
                  className={`sb-dossier-tab${activeTab === t.id ? ' active' : ''}`}
                  onClick={() => setActiveTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div
              id="sb-dossier-tabpanel"
              className="sb-dossier-body"
              role="tabpanel"
              aria-labelledby={`sb-dossier-tab-${activeTab}`}
            >
              {activeTab === 'identity' && <IdentityTab />}
              {activeTab === 'reputation' && <ReputationPage />}
              {activeTab === 'service' && <ServiceRecordTab />}
              {activeTab === 'fleet' && (
                <EmbeddedContext.Provider value={true}>
                  <ShipSelector />
                </EmbeddedContext.Provider>
              )}
              {activeTab === 'colonies' && <ColoniesRosterTab />}
              {activeTab === 'crew' && <TeamSummaryTab />}
              {activeTab === 'settings' && <SettingsTab />}
            </div>
          </div>
        )}
      </div>

      {/* [◉ location ▾] — RegionOwnerControls (sub-part b) wired in at integration */}
      <LocationDropdown>
        <RegionOwnerControls />
      </LocationDropdown>

      {/* vitals + REP badge */}
      <div className="sb-vitals">
        <span className="sb-stat sb-credits" title="Credits">
          {formatCredits(playerState?.credits)}
        </span>
        <span className={lowTurns ? 'sb-stat sb-turns-low' : 'sb-stat'} title={turnsTitle}>
          <span className="sb-k"><TurnsIcon size="0.8rem" /></span>
          <span className="sb-v sb-turns-stack">
            <span className="sb-turns-count">
              {turnsNow.toLocaleString()}
              {typeof maxTurns === 'number' && <span className="sb-sub">/{maxTurns.toLocaleString()}</span>}
            </span>
            {regenPerHr > 0 && <span className="sb-regen">+{Math.round(regenPerHr)}/hr</span>}
          </span>
        </span>
        <span className="sb-stat sb-drones" title="Attack / Defense drones (current ship)">
          <span className="sb-k">DRONES</span>
          <span className="sb-v">
            <span className="sb-drone" title="Attack drones">⚔ {playerState?.attack_drones ?? 0}</span>
            <span className="sb-drone" title="Defense drones">🛡 {playerState?.defense_drones ?? 0}</span>
          </span>
        </span>
        <span className="sb-stat" title="Mines">
          <span className="sb-k"><MineIcon size="0.8rem" /></span>
          <span className="sb-v">{playerState?.mines ?? 0}</span>
        </span>
        <span className={`sb-stat sb-link sb-link--${linkStatus}`} title={linkTitle}>
          <span className="sb-k">LINK</span>
          <span className="sb-v">{linkLabel}</span>
        </span>
        {bounty > 0 && (
          <span className="sb-stat sb-bounty" title="Bounty on your head">
            <span className="sb-k">BOUNTY</span>
            <span className="sb-v">{formatCredits(bounty)}</span>
          </span>
        )}
        <span
          className="sb-rep-badge"
          style={{ '--rep-color': repColor } as React.CSSProperties}
          title={`Reputation tier: ${repTier}`}
        >
          {repTier}
        </span>
      </div>

      {/* [⚙] settings — quick-access icon; placeholder nav-trigger, real popup is WO-UI0-SCALE-LAW */}
      <Link to="/game/settings" className="sb-icon-btn sb-settings-btn" aria-label="Settings" title="Settings">
        ⚙
      </Link>

      {/* [⏻] logout */}
      <LogoutButton className="sb-logout-btn" />
    </div>
  );
};

export default StatusBar;

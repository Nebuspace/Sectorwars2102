import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useSettings } from '../../contexts/SettingsContext';
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
 * StatusBar — the persistent one-flex-row status bar
 * (WO-UI0-STATUSBAR; audit/design-briefs/cockpit-redesign-v10-RATIFIED.html:
 * 496-502, 438-449). SUPERSEDES PlayerVitalsHud's role — mounted into
 * GameLayout's `.stage` grid row 1 at the serial integration step, which
 * also retired PlayerVitalsHud's mount and GameDashboard's three overlap-
 * defect canvas chips (their location context relocated into
 * LocationDropdown; see that file's own doc-comment).
 *
 * WO-UI0-SHELL-TRANSPLANT Leaf L1: re-emitted onto the shared shell
 * primitives cockpit-shell.css defines (`.sbar`/`.chip`/`.vit`/`.grow`/
 * `.repb`, artifact lines 438-449) — the root carries BOTH `sbar` (the
 * shell's row skin: flex/gap/padding/background/border-bottom) and
 * `status-bar` (a stable structural hook other files still query — see
 * that class's own rule comment below). Content-bearing `sb-*` classes
 * that cockpit-shell has no equivalent for (dossier/location dropdown
 * shells, turns-regen stack, drone/link/bounty color states, credits'
 * gold highlight) are KEPT as compound classes alongside the shell ones.
 *
 * Row order (left→right), matching the ratified brief exactly:
 *   [👤 name ▾ dossier] · [◉ location ▾] · grow · vitals + REP badge ·
 *   [⚙] · [⏻]
 *
 * Evolved from PlayerVitalsHud.tsx: reuses its data primitives (formatCredits,
 * TurnsIcon, MineIcon, useGame().playerState, useWebSocket().linkStatus)
 * and the same low-turns/bounty/LINK field logic. [⏻] logout is now a
 * compact `.chip` calling `useAuth().logout()` + `navigate('/')` directly
 * (the same two calls LogoutButton.tsx makes) rather than mounting that
 * shared component — LogoutButton's own full-width `.logout-button` skin
 * is built for UserProfile.tsx's sidebar context, not a chip-sized icon
 * button; LogoutButton.tsx itself is untouched and still owns that use.
 */
type DossierTab = 'identity' | 'reputation' | 'service' | 'fleet' | 'colonies' | 'crew' | 'settings';

// REP badge color-grading (canon §05 L614: "reputation visible at all
// times, color-graded (blue/gray/red grammar)") — the artifact's OWN
// grammar, reused verbatim rather than invented (cockpit-redesign-v10-
// RATIFIED.html:538 — "red is dead" #FF5A6A / "gray struck the lawful"
// #9AA6B5 / "blue in good standing" #5FB8FF), bucketed off the backend's
// 8-tier `reputation_tier` string (personal_reputation_service.py:
// REPUTATION_TIERS). Mirrors ReputationPage.tsx's LEVEL_COLORS /
// RankDisplay.tsx's TIER_COLORS shape (Record<tier, color> + a lookup
// helper), not a new pattern. LAWFUL is the one tier that keeps the
// artifact's own fixed green (`--grn` #46D68C, cockpit-shell.css) — also
// the fallback for an unrecognized/missing tier, so the common lawful-
// player case still reads green rather than falling through to a
// different default.
const REP_TIER_COLORS: Record<string, string> = {
  Villain: '#FF5A6A',
  Criminal: '#FF5A6A',
  Outlaw: '#FF5A6A',
  Suspicious: '#9AA6B5',
  Neutral: '#9AA6B5',
  Lawful: '#46D68C',
  Heroic: '#5FB8FF',
  Legendary: '#5FB8FF',
};
const REP_TIER_COLOR_DEFAULT = '#46D68C';
const repTierColor = (tier: string): string => REP_TIER_COLORS[tier] || REP_TIER_COLOR_DEFAULT;

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
 * disabled "coming soon" placeholder, zero PayPal imports/calls.
 *
 * WO-UI1-CHROME-COMPLETE: also mounts inside the [⚙] gear's OWN popup (see
 * StatusBar's `sb-settings-popover` below) — the canonical home per
 * cockpit-redesign-v10-RATIFIED.html:502 ("settings is a popup, not a
 * place"). The dossier's SETTINGS tab stays too (a mirror, not a
 * regression — the WO's call). Since both surfaces can independently be
 * open at once (the dossier's outside-click dismissal only fires on
 * `mousedown`, so a keyboard user tabbing from the name-chip to the gear
 * without a click can have BOTH open simultaneously), `idPrefix` keeps the
 * slider's `id`/`htmlFor` pair unique per mount — two same-id inputs would
 * otherwise both exist in the DOM at once, an invalid-HTML footgun even
 * though each input's own onChange still fires correctly either way. */
const SettingsTab: React.FC<{ idPrefix?: string }> = ({ idPrefix = '' }) => {
  const { uiScale, setUiScale } = useSettings();
  const rangeId = `${idPrefix}sb-ui-scale-range`;

  const handleScaleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const next = parseFloat(e.target.value);
    if (Number.isFinite(next)) setUiScale(next);
  };

  const currentPercent = `${Math.round(uiScale * 100)}%`;

  return (
    <div className="sb-settings-tab">
      <div className="sb-settings-row">
        <label htmlFor={rangeId} className="sb-identity-k">
          UI SCALE
        </label>
        <div className="sb-settings-row-control">
          <input
            id={rangeId}
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
  const { user, logout } = useAuth();
  const { playerState } = useGame();
  const { linkStatus } = useWebSocket();
  const navigate = useNavigate();

  // [⏻] compact chip (nit c) — same two calls LogoutButton.tsx makes,
  // inlined here instead of mounting that component (its shared
  // `.logout-button` skin is built for UserProfile.tsx's full-width
  // sidebar context, not this row's icon-sized chip).
  const handleLogout = () => {
    logout();
    navigate('/');
  };

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

  // WO-UI1-CHROME-COMPLETE — [⚙] settings popover. Same shell idiom as the
  // dossier/location dropdowns above: own container/trigger/panel refs,
  // independent outside-click + Escape dismissal, focus-in-on-open /
  // focus-back-on-close (the panel itself is the focus target, mirroring
  // LocationDropdown's convention — a single-slider popup has no obvious
  // "first tab" the way the dossier does).
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);
  const settingsTriggerRef = useRef<HTMLButtonElement>(null);
  const settingsPanelRef = useRef<HTMLDivElement>(null);

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

  // Settings popover — outside-click / Escape dismissal (same pattern as
  // the dossier's own effect above, independent state).
  useEffect(() => {
    if (!settingsOpen) return;
    const handlePointer = (e: MouseEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setSettingsOpen(false);
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSettingsOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointer);
      document.removeEventListener('keydown', handleKey);
    };
  }, [settingsOpen]);

  // Settings popover — focus moves into the panel on open, returns to the
  // trigger on close (mirrors the dossier's wasDossierOpenRef edge-detection
  // idiom so it never steals focus on initial mount).
  const wasSettingsOpenRef = useRef(false);
  useEffect(() => {
    if (settingsOpen && !wasSettingsOpenRef.current) {
      settingsPanelRef.current?.focus();
    } else if (!settingsOpen && wasSettingsOpenRef.current) {
      settingsTriggerRef.current?.focus();
    }
    wasSettingsOpenRef.current = settingsOpen;
  }, [settingsOpen]);

  // Pixel a11y REVISE #1 — WAI-ARIA tabs pattern keyboard nav: Left/Right
  // cycle tabs (wrapping), Home/End jump to first/last; each moves BOTH the
  // active tab (automatic activation — this console has no separate
  // "activate" step) and DOM focus to the newly-active tab (roving
  // tabindex, set below).
  const handleTablistKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const currentIndex = DOSSIER_TABS.findIndex((t) => t.id === activeTab);
    // No initializer: every case below assigns it, and the unmatched-key
    // default returns BEFORE nextIndex is ever read (so an unmatched key
    // moves neither focus nor the active tab) — the old `= currentIndex`
    // init was never actually read on any reachable path.
    let nextIndex: number;
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

  // REP badge (nit a) — tier + SIGNED personal_reputation, e.g. "Trusted
  // +250" (artifact: "LAWFUL +300"). NOT tier-only (the prior content).
  // Color-graded via `repTierColor()` (WO-UI5-RETIREMENT+GLASS rep-lane) --
  // RESTORES the per-tier grading the shell-transplant's fixed green had
  // dropped; see REP_TIER_COLORS above + the `.vit.repb` rule in
  // statusbar.css for the CSS-var mechanism.
  const repTier = playerState?.reputation_tier || 'Neutral';
  const personalRep = playerState?.personal_reputation ?? 0;
  const repSign = personalRep >= 0 ? '+' : '';
  const repColor = repTierColor(repTier);

  return (
    <div className="sbar status-bar">
      {/* [👤 name ▾] — dossier dropdown. `.chip.who` (cockpit-shell.css)
          already appends " ▾" via ::after -- no manual caret span here
          (LocationDropdown's OWN trigger, out of this leaf's scope, now
          re-classed to `.chip.loc` the same way -- WO-UI0-SHELL-TRANSPLANT
          integration cleanup item 4). Pilot name-color is an inline style,
          not a class rule --
          `.chip.who`'s own `color` (2 classes) would otherwise always beat
          a bare single-class `.sb-name-chip` rule regardless of CSS load
          order. */}
      <div className="sb-dossier" ref={dossierRef}>
        <button
          type="button"
          ref={nameChipRef}
          className="chip who sb-chip sb-name-chip"
          style={{ color: playerState?.name_color || '#00D9FF' }}
          onClick={() => setDossierOpen((o) => !o)}
          aria-haspopup="dialog"
          aria-expanded={dossierOpen}
          aria-controls="sb-dossier-menu"
        >
          <span className="sb-chip-icon" aria-hidden="true">👤</span>
          <span className="sb-name-text">{user?.username || '—'}</span>
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

      {/* [◉ location ▾] — RegionOwnerControls (sub-part b) wired in at
          integration. LocationDropdown owns its own trigger/panel markup
          (a sibling leaf's file, out of this WO's scope) — now carries
          `.chip.loc` (WO-UI0-SHELL-TRANSPLANT integration cleanup item 4). */}
      <LocationDropdown>
        <RegionOwnerControls />
      </LocationDropdown>

      {/* Pushes the vitals + right-hand icon chips to the row's right edge
          (artifact `.grow`, cockpit-shell.css: `flex:1`) — a real spacer,
          not the vitals cluster itself doing the growing (that job used to
          live on `.sb-vitals`; see that class's own trimmed rule below). */}
      <span className="grow" aria-hidden="true" />

      {/* vitals + REP badge. `.sb-vitals` is now `display:contents` (see
          its CSS) -- a pure DOM grouping node (GameLayout.
          statusBarIntegration.test.tsx still queries its presence as the
          "StatusBar's vitals cluster exists" pin) whose `.vit` children lay
          out as direct flex items of `.sbar` itself, picking up that row's
          own `gap`. */}
      <div className="sb-vitals">
        <span className="vit sb-stat sb-credits" title="Credits">
          {formatCredits(playerState?.credits)}
        </span>
        <span className={`vit sb-stat sb-turns${lowTurns ? ' sb-turns-low' : ''}`} title={turnsTitle}>
          <span className="sb-k"><TurnsIcon /></span>
          <span className="sb-v sb-turns-stack">
            <span className="sb-turns-count">
              {turnsNow.toLocaleString()}
              {typeof maxTurns === 'number' && <span className="sb-sub">/{maxTurns.toLocaleString()}</span>}
            </span>
            {regenPerHr > 0 && <span className="sb-regen">+{Math.round(regenPerHr)}/hr</span>}
          </span>
        </span>
        <span className="vit sb-stat sb-drones">
          <span
            className="sb-drone"
            title="Attack drones"
            aria-label={`Attack drones ${playerState?.attack_drones ?? 0}`}
          >
            <span aria-hidden="true">⚔</span> <b>{playerState?.attack_drones ?? 0}</b>
          </span>
          <span
            className="sb-drone"
            title="Defense drones"
            aria-label={`Defense drones ${playerState?.defense_drones ?? 0}`}
          >
            <span aria-hidden="true">🛡</span> <b>{playerState?.defense_drones ?? 0}</b>
          </span>
        </span>
        <span className="vit sb-stat sb-mines" title="Mines">
          <span className="sb-k"><MineIcon /></span>
          <b>{playerState?.mines ?? 0}</b>
        </span>
        <span className={`vit sb-stat sb-link sb-link--${linkStatus}`} title={linkTitle}>
          <span className="sb-k">LINK</span>
          <span className="sb-v">{linkLabel}</span>
        </span>
        {bounty > 0 && (
          <span className="vit sb-stat sb-bounty" title="Bounty on your head">
            <span className="sb-k">BOUNTY</span>
            <span className="sb-v">{formatCredits(bounty)}</span>
          </span>
        )}
        <span
          className="vit repb"
          style={{ '--rep-color': repColor } as React.CSSProperties}
          title={`Reputation tier: ${repTier}`}
          aria-label={`Reputation: ${repTier} ${repSign}${personalRep}`}
        >
          {repTier} {repSign}{personalRep}
        </span>
      </div>

      {/* [⚙] settings — popup (WO-UI1-CHROME-COMPLETE; canon L502 "settings
          is a popup, not a place"). Right-anchored (`.sb-settings-popup`
          overrides `.sb-dropdown`'s default left:0) since this trigger sits
          at the row's far-right edge — a left-anchored panel would overflow
          the viewport. Plain `.chip` (artifact: `<button class="chip">⚙
          </button>`) -- the old fixed-size `.sb-icon-btn` square skin is
          retired, superseded by `.chip`'s own box-model. */}
      <div className="sb-settings-popover" ref={settingsRef}>
        <button
          type="button"
          ref={settingsTriggerRef}
          className="chip sb-settings-btn"
          onClick={() => setSettingsOpen((o) => !o)}
          aria-haspopup="dialog"
          aria-expanded={settingsOpen}
          aria-controls="sb-settings-popup"
          aria-label="Settings"
          title="Settings"
        >
          ⚙
        </button>
        {settingsOpen && (
          <div
            id="sb-settings-popup"
            ref={settingsPanelRef}
            tabIndex={-1}
            className="sb-dropdown sb-settings-popup"
            role="dialog"
            aria-label="Settings"
          >
            <SettingsTab idPrefix="popup-" />
          </div>
        )}
      </div>

      {/* [⏻] logout (nit c) — a compact `.chip` (artifact: `<button
          class="chip">⏻</button>`), not the old full-width LogoutButton
          strip. Calls the same logout()+navigate('/') LogoutButton.tsx
          uses; that shared component is untouched and still owns
          UserProfile.tsx's full-width use. */}
      <button
        type="button"
        className="chip sb-logout-chip"
        onClick={handleLogout}
        aria-label="Log out"
        title="Log out"
      >
        ⏻
      </button>
    </div>
  );
};

export default StatusBar;

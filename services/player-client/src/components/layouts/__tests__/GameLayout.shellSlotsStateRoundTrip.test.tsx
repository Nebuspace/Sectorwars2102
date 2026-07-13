// @vitest-environment jsdom
/**
 * GameLayout — shell-slots portal + state round-trip (WO-UI0-SHELL-
 * TRANSPLANT's STATE-PRESERVATION GATE).
 *
 * Pins the Accept criterion verbatim: MFDProvider (GameLayout.tsx) and
 * teleprinterDisplayMode (GameLayout.tsx) MUST stay owned by GameLayout
 * (persistent across navigation) — render, set MFD-B->COMM + teleprinter->
 * mid-panel, navigate /game -> /game/map -> back to /game, assert both
 * preserved plus the scene still present. Plus the portal-lifecycle asserts
 * the WO calls out by name: `.band` empty (no orphan node, no "Target
 * container is not a DOM node") the instant the index route's content
 * unmounts on nav-away, and exactly ONE `.cockpit-windshield` inside `.band`
 * after returning (no duplicate/leaked portal instance).
 *
 * Uses a MINIMAL stand-in index-route component (StubIndexPage) that
 * exercises the EXACT mechanism GameDashboard.tsx uses — `useShellSlots()`
 * + `bandEl ? createPortal(...) : <inline fallback>` — rather than mounting
 * the real 3600+-line GameDashboard (whose OWN portal wiring + fallback-
 * safety for its 6 existing GameDashboard.*.test.tsx files, none of which
 * wrap it in a real GameLayout/bandEl, is covered separately: those files
 * stay green precisely BECAUSE of the same inline-fallback design proven
 * here). This isolates the SHELL's contract (context plumbing + portal
 * lifecycle across a real react-router navigation) from GameDashboard's own
 * content, which is what this WO is actually responsible for proving.
 *
 * MFDScreen is real-context-connected but content-stubbed: mocked to a
 * lightweight component that still calls the REAL `useMFDScreenInternal`/
 * `useMFD` (MFDContext.tsx is NOT mocked in this file) so it registers with
 * and reads from the SAME MFDProvider instance GameLayout renders — this is
 * what lets `selectPage('sidebar-b', 'comms-crew')` (fired from a button in
 * StubIndexPage, mirroring a real softkey click) actually flip MFD-B and
 * have that survive the nav, without needing the real (API-backed) MFD page
 * content components. Teleprinter is the REAL, unmocked component (same
 * seam as GameLayout.teleprinterDisplayModes.test.tsx) — mid-panel is
 * entered via a real click on its own single mode-toggle control
 * (WO-UI-MAX-BATCH-1's `.tp-mode-toggle`, ticker->mid-panel), exactly as a
 * player would. Annunciator/the toast-banner children are stubbed as
 * irrelevant chrome (matches every sibling GameLayout test).
 */
import React, { act, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { createRoot } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom does not implement scrollIntoView -- the real (unmocked) Teleprinter
// calls it on mount/update.
Element.prototype.scrollIntoView = vi.fn();

let mockRequiresFirstLogin = false;
vi.mock('../../../contexts/FirstLoginContext', () => ({
  useFirstLogin: () => ({ requiresFirstLogin: mockRequiresFirstLogin }),
}));

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1', username: 'commander' }, logout: vi.fn() }),
}));

const HYDRATED_PLAYER_STATE = {
  id: 'player-1',
  username: 'commander',
  credits: 10_000,
  turns: 400,
  max_turns: 500,
  current_sector_id: 1,
  is_docked: false,
  is_landed: false,
  defense_drones: 0,
  attack_drones: 0,
  mines: 0,
  personal_reputation: 0,
  reputation_tier: 'Neutral',
  name_color: '#00D9FF',
  military_rank: 'Cadet',
};

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: HYDRATED_PLAYER_STATE,
    isLoading: false,
    isRefreshing: false,
    refreshPlayerState: vi.fn(),
    unreadMessageCount: 0,
    currentSector: { name: 'Sol', sector_id: 1, sector_number: 1, region_name: null, region_id: null, type: 'STANDARD', hazard_level: 0 },
    stationsInSector: [],
    planetsInSector: [],
    dockAtStation: vi.fn(),
    undockFromStation: vi.fn(),
    landOnPlanet: vi.fn(),
    leavePlanet: vi.fn(),
    markMessageRead: vi.fn().mockResolvedValue(undefined),
  }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    ariaMessages: [],
    notifications: [],
    linkStatus: 'up',
    sendARIAMessage: vi.fn(),
    isConnected: true,
    npcCombatSignal: 0,
    lastNpcCombatInitiated: null,
    newMessageSignal: 0,
    lastNewMessage: null,
  }),
}));

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({
    status: 'idle',
    course: null,
    pauseReason: null,
    lastPlot: null,
    plotCourse: vi.fn(),
    engage: vi.fn(),
    abort: vi.fn(),
  }),
}));

vi.mock('../../hud/Annunciator', () => ({ default: () => <div data-testid="annunciator-stub" /> }));
vi.mock('../../ranking/MedalToast', () => ({ default: () => null }));
vi.mock('../../comms/PriorityHailConsumer', () => ({ default: () => null }));
vi.mock('../../auth/WelcomeBackToast', () => ({ default: () => null }));
vi.mock('../../combat/NpcCombatBanner', () => ({ default: () => null }));
vi.mock('../../onboarding/FirstSessionObjectives', () => ({ default: () => null }));

// Content-stubbed but REAL-context-connected MFDScreen: registers with (and
// reads from) the actual MFDProvider instance GameLayout renders, so
// selectPage()/activeFor() calls from StubIndexPage below are visible here
// -- proving MFDProvider's state, not just its identity, survives the nav.
vi.mock('../../mfd/MFDScreen', async () => {
  const { useMFD, useMFDScreenInternal } = await import('../../mfd/MFDContext');
  const Stub: React.FC<{ config: { screenId: string; pageIds: string[]; defaultPageId: string } }> = ({ config }) => {
    const { registerScreen } = useMFDScreenInternal();
    const { activeFor } = useMFD();
    useEffect(() => {
      registerScreen(config.screenId, config.pageIds as never, config.defaultPageId as never, config.defaultPageId as never);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [config.screenId]);
    return (
      <div data-testid={`mfd-screen-${config.screenId}`} data-active-page={activeFor(config.screenId) ?? ''} />
    );
  };
  return { default: Stub };
});

import GameShellRoute from '../GameShellRoute';
import { useShellSlots } from '../ShellContext';
import { useMFD } from '../../mfd/MFDContext';

/** Mirrors GameDashboard.tsx's exact mechanism: a windshield node, portaled
 * into `.band` when the slot is published, falling back to the SAME node
 * rendered inline when it isn't (GameLayout unmounted/not yet committed) --
 * see ShellContext.ts's own doc-comment for why this fallback exists. */
function StubIndexPage() {
  const { bandEl } = useShellSlots();
  const { selectPage } = useMFD();
  const windshield = (
    <div className="cockpit-windshield" data-testid="windshield-scene">SCENE</div>
  );
  return (
    <div data-testid="stub-index-page">
      {bandEl ? createPortal(windshield, bandEl) : windshield}
      <button data-testid="set-comm" onClick={() => selectPage('sidebar-b', 'comms-crew' as never)}>
        SET COMM
      </button>
    </div>
  );
}

function StubMapPage() {
  return <div data-testid="stub-map-page">MAP PAGE</div>;
}

function buildGameRouter() {
  return createMemoryRouter(
    [
      {
        path: '/game',
        element: <GameShellRoute />,
        children: [
          { index: true, element: <StubIndexPage /> },
          { path: 'map', element: <StubMapPage /> },
        ],
      },
    ],
    { initialEntries: ['/game'] },
  );
}

describe('GameLayout — shell-slots portal + MFDProvider/teleprinterDisplayMode survive navigation', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mockRequiresFirstLogin = false;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    errorSpy.mockRestore();
  });

  it('MFD-B selection + teleprinter mid-panel survive a /game -> /game/map -> /game round trip; the band portal never orphans or duplicates', async () => {
    const router = buildGameRouter();

    // ── Mount at /game ──────────────────────────────────────────────────
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });

    expect(container.querySelector('[data-testid="stub-index-page"]')).not.toBeNull();
    // Scene is present, portaled INTO .band (not rendered inline -- bandEl
    // is real by the time this settles).
    expect(container.querySelectorAll('.band .cockpit-windshield').length).toBe(1);
    expect(container.querySelector('[data-testid="windshield-scene"]')).not.toBeNull();

    // MFD-B starts on its default page.
    const mfdB = () => container.querySelector('[data-testid="mfd-screen-sidebar-b"]');
    expect(mfdB()?.getAttribute('data-active-page')).toBe('nav-position');

    // ── Set MFD-B -> COMM ────────────────────────────────────────────────
    const setCommBtn = container.querySelector('[data-testid="set-comm"]') as HTMLButtonElement;
    expect(setCommBtn).not.toBeNull();
    await act(async () => {
      setCommBtn.click();
    });
    expect(mfdB()?.getAttribute('data-active-page')).toBe('comms-crew');

    // ── Set teleprinter -> mid-panel via its own real single mode toggle ──
    const panelBtn = container.querySelector('.tkey.tp-mode-toggle') as HTMLButtonElement;
    expect(panelBtn).not.toBeNull();
    await act(async () => {
      panelBtn.click();
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(container.querySelector('.teleprinter')?.className).toContain('tp-mid-panel');
    expect(container.querySelector('.game-container')?.className).toContain('tp-mid-panel');
    // The MFD-B/MFD-A fold: only the merged sidebar-a-folded screen mounts.
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-b"]')).toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a-folded"]')).not.toBeNull();

    // ── Navigate away: /game -> /game/map ────────────────────────────────
    await act(async () => {
      await router.navigate('/game/map');
    });

    expect(container.querySelector('[data-testid="stub-map-page"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="stub-index-page"]')).toBeNull();
    // Portal asserts (WO-UI0-SHELL-TRANSPLANT Accept, verbatim): `.band` is
    // empty -- no orphaned windshield node left behind by the unmounted
    // portal source, and (via errorSpy below) no "Target container is not
    // a DOM node" React warning either.
    expect(container.querySelector('.band')).not.toBeNull(); // the slot itself persists (GameLayout owns it)
    expect(container.querySelector('.band .cockpit-windshield')).toBeNull();
    expect(container.querySelector('[data-testid="windshield-scene"]')).toBeNull();
    // The shell chrome itself never unmounted across the nav.
    expect(container.querySelector('.game-layout-wrapper')).not.toBeNull();

    // ── Navigate back: /game/map -> /game ────────────────────────────────
    await act(async () => {
      await router.navigate('/game');
    });

    expect(container.querySelector('[data-testid="stub-index-page"]')).not.toBeNull();
    // Scene is back, portaled into `.band` -- and there is exactly ONE
    // instance (no leaked/duplicate portal from the prior mount).
    expect(container.querySelectorAll('.band .cockpit-windshield').length).toBe(1);

    // MFDProvider state survived the whole round trip (never reset).
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-a-folded"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mfd-screen-sidebar-b"]')).toBeNull();

    // teleprinterDisplayMode survived the whole round trip too.
    expect(container.querySelector('.teleprinter')?.className).toContain('tp-mid-panel');
    expect(container.querySelector('.game-container')?.className).toContain('tp-mid-panel');

    // Collapse back out of mid-panel and confirm the COMM selection itself
    // (not just the fold) survived underneath it the whole time. The strict
    // 3-state cycle (WO-UI-MAX-BATCH-1) has no direct mid-panel->ticker
    // jump any more -- two clicks: mid-panel -> full-overlay -> ticker.
    let collapseBtn = container.querySelector('.tp-display-btn.tp-mode-toggle') as HTMLButtonElement;
    await act(async () => {
      collapseBtn.click(); // mid-panel -> full-overlay
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    collapseBtn = container.querySelector('.tp-display-btn.tp-mode-toggle') as HTMLButtonElement;
    await act(async () => {
      collapseBtn.click(); // full-overlay -> ticker
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(container.querySelector('.teleprinter')?.className).toContain('tp-ticker');
    expect(mfdB()?.getAttribute('data-active-page')).toBe('comms-crew');

    expect(errorSpy).not.toHaveBeenCalled();
  });
});

// @vitest-environment jsdom
/**
 * RegionOwnerControls — live-mount smoke for the WO-UI0-STATUSBAR sub-part
 * (b) extraction. Pins that relocating the region/governance/owner-tools
 * bundle out of GameDashboard's `id="location"` HudChip into its own
 * self-contained component did NOT change any gate: every conditional here
 * is traced 1:1 against the original block (GameDashboard.tsx :2466-2537,
 * :3630-3665) —
 *   - GOVERNANCE renders iff `currentSector.region_id` is truthy (ownership
 *     independent — every player with a region sees it)
 *   - the multi-region `<select>` picker renders iff NOT an owner yet AND
 *     `ownedRegionChoices.length > 0` (the ERR_AMBIGUOUS_REGION_OWNER path)
 *   - INVITE CONTROL / TRADEDOCK CONSTRUCTION render iff `isRegionOwner`
 *     (`ownedRegionId !== null`)
 * regionOwnerAPI is mocked at the module boundary (not fetch), mirroring the
 * established RegionTradeDockPanel.test.tsx / PlayerVitalsHud.lowTurns.test.tsx
 * house pattern: createRoot + act(), no RTL.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGetMyRegion, mockNavigate } = vi.hoisted(() => ({
  mockGetMyRegion: vi.fn(),
  mockNavigate: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  regionOwnerAPI: {
    getMyRegion: mockGetMyRegion,
  },
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

let mockCurrentSector: { region_id?: string | null; region_name?: string | null } | null = {
  region_id: 'sector-region-1',
  region_name: 'Testopia',
};

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ currentSector: mockCurrentSector }),
}));

// RegionInvitePanel / RegionTradeDockPanel are reused verbatim (not rebuilt);
// stub their internals here so this smoke stays scoped to the extraction's
// own gating logic, not their independent, already-covered behavior.
vi.mock('../RegionInvitePanel', () => ({
  default: ({ regionId }: { regionId: string }) => (
    <div data-testid="invite-panel">invite-panel:{regionId}</div>
  ),
}));
vi.mock('../RegionTradeDockPanel', () => ({
  default: ({ regionId }: { regionId: string | null }) => (
    <div data-testid="tradedock-panel">tradedock-panel:{regionId}</div>
  ),
}));

import RegionOwnerControls from '../RegionOwnerControls';

// Two-microtask flush for the getMyRegion probe effect — established idiom
// (see RegionTradeDockPanel.test.tsx).
const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

const AMBIGUOUS_ERR = Object.assign(new Error('Ambiguous owner'), {
  code: 'ERR_AMBIGUOUS_REGION_OWNER',
  regions: [
    { id: 'region-a', name: 'alpha', display_name: 'Alpha Region' },
    { id: 'region-b', name: 'beta', display_name: 'Beta Region' },
  ],
});

// player-client's vitest.config.ts has no setupFiles / IS_REACT_ACT_ENVIRONMENT,
// so every react-dom/client createRoot()+act() mount logs this exact harness
// string via console.error regardless of the component under test (confirmed
// pre-existing gap, not a real render error — see
// components/tactical/__tests__/NavigationMap.chartPolish.test.tsx and the
// project's agent-memory note vitest-act-environment-noise.md). Filter it out
// before asserting "no unexpected errors".
const ACT_ENV_NOISE = 'The current testing environment is not configured to support act(...)';
const unexpectedErrors = (spy: ReturnType<typeof vi.spyOn>) =>
  spy.mock.calls.filter((call) => !String(call[0]).includes(ACT_ENV_NOISE));

describe('RegionOwnerControls', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGetMyRegion.mockReset();
    mockNavigate.mockReset();
    mockCurrentSector = { region_id: 'sector-region-1', region_name: 'Testopia' };
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    consoleErrorSpy.mockRestore();
  });

  it('owner: shows GOVERNANCE + INVITE CONTROL + TRADEDOCK CONSTRUCTION, no picker', async () => {
    mockGetMyRegion.mockResolvedValueOnce({ id: 'owned-region-1', display_name: 'Owned Region' });

    await act(async () => {
      root.render(<RegionOwnerControls />);
    });
    await flush();

    expect(container.querySelector('.hud-region-governance-btn')).not.toBeNull();
    expect(container.querySelector('.hud-region-invite-btn')).not.toBeNull();
    expect(container.querySelector('.hud-region-tradedock-btn')).not.toBeNull();
    expect(container.querySelector('.hud-region-owner-picker')).toBeNull();
    expect(unexpectedErrors(consoleErrorSpy)).toHaveLength(0);
  });

  it('non-owner (404, no choices): shows GOVERNANCE only, no owner buttons/picker, no error line', async () => {
    mockGetMyRegion.mockRejectedValueOnce(new Error('Not Found'));

    await act(async () => {
      root.render(<RegionOwnerControls />);
    });
    await flush();

    expect(container.querySelector('.hud-region-governance-btn')).not.toBeNull();
    expect(container.querySelector('.hud-region-invite-btn')).toBeNull();
    expect(container.querySelector('.hud-region-tradedock-btn')).toBeNull();
    expect(container.querySelector('.hud-region-owner-picker')).toBeNull();
    // The expected "not an owner" 404 stays silent -- no probe-status line.
    expect(container.querySelector('.hud-region-probe-status')).toBeNull();
    expect(container.querySelector('.hud-region-probe-error')).toBeNull();
    expect(unexpectedErrors(consoleErrorSpy)).toHaveLength(0);
  });

  // Pixel a11y REVISE #4 — the probe previously rendered nothing while
  // in-flight (buttons silently popped in/out) and had no error surface on
  // a genuine transient failure.
  it('shows a "Loading region status…" line while the probe is in-flight, then clears it', async () => {
    let resolveProbe!: (value: { id: string; display_name: string }) => void;
    mockGetMyRegion.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveProbe = resolve;
      })
    );

    await act(async () => {
      root.render(<RegionOwnerControls />);
    });

    // Still in-flight: loading line shown, owner-derived controls withheld
    // (GOVERNANCE is independent of the probe and can render immediately).
    expect(container.querySelector('.hud-region-probe-status')?.textContent).toBe(
      'Loading region status…'
    );
    expect(container.querySelector('.hud-region-invite-btn')).toBeNull();
    expect(container.querySelector('.hud-region-tradedock-btn')).toBeNull();

    await act(async () => {
      resolveProbe({ id: 'owned-region-1', display_name: 'Owned Region' });
    });
    await flush();

    expect(container.querySelector('.hud-region-probe-status')).toBeNull();
    expect(container.querySelector('.hud-region-invite-btn')).not.toBeNull();
    expect(unexpectedErrors(consoleErrorSpy)).toHaveLength(0);
  });

  it('surfaces a brief error line on a genuine transient failure (not the expected not-found case)', async () => {
    mockGetMyRegion.mockRejectedValueOnce(new Error('Network Error'));

    await act(async () => {
      root.render(<RegionOwnerControls />);
    });
    await flush();

    expect(container.querySelector('.hud-region-probe-status')).toBeNull();
    expect(container.querySelector('.hud-region-probe-error')?.textContent).toBe(
      'Region status unavailable — try again shortly.'
    );
    // GOVERNANCE is unaffected (not owner-gated); owner-derived controls stay hidden.
    expect(container.querySelector('.hud-region-governance-btn')).not.toBeNull();
    expect(container.querySelector('.hud-region-invite-btn')).toBeNull();
    expect(unexpectedErrors(consoleErrorSpy)).toHaveLength(0);
  });

  it('ambiguous multi-region owner: shows the picker, not the invite/tradedock buttons; selecting flips to owner gates', async () => {
    mockGetMyRegion.mockRejectedValueOnce(AMBIGUOUS_ERR);

    await act(async () => {
      root.render(<RegionOwnerControls />);
    });
    await flush();

    const picker = container.querySelector('.hud-region-owner-picker') as HTMLSelectElement | null;
    expect(picker).not.toBeNull();
    expect(container.querySelectorAll('.hud-region-owner-picker option')).toHaveLength(3); // placeholder + 2 regions
    expect(container.querySelector('.hud-region-invite-btn')).toBeNull();
    expect(container.querySelector('.hud-region-tradedock-btn')).toBeNull();

    // Selecting a region flips isRegionOwner -> true; picker disappears,
    // owner buttons appear (verbatim selectOwnedRegion behavior).
    await act(async () => {
      picker!.value = 'region-a';
      picker!.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(container.querySelector('.hud-region-owner-picker')).toBeNull();
    expect(container.querySelector('.hud-region-invite-btn')).not.toBeNull();
    expect(container.querySelector('.hud-region-tradedock-btn')).not.toBeNull();
    expect(unexpectedErrors(consoleErrorSpy)).toHaveLength(0);
  });

  it('GOVERNANCE hides when currentSector has no region_id, independent of ownership', async () => {
    mockCurrentSector = { region_id: null };
    mockGetMyRegion.mockResolvedValueOnce({ id: 'owned-region-1', display_name: 'Owned Region' });

    await act(async () => {
      root.render(<RegionOwnerControls />);
    });
    await flush();

    expect(container.querySelector('.hud-region-governance-btn')).toBeNull();
    // Ownership gates are unaffected by the region_id-less sector.
    expect(container.querySelector('.hud-region-invite-btn')).not.toBeNull();
    expect(unexpectedErrors(consoleErrorSpy)).toHaveLength(0);
  });

  it('GOVERNANCE click navigates to /game/governance', async () => {
    mockGetMyRegion.mockRejectedValueOnce(new Error('Not Found'));

    await act(async () => {
      root.render(<RegionOwnerControls />);
    });
    await flush();

    const btn = container.querySelector('.hud-region-governance-btn') as HTMLButtonElement;
    await act(async () => {
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(mockNavigate).toHaveBeenCalledWith('/game/governance');
  });

  it('INVITE CONTROL / TRADEDOCK CONSTRUCTION open their portal panels', async () => {
    mockGetMyRegion.mockResolvedValueOnce({ id: 'owned-region-1', display_name: 'Owned Region' });

    await act(async () => {
      root.render(<RegionOwnerControls />);
    });
    await flush();

    const inviteBtn = container.querySelector('.hud-region-invite-btn') as HTMLButtonElement;
    await act(async () => {
      inviteBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(document.body.querySelector('[data-testid="invite-panel"]')?.textContent).toBe(
      'invite-panel:owned-region-1'
    );

    const tradedockBtn = container.querySelector('.hud-region-tradedock-btn') as HTMLButtonElement;
    await act(async () => {
      tradedockBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(document.body.querySelector('[data-testid="tradedock-panel"]')?.textContent).toBe(
      'tradedock-panel:owned-region-1'
    );

    expect(unexpectedErrors(consoleErrorSpy)).toHaveLength(0);
  });
});

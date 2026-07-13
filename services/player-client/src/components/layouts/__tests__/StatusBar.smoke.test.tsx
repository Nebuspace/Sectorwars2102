// @vitest-environment jsdom
/**
 * StatusBar — live-mount console-error smoke (WO-UI0-STATUSBAR sub-part a).
 *
 * Golden/pipeline tests go stale under HMR (per notebook precedent); this is
 * the real proof: mount the actual component tree (StatusBar + both
 * dropdowns + all six dossier tabs) with a representative playerState and
 * assert ZERO console.error output while clicking through every tab, plus
 * that the vitals/REP badge/dropdown a11y roles are actually present in the
 * DOM. Mirrors StatusBar.lowTurns.test.tsx's seam (jsdom +
 * react-dom/client createRoot + act(), no RTL in this project) and
 * RankDisplay/RankProgress/MedalShowcase.test.tsx's services/api mock shape.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// This suite asserts ZERO console.error output, so the React 18
// "current testing environment is not configured to support act(...)"
// warning (a harness-level quirk observed baseline-wide in this repo's
// jsdom+createRoot+act tests -- e.g. RankDisplay.test.tsx,
// StatusBar.lowTurns.test.tsx -- unrelated to any component bug) must
// be silenced at the source rather than filtered after the fact.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'commander' }, logout: vi.fn() }),
}));

const mockPlayerState = {
  id: 'player-1',
  username: 'commander',
  credits: 125000,
  turns: 480,
  max_turns: 1000,
  turn_regen_per_hour: 20,
  current_sector_id: 42,
  is_docked: false,
  is_landed: false,
  defense_drones: 12,
  attack_drones: 8,
  mines: 3,
  personal_reputation: 250,
  reputation_tier: 'Trusted',
  name_color: '#00FFFF',
  military_rank: 'Commander',
  bounty_total: 0,
};

const mockSector = {
  id: 42,
  sector_id: 42,
  name: 'Sector 42',
  type: 'normal',
  region_name: 'Fringe Rylan',
  hazard_level: 0,
  radiation_level: 0,
  resources: {},
  players_present: [],
};

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    currentSector: mockSector,
    ships: [],
    currentShip: null,
    setCurrentShip: vi.fn(),
  }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    linkStatus: 'up',
    reputationEventSignal: 0,
    lastReputationChanged: null,
    lastTeamReputationChanged: null,
    medalAwardedSignal: 0,
  }),
}));

const mockGetReputation = vi.fn();
const mockGetRank = vi.fn();
const mockGetProgress = vi.fn();
const mockGetMedals = vi.fn();
const mockGetOwnedPlanets = vi.fn();
const mockGetTeam = vi.fn();
const mockGetPermissions = vi.fn();

vi.mock('../../../services/api', () => ({
  factionAPI: {
    getReputation: (...a: unknown[]) => mockGetReputation(...a),
  },
  rankingAPI: {
    getRank: (...a: unknown[]) => mockGetRank(...a),
    getProgress: (...a: unknown[]) => mockGetProgress(...a),
    getMedals: (...a: unknown[]) => mockGetMedals(...a),
  },
  gameAPI: {
    planetary: {
      getOwnedPlanets: (...a: unknown[]) => mockGetOwnedPlanets(...a),
    },
  },
  // CREW tab (WO-UI5-DOSSIER) -- mockPlayerState below carries no team_id,
  // so TeamSummaryTab never actually calls these, but the mock shape is
  // kept complete/realistic rather than relying on that never changing.
  teamAPI: {
    getTeam: (...a: unknown[]) => mockGetTeam(...a),
    getPermissions: (...a: unknown[]) => mockGetPermissions(...a),
  },
}));

const FULL_RANK = {
  player_id: 'player-1',
  username: 'commander',
  current_rank: 'Commander',
  rank_level: 5,
  rank_tier: 'Officer',
  rank_points: 4200,
  points_to_next_rank: 800,
  next_rank: 'Captain',
  next_rank_points_required: 5000,
  progress_percent: 84,
  bonuses: {
    trading_discount_percent: 5,
    max_turns_bonus: 10,
    combat_damage_bonus_percent: 3,
  },
  is_max_rank: false,
};

const FULL_PROGRESS = {
  player_id: 'player-1',
  username: 'commander',
  current_rank: 'Commander',
  rank_level: 5,
  rank_tier: 'Officer',
  rank_points: 4200,
  points_to_next_rank: 800,
  next_rank: 'Captain',
  next_rank_points_required: 5000,
  progress_percent: 84,
  is_max_rank: false,
  stats: {
    combat_victories: 12,
    total_trades: 340,
    trade_volume: 1500000,
    exploration_score: 88,
    credits: 125000,
    turns_remaining: 480,
  },
  requirements: [{ name: 'Combat Wins', current: 12, required: 20, met: false }],
};

mockGetReputation.mockResolvedValue([]);
mockGetRank.mockResolvedValue(FULL_RANK);
mockGetProgress.mockResolvedValue(FULL_PROGRESS);
mockGetMedals.mockResolvedValue({ earned: [], available: [] });
mockGetOwnedPlanets.mockResolvedValue({ planets: [] });

import StatusBar from '../StatusBar';
import { SettingsProvider } from '../../../contexts/SettingsContext';

describe('StatusBar — live-mount smoke', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  const flush = async () => {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  beforeEach(() => {
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

  it('mounts with zero console errors and renders vitals + REP badge', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <SettingsProvider>
            <StatusBar />
          </SettingsProvider>
        </MemoryRouter>
      );
    });
    await flush();

    expect(container.querySelector('.status-bar')).not.toBeNull();
    expect(container.querySelector('.sb-credits')?.textContent).toContain('125,000');
    expect(container.querySelector('.sb-drones')).not.toBeNull();
    expect(container.querySelector('.sb-link')).not.toBeNull();
    expect(container.querySelector('.sb-rep-badge')?.textContent).toBe('Trusted');
    expect(container.querySelector('.sb-name-chip')).not.toBeNull();
    expect(container.querySelector('.sb-location-chip')).not.toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('opens the dossier dropdown with tablist/tab/tabpanel a11y roles and cycles every tab error-free', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <SettingsProvider>
            <StatusBar />
          </SettingsProvider>
        </MemoryRouter>
      );
    });
    await flush();

    const nameChip = container.querySelector('.sb-name-chip') as HTMLButtonElement;
    expect(nameChip.getAttribute('aria-haspopup')).toBe('dialog');
    expect(nameChip.getAttribute('aria-expanded')).toBe('false');

    await act(async () => {
      nameChip.click();
    });
    await flush();

    expect(nameChip.getAttribute('aria-expanded')).toBe('true');
    const tablist = container.querySelector('[role="tablist"]');
    expect(tablist).not.toBeNull();
    const tabpanel = container.querySelector('[role="tabpanel"]');
    expect(tabpanel).not.toBeNull();
    const tabs = container.querySelectorAll('[role="tab"]');
    expect(tabs.length).toBe(7);
    // exactly one tab starts selected (Identity, the default active tab)
    expect(Array.from(tabs).filter((t) => t.getAttribute('aria-selected') === 'true').length).toBe(1);

    for (const tab of Array.from(tabs) as HTMLButtonElement[]) {
      await act(async () => {
        tab.click();
      });
      await flush();
      expect(tab.getAttribute('aria-selected')).toBe('true');
      expect(container.querySelector('.sb-dossier-body')).not.toBeNull();
    }

    expect(errorSpy).not.toHaveBeenCalled();
  });

  // Pixel a11y fix (WO-UI5-DOSSIER gate review): aria-disabled is invalid
  // on a non-interactive <span> -- the subscription stub must instead carry
  // an accessible name via aria-label, and stay a pure stub (no PayPal).
  it('SETTINGS tab: the subscription stub has an accessible name and no aria-disabled', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <SettingsProvider>
            <StatusBar />
          </SettingsProvider>
        </MemoryRouter>
      );
    });
    await flush();

    const nameChip = container.querySelector('.sb-name-chip') as HTMLButtonElement;
    await act(async () => {
      nameChip.click();
    });
    await flush();

    const settingsTab = Array.from(container.querySelectorAll('[role="tab"]')).find(
      (t) => t.textContent === 'SETTINGS'
    ) as HTMLButtonElement;
    await act(async () => {
      settingsTab.click();
    });
    await flush();

    const stub = container.querySelector('.sb-settings-subscription-stub');
    expect(stub).not.toBeNull();
    expect(stub?.getAttribute('aria-label')).toBe('Subscription — coming soon');
    expect(stub?.getAttribute('aria-disabled')).toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  // Pixel a11y REVISE #1 (WCAG 2.1 Level A) — the WAI-ARIA tabs pattern:
  // Left/Right cycle tabs (wrapping), Home/End jump to first/last, and
  // focus follows the newly-active tab (roving tabindex).
  it('dossier tablist: arrow/Home/End keys change the active tab AND move focus (roving tabindex)', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <SettingsProvider>
            <StatusBar />
          </SettingsProvider>
        </MemoryRouter>
      );
    });
    await flush();

    const nameChip = container.querySelector('.sb-name-chip') as HTMLButtonElement;
    await act(async () => {
      nameChip.click();
    });
    await flush();

    const tablist = container.querySelector('[role="tablist"]') as HTMLElement;
    const tabs = Array.from(container.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];
    expect(tabs.length).toBe(7);

    const pressKey = async (key: string) => {
      await act(async () => {
        tablist.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
      });
      await flush();
    };

    // Default: Identity (index 0) selected, roving tabindex = 0 there only.
    expect(tabs[0].tabIndex).toBe(0);
    expect(tabs[1].tabIndex).toBe(-1);

    // ArrowRight: 0 -> 1, active tab AND focus both follow.
    await pressKey('ArrowRight');
    expect(tabs[1].getAttribute('aria-selected')).toBe('true');
    expect(tabs[0].getAttribute('aria-selected')).toBe('false');
    expect(document.activeElement).toBe(tabs[1]);
    expect(tabs[1].tabIndex).toBe(0);
    expect(tabs[0].tabIndex).toBe(-1);

    // End: jump straight to the last tab (Settings, index 6).
    await pressKey('End');
    expect(tabs[6].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs[6]);

    // ArrowRight wraps from the last tab back to the first.
    await pressKey('ArrowRight');
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs[0]);

    // ArrowLeft wraps from the first tab back to the last.
    await pressKey('ArrowLeft');
    expect(tabs[6].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs[6]);

    // Home: jump straight back to the first tab.
    await pressKey('Home');
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs[0]);

    expect(errorSpy).not.toHaveBeenCalled();
  });

  // Pixel a11y REVISE #2 — focus enters the panel on open (the active tab)
  // and RETURNS to the trigger on close, however triggered.
  it('dossier: focus moves to the active tab on open and returns to the trigger on close', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <SettingsProvider>
            <StatusBar />
          </SettingsProvider>
        </MemoryRouter>
      );
    });
    await flush();

    const nameChip = container.querySelector('.sb-name-chip') as HTMLButtonElement;
    await act(async () => {
      nameChip.click();
    });
    await flush();

    const tabs = Array.from(container.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];
    expect(document.activeElement).toBe(tabs[0]); // Identity, the default active tab

    // Escape closes and returns focus to the trigger.
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    await flush();

    expect(nameChip.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(nameChip);

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('opens the location dropdown shell with the current sector/region header', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <SettingsProvider>
            <StatusBar />
          </SettingsProvider>
        </MemoryRouter>
      );
    });
    await flush();

    const locationChip = container.querySelector('.sb-location-chip') as HTMLButtonElement;
    // Pixel a11y REVISE #3 — the panel is role="region" (informational, not
    // a dialog/menu); no aria-haspopup, rely on aria-expanded + aria-controls.
    expect(locationChip.getAttribute('aria-haspopup')).toBeNull();
    expect(locationChip.getAttribute('aria-expanded')).toBe('false');
    expect(locationChip.getAttribute('aria-controls')).toBe('sb-location-panel');

    await act(async () => {
      locationChip.click();
    });
    await flush();

    const panel = container.querySelector('[role="region"][aria-label="Location"]');
    expect(panel).not.toBeNull();
    expect(panel?.textContent).toContain('Sector 42');
    expect(panel?.textContent).toContain('Fringe Rylan');
    // Pixel a11y REVISE #2 — focus moves into the panel on open.
    expect(document.activeElement).toBe(panel);

    // ...and returns to the trigger on close.
    await act(async () => {
      locationChip.click();
    });
    await flush();
    expect(document.activeElement).toBe(locationChip);

    expect(errorSpy).not.toHaveBeenCalled();
  });

  // WO-UI1-CHROME-COMPLETE — [⚙] gear opens a settings POPUP (canon L502:
  // "settings is a popup, not a place"), not the old /game/settings
  // nav-link. Real SettingsProvider (not mocked) so dragging the slider
  // provably drives the SAME `--ui-scale` root variable that scales the
  // whole cockpit -- not a mocked stand-in for setUiScale.
  it('[⚙] opens a settings popup with a working UI-scale slider that scales the whole cockpit', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <SettingsProvider>
            <StatusBar />
          </SettingsProvider>
        </MemoryRouter>
      );
    });
    await flush();

    const gearBtn = container.querySelector('.sb-settings-btn') as HTMLButtonElement;
    expect(gearBtn.tagName).toBe('BUTTON'); // not an <a>/Link to /game/settings anymore
    expect(gearBtn.getAttribute('aria-haspopup')).toBe('dialog');
    expect(gearBtn.getAttribute('aria-expanded')).toBe('false');
    expect(gearBtn.getAttribute('aria-controls')).toBe('sb-settings-popup');

    await act(async () => {
      gearBtn.click();
    });
    await flush();

    expect(gearBtn.getAttribute('aria-expanded')).toBe('true');
    const panel = container.querySelector('#sb-settings-popup[role="dialog"][aria-label="Settings"]');
    expect(panel).not.toBeNull();
    // Focus moves into the panel on open (mirrors the dossier/location idiom).
    expect(document.activeElement).toBe(panel);

    const slider = panel!.querySelector('input[type="range"]') as HTMLInputElement;
    expect(slider).not.toBeNull();
    // Accessible label: a <label htmlFor> pointing at this exact input id.
    const label = panel!.querySelector('label[for="popup-sb-ui-scale-range"]');
    expect(label).not.toBeNull();
    expect(label?.textContent).toBe('UI SCALE');
    expect(slider.id).toBe('popup-sb-ui-scale-range');

    expect(slider.value).toBe('1');
    expect(document.documentElement.style.getPropertyValue('--ui-scale')).toBe('1');

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(slider, '0.8');
      slider.dispatchEvent(new Event('input', { bubbles: true }));
      slider.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await flush();

    // setUiScale fired for real -- the same #root zoom variable moved.
    expect(document.documentElement.style.getPropertyValue('--ui-scale')).toBe('0.8');
    expect(panel!.textContent).toContain('80%');

    // Esc closes and returns focus to the trigger.
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    await flush();

    expect(gearBtn.getAttribute('aria-expanded')).toBe('false');
    expect(container.querySelector('#sb-settings-popup')).toBeNull();
    expect(document.activeElement).toBe(gearBtn);

    expect(errorSpy).not.toHaveBeenCalled();
  });

  // The dossier's own SETTINGS tab is a deliberate mirror (WO's call), not
  // a regression -- both surfaces render the same slider, distinguished by
  // idPrefix so their ids never collide even if both are open at once.
  it('dossier SETTINGS tab is unregressed alongside the new [⚙] popup', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <SettingsProvider>
            <StatusBar />
          </SettingsProvider>
        </MemoryRouter>
      );
    });
    await flush();

    const nameChip = container.querySelector('.sb-name-chip') as HTMLButtonElement;
    await act(async () => {
      nameChip.click();
    });
    await flush();

    const settingsTab = Array.from(container.querySelectorAll('[role="tab"]')).find(
      (t) => t.textContent === 'SETTINGS'
    ) as HTMLButtonElement;
    await act(async () => {
      settingsTab.click();
    });
    await flush();

    const dossierSlider = container.querySelector('#sb-ui-scale-range') as HTMLInputElement;
    expect(dossierSlider).not.toBeNull();
    expect(container.querySelector('#popup-sb-ui-scale-range')).toBeNull(); // gear popup not open here

    expect(errorSpy).not.toHaveBeenCalled();
  });
});

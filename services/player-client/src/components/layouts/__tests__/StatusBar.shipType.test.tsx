// @vitest-environment jsdom
/**
 * StatusBar — ship-type readout beside the name (WO-HUD-SHIPTYPE).
 *
 * Max (live-playtest, 2026-07-19): regrouped the StatusBar's top-left
 * readouts into IDENTITY (name + the ship the player is CURRENTLY flying)
 * and LOCATION (region + sector, LocationDropdown.tsx's own concern).
 * Mirrors StatusBar.lowTurns.test.tsx's mutable-mock-per-test seam (jsdom +
 * react-dom/client createRoot + act(), no RTL) so `currentShip` can vary
 * between the populated and null cases within one file.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'commander' }, logout: vi.fn() }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ linkStatus: 'up' }),
}));

const basePlayer = {
  credits: 1000,
  turns: 500,
  max_turns: 1000,
  attack_drones: 0,
  defense_drones: 0,
  mines: 0,
  name_color: '#00D9FF',
  military_rank: 'Recruit',
  reputation_tier: 'Neutral',
  personal_reputation: 0,
  bounty_total: 0,
};

const baseSector = {
  sector_id: 7,
  sector_number: 7,
  type: 'normal',
  region_name: 'Fringe Rylan',
};

let mockCurrentShip: { id: string; type: string } | null = null;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: basePlayer,
    currentSector: baseSector,
    planetsInSector: [],
    stationsInSector: [],
    currentShip: mockCurrentShip,
    setCurrentShip: vi.fn(),
  }),
}));

import StatusBar from '../StatusBar';
import { SettingsProvider } from '../../../contexts/SettingsContext';

describe('StatusBar — ship-type readout', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    mockCurrentShip = null;
  });

  const render = async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <SettingsProvider>
            <StatusBar />
          </SettingsProvider>
        </MemoryRouter>
      );
    });
  };

  it('renders the current ship TYPE, formatted, beside the name -- e.g. LIGHT_FREIGHTER -> "Light Freighter"', async () => {
    mockCurrentShip = { id: 'ship-1', type: 'LIGHT_FREIGHTER' };
    await render();

    const badge = container.querySelector('.sb-shiptype-chip');
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toContain('Light Freighter');

    // Sits beside the name chip -- both are DOM siblings inside .sbar,
    // name chip's container ancestor precedes the ship-type badge, which
    // itself precedes the location chip (IDENTITY, then LOCATION).
    const chipOrder = Array.from(
      container.querySelectorAll('.sb-dossier, .sb-shiptype-chip, .sb-location-chip')
    ).map((el) => el.className);
    expect(chipOrder[0]).toContain('sb-dossier');
    expect(chipOrder[1]).toContain('sb-shiptype-chip');
    expect(chipOrder[2]).toContain('sb-location-chip');
  });

  it('is dynamic -- a different ship type formats differently, not a hardcoded string', async () => {
    mockCurrentShip = { id: 'ship-2', type: 'WARP_JUMPER' };
    await render();

    expect(container.querySelector('.sb-shiptype-chip')?.textContent).toContain('Warp Jumper');
    expect(container.querySelector('.sb-shiptype-chip')?.textContent).not.toContain('Light Freighter');
  });

  it('gracefully degrades to a neutral placeholder when there is no current ship (never crashes, never renders "undefined")', async () => {
    mockCurrentShip = null;
    await render();

    const badge = container.querySelector('.sb-shiptype-chip');
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toContain('—');
    expect(badge?.textContent).not.toMatch(/undefined/i);
  });
});

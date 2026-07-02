// @vitest-environment jsdom
/**
 * Dashboard — cargo resource-bar icon wiring (WO-ARCH-RES-3B-PC-RESIDUAL-
 * LITERALS, accept 3).
 *
 * The retired :80-82 ternary hardcoded ⚡ for fuel and 🪨 for ore (plus 🌾
 * for food/organics, 🔧 for anything else). This asserts the cargo bar now
 * renders the shared-catalog glyph per cargo entry type — including the
 * documented ⚡→⛽ and 🪨→⛏️ visual convergences — and that an unrecognized
 * cargo key degrades to the generic 📦 rather than the old catch-all 🔧.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'tester' }, logout: vi.fn() }),
}));

// UserProfile (auth chrome, react-router-dependent LogoutButton) is out of
// scope for this WO's icon-wiring assertion — stubbed to keep the test
// focused on the cargo resource bar under test.
vi.mock('../../auth/UserProfile', () => ({
  default: () => <div data-testid="user-profile-stub" />,
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { credits: 1000, turns: 10, current_sector_id: 1 },
    ships: [],
    currentShip: { cargo: { ore: 5, fuel: 3, mystery_goo: 2 } },
    currentSector: { name: 'Sol' },
    isLoading: false,
  }),
}));

import Dashboard from '../Dashboard';

describe('Dashboard — cargo resource icons', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.clearAllMocks();
  });

  it("renders ⛏️ for ore, ⛽ for fuel, and 📦 for an unrecognized cargo key", async () => {
    await act(async () => {
      root.render(
        <Dashboard apiStatus="ok" apiMessage="" apiEnvironment="dev" />
      );
    });

    const items = Array.from(container.querySelectorAll('.resource-item'));
    const iconFor = (cssClass: string) =>
      items.find((el) => el.classList.contains(cssClass))
        ?.querySelector('.resource-icon')?.textContent;

    expect(iconFor('ore')).toBe('⛏️');
    expect(iconFor('fuel')).toBe('⛽');
    expect(iconFor('mystery_goo')).toBe('📦');
  });
});

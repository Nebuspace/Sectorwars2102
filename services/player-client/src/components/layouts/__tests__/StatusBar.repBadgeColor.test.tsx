// @vitest-environment jsdom
/**
 * StatusBar — REP badge color-grading (WO-UI5-RETIREMENT+GLASS rep-lane).
 *
 * Canon (cockpit-redesign-v10-RATIFIED.html:614, quoted verbatim in
 * DECISIONS #9's ACCEPT criteria): "reputation visible at all times,
 * color-graded (blue/gray/red grammar)". WO-UI0-SHELL-TRANSPLANT Leaf L1
 * re-classed `.repb` onto the artifact's fixed `color: var(--grn)`
 * (cockpit-shell.css), dropping the per-tier grading the badge carried
 * pre-transplant -- this pins the restoration: a mocked non-lawful tier
 * gets the graded color, lawful (and any unrecognized tier) stays the
 * artifact's own green. Mirrors StatusBar.lowTurns.test.tsx's minimal
 * mutable-mock seam (jsdom + react-dom/client createRoot + act(), no RTL)
 * -- the dossier panel (and therefore ReputationPage/ServiceRecordTab/etc)
 * never mounts unless the dossier is opened, so no factionAPI/rankingAPI/
 * teamAPI mocks are needed here either.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'commander' } }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ linkStatus: 'up' }),
}));

let mockPlayerState: Record<string, unknown> | null = null;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    isLoading: false,
    refreshPlayerState: vi.fn(),
  }),
}));

import StatusBar from '../StatusBar';

const basePlayer = {
  credits: 1000,
  max_turns: 1000,
  attack_drones: 0,
  defense_drones: 0,
  mines: 0,
  name_color: '#00D9FF',
  military_rank: 'Recruit',
  personal_reputation: 0,
};

describe('StatusBar — REP badge color-grading', () => {
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
    mockPlayerState = null;
  });

  const renderWithTier = async (tier: string) => {
    mockPlayerState = { ...basePlayer, reputation_tier: tier };
    await act(async () => {
      root.render(
        <MemoryRouter>
          <StatusBar />
        </MemoryRouter>
      );
    });
    return container.querySelector('.repb') as HTMLElement;
  };

  it('hostile/criminal tiers (Villain/Criminal/Outlaw) grade red — #FF5A6A', async () => {
    for (const tier of ['Villain', 'Criminal', 'Outlaw']) {
      const badge = await renderWithTier(tier);
      expect(badge.style.getPropertyValue('--rep-color')).toBe('#FF5A6A');
    }
  });

  it('suspicious/neutral tiers grade gray — #9AA6B5', async () => {
    for (const tier of ['Suspicious', 'Neutral']) {
      const badge = await renderWithTier(tier);
      expect(badge.style.getPropertyValue('--rep-color')).toBe('#9AA6B5');
    }
  });

  it('trusted/allied tiers (Heroic/Legendary) grade blue — #5FB8FF', async () => {
    for (const tier of ['Heroic', 'Legendary']) {
      const badge = await renderWithTier(tier);
      expect(badge.style.getPropertyValue('--rep-color')).toBe('#5FB8FF');
    }
  });

  it('Lawful keeps the artifact\'s own fixed green — #46D68C', async () => {
    const badge = await renderWithTier('Lawful');
    expect(badge.style.getPropertyValue('--rep-color')).toBe('#46D68C');
  });

  it('an unrecognized/missing tier falls back to the lawful-positive green default, not an undifferentiated color', async () => {
    const badge = await renderWithTier('SomeUnknownTier');
    expect(badge.style.getPropertyValue('--rep-color')).toBe('#46D68C');
  });

  it('preserves the accessible name, uppercase text, and signed value across grading -- only the color changes', async () => {
    mockPlayerState = { ...basePlayer, reputation_tier: 'Villain', personal_reputation: -900 };
    await act(async () => {
      root.render(
        <MemoryRouter>
          <StatusBar />
        </MemoryRouter>
      );
    });
    const badge = container.querySelector('.repb') as HTMLElement;
    expect(badge.getAttribute('aria-label')).toBe('Reputation: Villain -900');
    expect(badge.getAttribute('title')).toBe('Reputation tier: Villain');
    expect(badge.textContent).toBe('Villain -900');
    expect(badge.style.getPropertyValue('--rep-color')).toBe('#FF5A6A');
  });
});

// @vitest-environment jsdom
/**
 * CombatInterface — port assault honesty (Scroll-Law/honesty self-audit).
 *
 * Backend engage targetType=="port" is live (attack_port). The weapons
 * console must offer ENGAGE for owned-by-other ports, not a stale
 * "ASSAULT NOT AUTHORIZED" hard-block. Own / unowned stay disabled with
 * honest notes.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const ME = 'player-me';
const OTHER = 'player-other';

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: ME, turns: 10 },
    currentShip: { id: 'ship-1', name: 'Test Hull' },
    currentSector: { players_present: [] },
    planetsInSector: [],
    stationsInSector: [
      {
        id: 'st-owned',
        name: 'Rival Dock',
        type: 'trading_post',
        status: 'active',
        sector_id: 1,
        owner_id: OTHER,
        services: {},
      },
      {
        id: 'st-mine',
        name: 'My Dock',
        type: 'trading_post',
        status: 'active',
        sector_id: 1,
        owner_id: ME,
        services: {},
      },
      {
        id: 'st-free',
        name: 'NPC Dock',
        type: 'trading_post',
        status: 'active',
        sector_id: 1,
        owner_id: null,
        services: {},
      },
    ],
    refreshPlayerState: vi.fn(),
  }),
}));

vi.mock('../../../services/api', () => ({
  gameAPI: {
    combat: {
      engage: vi.fn(),
      getStatus: vi.fn(),
    },
  },
}));

vi.mock('../../../utils/security/inputValidation', () => ({
  InputValidator: {
    validateCombatParams: () => ({ valid: true, errors: [] }),
    clearRateLimit: vi.fn(),
  },
  SecurityAudit: { logSecurityEvent: vi.fn() },
}));

vi.mock('../../cockpit/CockpitInstrument', () => ({
  default: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="weapons-shell">{children}</div>
  ),
}));

import { CombatInterface } from '../CombatInterface';

describe('CombatInterface — port assault honesty', () => {
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
    vi.clearAllMocks();
  });

  it('enables ENGAGE for rival-owned ports; disables own and unowned with honest notes', async () => {
    await act(async () => {
      // Standalone route (no onClose) → target selection list
      root.render(<CombatInterface />);
    });

    const text = container.textContent || '';
    expect(text).toContain('Rival Dock');
    expect(text).toContain('My Dock');
    expect(text).toContain('NPC Dock');
    expect(text).not.toContain('ASSAULT NOT AUTHORIZED');
    expect(text).toContain('YOUR PORT');
    expect(text).toContain('UNOWNED — NO ASSAULT');

    const engageButtons = Array.from(
      container.querySelectorAll('button.engage-target-btn'),
    ) as HTMLButtonElement[];
    const labels = engageButtons.map((b) => (b.textContent || '').trim());
    expect(labels).toContain('ENGAGE');
    expect(labels).toContain('YOUR PORT');
    expect(labels).toContain('UNOWNED — NO ASSAULT');

    const rivalEngage = engageButtons.find((b) => (b.textContent || '').trim() === 'ENGAGE');
    expect(rivalEngage).toBeDefined();
    expect(rivalEngage!.disabled).toBe(false);
  });
});

// @vitest-environment jsdom
/**
 * LEG-305 — Combat HUD pre-engage readout: hull/shield/cargo, weapon,
 * turn-cost preview. No Defend/Evade/Flee buttons.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const ME = 'player-me';

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: ME, turns: 40, defense_drones: 2 },
    currentShip: {
      id: 'ship-1',
      name: 'Test Hull',
      type: 'SCOUT_SHIP',
      cargo_capacity: 100,
      cargo: { used: 25, contents: { ore: 25 } },
      combat: {
        attack_rating: 12,
        defense_rating: 8,
        hull: 80,
        max_hull: 100,
        shields: 20,
        max_shields: 40,
      },
    },
    currentSector: { players_present: [] },
    planetsInSector: [],
    stationsInSector: [],
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
    checkRateLimit: () => true,
  },
  SecurityAudit: { log: vi.fn(), logSecurityEvent: vi.fn() },
}));

vi.mock('../../cockpit/CockpitInstrument', () => ({
  default: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="weapons-shell">{children}</div>
  ),
}));

import { CombatInterface } from '../CombatInterface';
import {
  defaultWeaponForShipType,
  previewTurnCost,
} from '../combatHudHelpers';

describe('combatHudHelpers', () => {
  it('maps canon default weapons', () => {
    expect(defaultWeaponForShipType('SCOUT_SHIP')).toBe('EMP');
    expect(defaultWeaponForShipType('DEFENDER')).toBe('Plasma');
    expect(defaultWeaponForShipType('CARRIER')).toBe('Missile');
    expect(defaultWeaponForShipType('WARP_JUMPER')).toBe('Plasma');
    expect(defaultWeaponForShipType('LIGHT_FREIGHTER')).toBe('Laser');
  });

  it('previews ship turn cost from tip-mirrored table (min 2)', () => {
    expect(previewTurnCost({ targetType: 'ship', shipType: 'SCOUT_SHIP' })).toBe(5);
    expect(previewTurnCost({ targetType: 'ship', shipType: 'ESCAPE_POD' })).toBe(10000);
    expect(previewTurnCost({ targetType: 'ship', attackTurnCost: 1 })).toBe(2);
  });

  it('uses canon planet/port turn cost of 3', () => {
    expect(previewTurnCost({ targetType: 'planet' })).toBe(3);
    expect(previewTurnCost({ targetType: 'port' })).toBe(3);
  });

  it('returns null for unknown ship types without tip field', () => {
    expect(previewTurnCost({ targetType: 'ship', shipType: 'UNKNOWN_HULL' })).toBeNull();
  });
});

describe('CombatInterface — LEG-305 HUD preview', () => {
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

  it('renders hull/shield/cargo/weapon and ship turn-cost preview; no mid-fight actions', async () => {
    await act(async () => {
      root.render(
        <CombatInterface
          target={{
            id: 'ship-rival',
            name: 'Rival Scout',
            type: 'ship',
            shipType: 'SCOUT_SHIP',
          }}
          onClose={() => undefined}
        />,
      );
    });

    const stats = container.querySelector('[data-testid="combat-hud-player-stats"]');
    expect(stats?.textContent).toContain('Hull: 80 / 100');
    expect(stats?.textContent).toContain('Shields: 20 / 40');
    expect(stats?.textContent).toContain('Cargo: 25 / 100');
    expect(stats?.textContent).toContain('Weapon: EMP');

    const cost = container.querySelector('[data-testid="combat-turn-cost-preview"]');
    expect(cost?.textContent).toBe('Turn cost: 5');

    const text = container.textContent || '';
    expect(text).not.toMatch(/\bDefend\b/i);
    expect(text).not.toMatch(/\bEvade\b/i);
    expect(text).not.toMatch(/\bFlee\b/i);
    expect(text).toMatch(/ENGAGE COMBAT/);
  });

  it('shows planet turn cost 3 from canon table', async () => {
    await act(async () => {
      root.render(
        <CombatInterface
          target={{ id: 'planet-1', name: 'Dustball', type: 'planet' }}
          onClose={() => undefined}
        />,
      );
    });
    expect(
      container.querySelector('[data-testid="combat-turn-cost-preview"]')?.textContent,
    ).toBe('Turn cost: 3');
  });
});

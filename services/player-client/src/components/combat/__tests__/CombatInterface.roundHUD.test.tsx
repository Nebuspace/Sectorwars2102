// @vitest-environment jsdom
/**
 * CombatInterface — round HUD bars + actions (LEG-4148).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const ME = 'player-me';
const OPP = 'player-other';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

// Module-level mock functions — must be at module scope for vi.mock hoisting.
const mockEngage = vi.fn();
const mockGetStatus = vi.fn();
const mockRetreat = vi.fn();
const mockRefresh = vi.fn();

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: ME, turns: 10, is_docked: false, is_landed: false },
    currentShip: {
      id: 'ship-1',
      name: 'Test Hull',
      type: 'SCOUT',
      cargo: { used: 25, capacity: 100, contents: {} },
      cargo_capacity: 100,
      sector_id: 1,
      current_speed: 0,
      base_speed: 0,
      combat: { hull: 50, max_hull: 100, shields: 20, max_shields: 40 },
      maintenance: {},
      is_flagship: false,
      purchase_value: 0,
      current_value: 0,
      genesis_devices: 0,
      max_genesis_devices: 0,
    },
    currentSector: {
      name: 'Sol',
      sector_number: 1,
      hazard_level: 0,
      radiation_level: 0,
      players_present: [
        {
          player_id: OPP,
          ship_id: 'opp-ship-1',
          ship_type: 'SCOUT',
          ship_name: 'Opponent Hull',
          username: 'Opponent',
          is_npc: false,
          attack_turn_cost: 7,
          combat: { hull: 80, max_hull: 100, shields: 10, max_shields: 25 },
          cargo: { used: 10, capacity: 50 },
        },
      ],
    },
    planetsInSector: [],
    stationsInSector: [],
    refreshPlayerState: (...args: unknown[]) => mockRefresh(...args),
  }),
}));

vi.mock('../../../services/api', () => ({
  gameAPI: {
    combat: {
      engage: (...args: unknown[]) => mockEngage(...args),
      getStatus: (...args: unknown[]) => mockGetStatus(...args),
      retreat: (...args: unknown[]) => mockRetreat(...args),
    },
  },
}));

vi.mock('../../../utils/security/inputValidation', () => ({
  InputValidator: {
    validateCombatParams: () => ({ valid: true, errors: [] }),
    checkRateLimit: () => true,
    clearRateLimit: vi.fn(),
  },
  SecurityAudit: { log: vi.fn() },
}));

vi.mock('../../cockpit/CockpitInstrument', () => ({
  default: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../CombatHistoryPanel', () => ({
  CombatHistoryPanel: () => null,
}));

vi.mock('../CombatAdvicePanel', () => ({
  default: () => null,
}));

import { CombatInterface } from '../CombatInterface';

describe('CombatInterface round HUD (LEG-4148)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    mockEngage.mockReset();
    mockGetStatus.mockReset();
    mockRetreat.mockReset();
    mockRefresh.mockReset();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('renders hull/shield/cargo bars + Attack/Defend/Evade/Flee and calls combatAPI.retreat on Flee (LEG-4148)', async () => {
    let resolveEngage: ((v: unknown) => void) | null = null;
    mockEngage.mockReturnValue(
      new Promise((resolve) => {
        resolveEngage = resolve;
      }),
    );
    mockGetStatus.mockResolvedValue({
      status: 'completed',
      outcome: 'attacker_win',
      winner: ME,
      rounds: [
        { round: 1, actor: 'attacker', action: 'ship_destroyed', message: 'ok' },
      ],
      combatDuration: 1,
      creditsLooted: 0,
      cargoLooted: [],
    });
    mockRetreat.mockResolvedValue({
      success: false,
      message: 'Combat already resolved — use sector retreat to flee your current sector',
      turnsConsumed: 0,
      turnsRemaining: 0,
    });

    await act(async () => {
      root.render(<CombatInterface />);
    });

    // Click the target selection ENGAGE button (this triggers the async initiateCombat call).
    const engageTargetBtn = container.querySelector('button.engage-target-btn') as HTMLButtonElement;
    expect(engageTargetBtn).toBeTruthy();
    await act(async () => {
      engageTargetBtn.click();
      await flush();
    });

    // While engage is pending, the pre-engage panel should render the turn-cost preview.
    const turnPreview = container.querySelector('[data-testid="combat-turn-cost-preview"]');
    expect(turnPreview?.textContent).toContain('Costs 7 turn');

    // Resolve engage + status load.
    act(() => {
      resolveEngage?.({ status: 'initiated', combatId: 'c-1' });
    });
    await flush();

    // Active-combat round HUD bars.
    expect(container.querySelector('[data-testid="combat-hull-bar-player"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="combat-shield-bar-player"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="combat-cargo-bar-player"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="combat-hull-bar-opponent"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="combat-shield-bar-opponent"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="combat-cargo-bar-opponent"]')).toBeTruthy();

    // Buttons render.
    expect(container.textContent).toContain('Attack');
    expect(container.textContent).toContain('Defend');
    expect(container.textContent).toContain('Evade');
    expect(container.textContent).toContain('Flee');

    const fleeBtn = container.querySelector('[data-testid="combat-round-action-flee"]') as HTMLButtonElement;
    expect(fleeBtn).toBeTruthy();
    await act(async () => {
      fleeBtn.click();
      await flush();
    });

    expect(mockRetreat).toHaveBeenCalledWith('c-1');
  });
});

// @vitest-environment jsdom
/**
 * PlayerVitalsHud — low-turn scarcity warning (WO-PROG-TURN-VISIBILITY).
 *
 * Canon (sw2102-docs FEATURES/gameplay/turns.md "Player-facing affordances"):
 * "Low-turn warning UI hints when the pool is below thresholds (design:
 * <50)." Pins the boundary on both sides — 49 (warning) and 50 (no warning,
 * the boundary itself is NOT low) — plus that the warning never falsely
 * fires while playerState hasn't loaded yet (turnsNow's ?? 0 default must
 * not read as "low").
 *
 * Mirrors Dashboard.icons.test.tsx's seam: jsdom + react-dom/client
 * createRoot + act(), no RTL. LogoutButton (react-router's useNavigate) is
 * stubbed — it's shipped/out-of-scope chrome unrelated to this assertion,
 * same rationale Dashboard.icons.test.tsx uses to stub UserProfile.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'commander' } }),
}));

vi.mock('../../auth/LogoutButton', () => ({
  default: () => <button data-testid="logout-stub" />,
}));

// PlayerVitalsHud now reads linkStatus (WO-PUX-UPLINK-HUD) — stub the hook so
// this pre-existing low-turn suite doesn't need a real WebSocketProvider.
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

import PlayerVitalsHud from '../PlayerVitalsHud';

const basePlayer = {
  credits: 1000,
  max_turns: 1000,
  attack_drones: 0,
  defense_drones: 0,
  mines: 0,
  name_color: '#00D9FF',
  military_rank: 'Recruit',
  reputation_tier: 'Neutral',
  personal_reputation: 0,
};

describe('PlayerVitalsHud — low-turn warning', () => {
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

  it('applies the low-turns class at 49 (below the <50 threshold)', async () => {
    mockPlayerState = { ...basePlayer, turns: 49 };
    await act(async () => {
      root.render(<PlayerVitalsHud />);
    });

    expect(container.querySelector('.pvh-turns-low')).not.toBeNull();
  });

  it('does not apply the low-turns class at exactly 50 (the boundary is not low)', async () => {
    mockPlayerState = { ...basePlayer, turns: 50 };
    await act(async () => {
      root.render(<PlayerVitalsHud />);
    });

    expect(container.querySelector('.pvh-turns-low')).toBeNull();
  });

  it('does not warn while playerState has not loaded (no false "low" on the default 0)', async () => {
    mockPlayerState = null;
    await act(async () => {
      root.render(<PlayerVitalsHud />);
    });

    expect(container.querySelector('.pvh-turns-low')).toBeNull();
  });
});

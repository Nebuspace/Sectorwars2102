// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const autopilotState = { value: null as any };
const gameState = { value: { currentSector: { sector_id: 10, name: 'Stardock' } } as any };

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => autopilotState.value,
}));
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState.value,
}));

import AutopilotHud, { buildAutopilotChain } from '../AutopilotHud';

const HOPS = [
  { sector_id: 19, name: 'Sector 19' },
  { sector_id: 6, name: 'Sector 6' },
  { sector_id: 17, name: 'Sector 17' },
];

const course = {
  success: true as const,
  reachable: true as const,
  target_sector_id: 17,
  total_turns: 6,
  hops: HOPS.map((h) => ({
    ...h,
    turn_cost: 2,
    visited: true,
    safety_rating: 0.7,
    via_tunnel: false,
  })),
};

describe('buildAutopilotChain', () => {
  const origin = { sector_id: 10, name: 'Stardock' };

  it('lays out origin + hops as a chain, destination last', () => {
    const chain = buildAutopilotChain(origin, HOPS, 0);
    expect(chain.map((n) => n.sector_id)).toEqual([10, 19, 6, 17]);
    expect(chain[0].state).toBe('origin');
    expect(chain[chain.length - 1].state).toBe('destination');
  });

  it('marks the in-transit leg active and completed legs traversed', () => {
    // currentHopIndex=1 → flying origin→…→hop[1]; hop[0] already reached.
    const chain = buildAutopilotChain(origin, HOPS, 1);
    // dot for hop[0] (index 1 in chain) is reached
    expect(chain[1].state).toBe('reached');
    // dot for hop[1] (index 2) is the active transit target
    expect(chain[2].state).toBe('active');
    // connector into hop[0] traversed, into hop[1] active, into hop[2] pending
    expect(chain[1].legState).toBe('traversed');
    expect(chain[2].legState).toBe('active');
    expect(chain[3].legState).toBe('pending');
  });

  it('works with no captured origin yet', () => {
    const chain = buildAutopilotChain(null, HOPS, 0);
    expect(chain.map((n) => n.sector_id)).toEqual([19, 6, 17]);
    expect(chain[0].legState).toBe('active');
  });
});

describe('AutopilotHud rendering', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    gameState.value = { currentSector: { sector_id: 10, name: 'Stardock' } };
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('renders dots while engaged', () => {
    autopilotState.value = { course, status: 'engaged', currentHopIndex: 0 };
    act(() => root.render(<AutopilotHud />));
    expect(container.querySelector('.autopilot-hud')).not.toBeNull();
    // origin + 3 hops = 4 dots
    expect(container.querySelectorAll('.autopilot-hud__dot').length).toBe(4);
    expect(container.textContent).toContain('ARIA AUTOPILOT');
  });

  it('disappears on arrival', () => {
    autopilotState.value = { course, status: 'arrived', currentHopIndex: 2 };
    act(() => root.render(<AutopilotHud />));
    expect(container.querySelector('.autopilot-hud')).toBeNull();
  });

  it('is hidden when idle / no course', () => {
    autopilotState.value = { course: null, status: 'idle', currentHopIndex: 0 };
    act(() => root.render(<AutopilotHud />));
    expect(container.querySelector('.autopilot-hud')).toBeNull();
  });
});

// @vitest-environment jsdom
/**
 * NavPositionPage — MFD ops page (position fix + charted exits + autopilot
 * course). Mirrors CargoPage.test.tsx / QuantumPage.test.tsx's seam: jsdom +
 * react-dom/client createRoot + act(), no RTL, no new deps.
 *
 * Pins: the no-position-fix guard, every position field reading live off
 * currentSector (incl. the conditional REGION row), the autopilot-course
 * block appearing only mid-course (nextHop derived from course.hops
 * [currentHopIndex]) and its own COURSE TOTAL sub-condition, the empty-
 * exits state, and warps+tunnels rendering as tagged rows with the
 * over-budget turn-cost class.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../icons/TurnsIcon', () => ({
  TurnsIcon: () => <span data-testid="turns-icon" />,
}));

const baseSector = (overrides: Record<string, unknown> = {}) => ({
  id: 1,
  sector_id: 5,
  sector_number: 5,
  name: 'Alpha Reach',
  type: 'nebula',
  hazard_level: 2,
  radiation_level: 0,
  resources: {},
  players_present: [],
  ...overrides,
});

const move = (overrides: Record<string, unknown> = {}) => ({
  sector_id: 9,
  sector_number: 9,
  name: 'Beta Gate',
  type: 'normal',
  turn_cost: 1,
  can_afford: true,
  ...overrides,
});

let mockCurrentSector: ReturnType<typeof baseSector> | null = baseSector();
let mockAvailableMoves: { warps: ReturnType<typeof move>[]; tunnels: ReturnType<typeof move>[] } = {
  warps: [],
  tunnels: [],
};
let mockAutopilot: {
  course: { hops: Array<{ sector_id: number; name: string; turn_cost: number }>; total_turns?: number } | null;
  currentHopIndex: number;
} = { course: null, currentHopIndex: 0 };

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentSector: mockCurrentSector,
    availableMoves: mockAvailableMoves,
  }),
}));

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => mockAutopilot,
}));

import NavPositionPage from './NavPositionPage';

describe('NavPositionPage', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockCurrentSector = baseSector();
    mockAvailableMoves = { warps: [], tunnels: [] };
    mockAutopilot = { course: null, currentHopIndex: 0 };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<NavPositionPage />);
    });
  };

  const fieldValue = (label: string): string | undefined => {
    const fields = Array.from(container.querySelectorAll('.mfd-field'));
    const field = fields.find((f) => f.querySelector('.mfd-field-label')?.textContent === label);
    return field?.querySelector('.mfd-field-value')?.textContent ?? undefined;
  };

  it('shows NO POSITION FIX and no position fields when currentSector is null', async () => {
    mockCurrentSector = null;
    await mount();

    expect(container.querySelector('.mfd-insufficient')?.textContent).toBe('NO POSITION FIX');
    expect(fieldValue('SECTOR')).toBeUndefined();
  });

  it('renders SECTOR/NAME/TYPE/HAZARD bound to the real currentSector', async () => {
    mockCurrentSector = baseSector({
      sector_number: 12,
      name: 'Deep Reach',
      type: 'asteroid',
      hazard_level: 7,
    });
    await mount();

    expect(fieldValue('SECTOR')).toBe('12');
    expect(fieldValue('NAME')).toBe('Deep Reach');
    expect(fieldValue('TYPE')).toBe('asteroid');
    expect(fieldValue('HAZARD')).toBe('7');
  });

  it('falls back sector display to sector_id when sector_number is absent', async () => {
    mockCurrentSector = baseSector({ sector_number: undefined, sector_id: 44 });
    await mount();

    expect(fieldValue('SECTOR')).toBe('44');
  });

  it('omits the REGION field when region_name is absent', async () => {
    mockCurrentSector = baseSector({ region_name: null });
    await mount();
    expect(fieldValue('REGION')).toBeUndefined();
  });

  it('renders the REGION field when region_name is present', async () => {
    mockCurrentSector = baseSector({ region_name: 'Frontier Belt' });
    await mount();
    expect(fieldValue('REGION')).toBe('Frontier Belt');
  });

  it('shows no AUTOPILOT COURSE block when there is no active course', async () => {
    mockAutopilot = { course: null, currentHopIndex: 0 };
    await mount();

    expect(fieldValue('NEXT HOP')).toBeUndefined();
    expect(Array.from(container.querySelectorAll('.mfd-page-section-label')).map((n) => n.textContent)).not.toContain(
      'AUTOPILOT COURSE',
    );
  });

  it('shows the next hop + hop cost mid-course', async () => {
    mockAutopilot = {
      course: {
        hops: [
          { sector_id: 9, name: 'Beta Gate', turn_cost: 2 },
          { sector_id: 10, name: 'Gamma Point', turn_cost: 1 },
        ],
        total_turns: 5,
      },
      currentHopIndex: 0,
    };
    await mount();

    expect(fieldValue('NEXT HOP')).toBe('Beta Gate');
    expect(fieldValue('HOP COST')?.trim()).toContain('2');
    expect(fieldValue('COURSE TOTAL')?.trim()).toContain('5');
  });

  it('shows no course block once currentHopIndex has run past the last hop', async () => {
    mockAutopilot = {
      course: { hops: [{ sector_id: 9, name: 'Beta Gate', turn_cost: 2 }], total_turns: 2 },
      currentHopIndex: 1,
    };
    await mount();

    expect(fieldValue('NEXT HOP')).toBeUndefined();
  });

  it('omits COURSE TOTAL when total_turns is not a number', async () => {
    mockAutopilot = {
      course: { hops: [{ sector_id: 9, name: 'Beta Gate', turn_cost: 2 }] },
      currentHopIndex: 0,
    };
    await mount();

    expect(fieldValue('NEXT HOP')).toBe('Beta Gate');
    expect(fieldValue('COURSE TOTAL')).toBeUndefined();
  });

  it('shows NO CHARTED EXITS when both warps and tunnels are empty', async () => {
    mockAvailableMoves = { warps: [], tunnels: [] };
    await mount();

    expect(container.querySelector('.mfd-empty')?.textContent).toBe('NO CHARTED EXITS');
    expect(container.querySelectorAll('.mfd-page-nav-exit').length).toBe(0);
  });

  it('renders warp rows untagged and tunnel rows TUN-tagged', async () => {
    mockAvailableMoves = {
      warps: [move({ sector_id: 1, name: 'Warp Dest' })],
      tunnels: [move({ sector_id: 2, name: 'Tunnel Dest' })],
    };
    await mount();

    const rows = Array.from(container.querySelectorAll('.mfd-page-nav-exit'));
    expect(rows.length).toBe(2);
    const warpRow = rows.find((r) => r.querySelector('.mfd-page-nav-exit-name')?.textContent === 'Warp Dest');
    const tunnelRow = rows.find((r) => r.querySelector('.mfd-page-nav-exit-name')?.textContent === 'Tunnel Dest');
    expect(warpRow?.querySelector('.mfd-page-nav-exit-tag')).toBeNull();
    expect(tunnelRow?.querySelector('.mfd-page-nav-exit-tag')?.textContent).toBe('TUN');
  });

  it('marks an unaffordable exit with the "over" cost class', async () => {
    mockAvailableMoves = { warps: [move({ can_afford: false })], tunnels: [] };
    await mount();

    const cost = container.querySelector('.mfd-page-nav-exit-cost');
    expect(cost?.className).toContain('over');
  });

  it('does not mark an affordable exit with the "over" cost class', async () => {
    mockAvailableMoves = { warps: [move({ can_afford: true })], tunnels: [] };
    await mount();

    const cost = container.querySelector('.mfd-page-nav-exit-cost');
    expect(cost?.className).not.toContain('over');
  });

  it('falls back exit name to an em-dash when absent', async () => {
    mockAvailableMoves = { warps: [move({ name: '' })], tunnels: [] };
    await mount();

    expect(container.querySelector('.mfd-page-nav-exit-name')?.textContent).toBe('—');
  });
});

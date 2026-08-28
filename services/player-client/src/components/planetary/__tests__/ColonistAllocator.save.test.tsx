// @vitest-environment jsdom
/**
 * ColonistAllocator — save POST honest error copy (LEG-2868).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Planet } from '../../../types/planetary';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockAllocateColonists } = vi.hoisted(() => ({
  mockAllocateColonists: vi.fn(async () => ({ success: true })),
}));

vi.mock('../../../services/api', () => ({
  gameAPI: {
    planetary: {
      allocateColonists: mockAllocateColonists,
    },
  },
}));

import { ColonistAllocator, formatColonistAllocateError } from '../ColonistAllocator';

const PLANET: Planet = {
  id: 'planet-1',
  name: 'Test World',
  sectorId: '1',
  sectorName: 'Sol',
  planetType: 'TERRAN',
  colonists: 100,
  maxColonists: 1000,
  productionRates: { fuel: 10, organics: 10, equipment: 10, colonists: 1, research: 0 },
  allocations: { fuel: 30, organics: 30, equipment: 30, unused: 10 },
  buildings: [],
  defenses: { turrets: 0, shields: 0, drones: 0 },
  underSiege: false,
};

describe('ColonistAllocator — save POST honest errors', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockAllocateColonists.mockClear();
    mockAllocateColonists.mockResolvedValue({ success: true });
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

  const changeAllocation = async () => {
    await act(async () => {
      root.render(<ColonistAllocator planet={PLANET} />);
    });
    const fuelPreset = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('Fuel Focus'),
    );
    await act(async () => {
      fuelPreset!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  it('surfaces save 400 server detail in error banner', async () => {
    mockAllocateColonists.mockRejectedValue(
      Object.assign(new Error('Cannot allocate 120 colonists, only 100 available'), { status: 400 }),
    );
    await changeAllocation();

    const saveBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Save Assignments',
    );
    await act(async () => {
      saveBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });

    expect(container.textContent).toContain('Cannot allocate 120 colonists, only 100 available');
  });

  it('formatColonistAllocateError falls back when message is generic API Error', () => {
    expect(formatColonistAllocateError(new Error('API Error: 400'))).toBe('Failed to update allocations');
  });
});

// @vitest-environment jsdom
/**
 * LEG-3602 Soft-ORDER — PlanetManager Network Error densify Vitest.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { EmbeddedContext } from '../../cockpit/EmbeddedContext';

const PLANET = {
  id: 'planet-1',
  name: 'Test World',
  sectorId: '42',
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

const mockMoveToSector = vi.fn();

vi.mock('../../../services/api', () => ({
  gameAPI: {
    planetary: {
      getOwnedPlanets: vi.fn(),
    },
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ moveToSector: mockMoveToSector }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ planetaryEventSignal: 0 }),
}));

import { gameAPI } from '../../../services/api';
import {
  PlanetManager,
  formatPlanetLoadError,
  formatPlanetCourseError,
} from '../PlanetManager';

const getOwnedPlanets = gameAPI.planetary.getOwnedPlanets as ReturnType<typeof vi.fn>;

describe('PlanetManager Network Error densify (LEG-3602)', () => {
  it('formatPlanetLoadError falls back on axios Network Error', () => {
    expect(formatPlanetLoadError(new Error('Network Error'))).toBe('Failed to load planets');
    expect(formatPlanetLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('formatPlanetCourseError falls back on axios Network Error', () => {
    expect(formatPlanetCourseError(new Error('Network Error'))).toBe('Failed to set course to colony');
    expect(formatPlanetCourseError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });
});

describe('PlanetManager load + mutation Network Error densify (LEG-3602)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    getOwnedPlanets.mockReset();
    mockMoveToSector.mockReset();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('load Network Error surfaces Failed to load planets without raw transport text', async () => {
    getOwnedPlanets.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(
        <EmbeddedContext.Provider value={true}>
          <PlanetManager />
        </EmbeddedContext.Provider>,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toMatch(/Failed to load planets/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('set course Network Error surfaces honest fallback without raw transport text', async () => {
    getOwnedPlanets.mockResolvedValue({ planets: [PLANET] });
    mockMoveToSector.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(
        <EmbeddedContext.Provider value={true}>
          <PlanetManager />
        </EmbeddedContext.Provider>,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const courseBtn = Array.from(container.querySelectorAll('button')).find((btn) =>
      btn.textContent?.includes('Set course'),
    );
    expect(courseBtn).toBeTruthy();

    await act(async () => {
      courseBtn!.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toMatch(/Failed to set course to colony/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});

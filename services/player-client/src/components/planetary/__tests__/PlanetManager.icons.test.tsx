// @vitest-environment jsdom
/**
 * PlanetManager — colony roster COLUMNS glyphs (WO-ARCH-RES-3B-PC-RESIDUAL-
 * LITERALS, accept 4).
 *
 * COLUMNS was a module-level literal array with hardcoded '⛽ Fuel' / '🌿
 * Org' / '⚙️ Equip' header labels. This mounts the roster with one owned
 * planet (embedded, so the heavy GameLayout shell is skipped) and asserts
 * the rendered column headers carry the shared-catalog glyphs, matching
 * resourceIcon('fuel'|'organics'|'equipment') byte-for-byte.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { EmbeddedContext } from '../../cockpit/EmbeddedContext';
import { resourceIcon } from '../../../services/resourceCatalog';

const PLANET = {
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

vi.mock('../../../services/api', () => ({
  gameAPI: {
    planetary: {
      getOwnedPlanets: vi.fn(() => Promise.resolve({ planets: [PLANET] })),
    },
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ moveToSector: vi.fn() }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ planetaryEventSignal: 0 }),
}));

import { PlanetManager } from '../PlanetManager';

describe('PlanetManager — colony roster column glyphs', () => {
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

  it('renders the shared-catalog glyph in the Fuel/Org/Equip column headers', async () => {
    await act(async () => {
      root.render(
        <EmbeddedContext.Provider value={true}>
          <PlanetManager />
        </EmbeddedContext.Provider>
      );
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const headers = Array.from(container.querySelectorAll('[role="columnheader"]'))
      .map((el) => el.textContent);

    expect(headers.some((h) => h?.startsWith(`${resourceIcon('fuel')} Fuel`))).toBe(true);
    expect(headers.some((h) => h?.startsWith(`${resourceIcon('organics')} Org`))).toBe(true);
    expect(headers.some((h) => h?.startsWith(`${resourceIcon('equipment')} Equip`))).toBe(true);
  });
});

// @vitest-environment jsdom
/**
 * ColonistAllocator — ROLE_META icon/colour identity (WO-ARCH-RES-3B-PC-
 * RESIDUAL-LITERALS, accept 5).
 *
 * The retired ROLE_META literal hardcoded ⛽/🌿/⚙️ + #ff6b6b/#51cf66/#339af0.
 * This mounts the allocator and asserts each slider's glyph and track
 * colour equal resourceIcon(key)/resourceColor(key) — byte-identical to
 * those retired literals — while cssClass/context labels stay local.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { ColonistAllocator } from '../ColonistAllocator';
import type { Planet } from '../../../types/planetary';
import { resourceIcon, resourceColor } from '../../../services/resourceCatalog';

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

describe('ColonistAllocator — ROLE_META icon/colour identity', () => {
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
  });

  it('renders resourceIcon()/resourceColor() per role, byte-identical to the retired hardcodes; cssClass/labels stay local', async () => {
    await act(async () => {
      root.render(<ColonistAllocator planet={PLANET} />);
    });

    // A 4th `.allocation-slider.unused` row (idle headcount) is not
    // ROLE_META-driven — excluded here so this test stays scoped to the
    // fuel/organics/equipment trio under WO.
    const sliders = Array.from(container.querySelectorAll('.allocation-slider:not(.unused)'));
    expect(sliders).toHaveLength(3);
    const [fuelRow, organicsRow, equipmentRow] = sliders;

    expect(fuelRow.querySelector('.resource-icon')?.textContent).toBe(resourceIcon('fuel'));
    expect(organicsRow.querySelector('.resource-icon')?.textContent).toBe(resourceIcon('organics'));
    expect(equipmentRow.querySelector('.resource-icon')?.textContent).toBe(resourceIcon('equipment'));

    expect(resourceIcon('fuel')).toBe('⛽');
    expect(resourceIcon('organics')).toBe('🌿');
    expect(resourceIcon('equipment')).toBe('⚙️');

    // Context label text stays local (unchanged by this WO).
    expect(fuelRow.textContent).toContain('Fuel Production');
    expect(organicsRow.textContent).toContain('Organics Production');
    expect(equipmentRow.textContent).toContain('Equipment Production');

    // cssClass stays local ("fuel-slider", not catalog-sourced).
    expect(fuelRow.querySelector('input.fuel-slider')).toBeTruthy();
    expect(organicsRow.querySelector('input.organics-slider')).toBeTruthy();
    expect(equipmentRow.querySelector('input.equipment-slider')).toBeTruthy();

    // The `var(--surface-secondary)` custom-property term keeps jsdom from
    // normalizing this shorthand's hex literals to rgb() (unlike the plain
    // CoupledColonistSliders gradient) — assert the raw hex verbatim.
    const trackBg = (row: Element) => (row.querySelector('input[type="range"]') as HTMLInputElement).style.background;
    expect(trackBg(fuelRow)).toContain(resourceColor('fuel'));
    expect(trackBg(organicsRow)).toContain(resourceColor('organics'));
    expect(trackBg(equipmentRow)).toContain(resourceColor('equipment'));

    expect(resourceColor('fuel')).toBe('#ff6b6b');
    expect(resourceColor('organics')).toBe('#51cf66');
    expect(resourceColor('equipment')).toBe('#339af0');
  });
});

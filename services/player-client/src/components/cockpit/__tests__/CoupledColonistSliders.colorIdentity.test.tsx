// @vitest-environment jsdom
/**
 * CoupledColonistSliders — ROLES icon/colour identity (WO-ARCH-RES-3B-PC-
 * RESIDUAL-LITERALS, accept 5).
 *
 * The retired ROLES literal hardcoded ⛽/🌿/⚙️ + #ff6b6b/#51cf66/#339af0.
 * This asserts each slider row's glyph and track colour now equal
 * resourceIcon(key)/resourceColor(key) — and are byte-identical to those
 * retired literals, so the swap to the shared catalog is a visual no-op.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import CoupledColonistSliders, { type RoleAllocation } from '../CoupledColonistSliders';
import { resourceIcon, resourceColor } from '../../../services/resourceCatalog';

const ALLOCATIONS: RoleAllocation = { fuel: 10, organics: 10, equipment: 10 };

describe('CoupledColonistSliders — ROLES icon/colour identity', () => {
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

  it('renders resourceIcon()/resourceColor() per role, byte-identical to the retired hardcodes', async () => {
    await act(async () => {
      root.render(
        <CoupledColonistSliders
          allocations={ALLOCATIONS}
          productionRates={{ fuel: 100, organics: 100, equipment: 100 }}
          budget={40}
          totalColonists={30}
          onSetAll={vi.fn()}
        />
      );
    });

    const rows = Array.from(container.querySelectorAll('.cp-slider-row'));
    expect(rows).toHaveLength(3);

    const [fuelRow, organicsRow, equipmentRow] = rows;

    expect(fuelRow.querySelector('.cp-slider-label span[aria-hidden]')?.textContent).toBe(resourceIcon('fuel'));
    expect(organicsRow.querySelector('.cp-slider-label span[aria-hidden]')?.textContent).toBe(resourceIcon('organics'));
    expect(equipmentRow.querySelector('.cp-slider-label span[aria-hidden]')?.textContent).toBe(resourceIcon('equipment'));

    expect(resourceIcon('fuel')).toBe('⛽');
    expect(resourceIcon('organics')).toBe('🌿');
    expect(resourceIcon('equipment')).toBe('⚙️');

    // jsdom normalizes the inline `background` shorthand's hex literals to
    // rgb(); compare against the same normalization rather than the raw hex.
    const toRgb = (hex: string) => {
      const n = parseInt(hex.slice(1), 16);
      return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
    };
    const trackBg = (row: Element) => (row.querySelector('.cp-slider-input') as HTMLInputElement).style.background;
    expect(trackBg(fuelRow)).toContain(toRgb(resourceColor('fuel')));
    expect(trackBg(organicsRow)).toContain(toRgb(resourceColor('organics')));
    expect(trackBg(equipmentRow)).toContain(toRgb(resourceColor('equipment')));

    expect(resourceColor('fuel')).toBe('#ff6b6b');
    expect(resourceColor('organics')).toBe('#51cf66');
    expect(resourceColor('equipment')).toBe('#339af0');
  });
});

// @vitest-environment jsdom
/**
 * CitadelPanel — level/siege readout + Buildings/Defenses/Siege action buttons.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../planetary/CitadelManager', () => ({
  default: () => <div data-testid="citadel-manager" />,
}));

import CitadelPanel from '../CitadelPanel';

describe('CitadelPanel', () => {
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
  });

  it('shows level readout and wires the three action buttons', async () => {
    const onOpenBuildings = vi.fn();
    const onOpenDefense = vi.fn();
    const onOpenSiege = vi.fn();

    await act(async () => {
      root.render(
        <CitadelPanel
          planetId="p1"
          playerCredits={1000}
          citadelLevel={3}
          onUpdate={vi.fn()}
          onOpenBuildings={onOpenBuildings}
          onOpenDefense={onOpenDefense}
          onOpenSiege={onOpenSiege}
        />,
      );
    });

    expect(container.textContent).toContain('Citadel');
    expect(container.textContent).toContain('Lv 3');
    expect(container.querySelector('.cp-siege-flag')).toBeNull();
    expect(container.querySelector('[data-testid="citadel-manager"]')).toBeTruthy();

    const buttons = Array.from(container.querySelectorAll('.cp-action-btn')) as HTMLButtonElement[];
    expect(buttons).toHaveLength(3);

    await act(async () => {
      buttons[0].click();
      buttons[1].click();
      buttons[2].click();
    });
    expect(onOpenBuildings).toHaveBeenCalledOnce();
    expect(onOpenDefense).toHaveBeenCalledOnce();
    expect(onOpenSiege).toHaveBeenCalledOnce();
    expect(buttons[2].classList.contains('danger')).toBe(false);
  });

  it('flags siege in the readout and marks the Siege button danger', async () => {
    await act(async () => {
      root.render(
        <CitadelPanel
          planetId="p1"
          playerCredits={0}
          citadelLevel={1}
          underSiege
          onUpdate={vi.fn()}
          onOpenBuildings={vi.fn()}
          onOpenDefense={vi.fn()}
          onOpenSiege={vi.fn()}
        />,
      );
    });

    expect(container.querySelector('.cp-siege-flag')?.textContent).toContain('SIEGE');
    const siegeBtn = Array.from(container.querySelectorAll('.cp-action-btn')).at(-1) as HTMLButtonElement;
    expect(siegeBtn.classList.contains('danger')).toBe(true);
    expect(siegeBtn.getAttribute('title')).toBe('View active siege status');
  });
});

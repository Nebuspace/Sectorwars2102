// @vitest-environment jsdom
/**
 * SpecializationDrawer — dialog chrome, Escape/scrim close, specialize action.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const setSelectedSpec = vi.fn();
const handleSpecialize = vi.fn();
const meetsRequirements = vi.fn(() => ({ meets: true, missing: [] as string[] }));

vi.mock('../ColonySpecialization', () => ({
  SPECIALIZATIONS: [
    {
      type: 'mining',
      name: 'Mining Colony',
      icon: '⛏️',
      description: 'Ore focus',
      benefits: ['+10% ore'],
    },
    {
      type: 'farming',
      name: 'Agri Colony',
      icon: '🌾',
      description: 'Food focus',
      benefits: ['+10% organics'],
    },
  ],
  useColonySpecialization: () => ({
    selectedSpec: 'farming',
    setSelectedSpec,
    changing: false,
    error: null,
    successMessage: null,
    currentSpec: {
      type: 'mining',
      name: 'Mining Colony',
      icon: '⛏️',
      description: 'Ore focus',
      benefits: ['+10% ore'],
    },
    selectedSpecInfo: {
      type: 'farming',
      name: 'Agri Colony',
      icon: '🌾',
      description: 'Food focus',
      benefits: ['+10% organics'],
    },
    meetsRequirements,
    handleSpecialize,
  }),
}));

import SpecializationDrawer from '../SpecializationDrawer';
import type { Planet } from '../../../types/planetary';

const planet = {
  id: 'p1',
  name: 'Kepler',
  specialization: 'mining',
} as Planet;

describe('SpecializationDrawer', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.clearAllMocks();
    meetsRequirements.mockReturnValue({ meets: true, missing: [] });
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

  it('shows planet name, current badge, and Specialize action', async () => {
    await act(async () => {
      root.render(<SpecializationDrawer planet={planet} onClose={vi.fn()} />);
    });

    const dialog = container.querySelector('.spec-drawer');
    expect(dialog?.getAttribute('role')).toBe('dialog');
    expect(container.textContent).toContain('Kepler');
    expect(container.textContent).toContain('Current');
    expect(container.textContent).toContain('Change from');
    expect(container.textContent).toContain('Agri Colony');

    await act(async () => {
      (
        Array.from(container.querySelectorAll('button')).find((b) =>
          b.textContent?.includes('Specialize Colony'),
        ) as HTMLButtonElement
      ).click();
    });
    expect(handleSpecialize).toHaveBeenCalledTimes(1);
  });

  it('closes via Escape, close button, and scrim click', async () => {
    const onClose = vi.fn();
    await act(async () => {
      root.render(<SpecializationDrawer planet={planet} onClose={onClose} />);
    });

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);

    onClose.mockClear();
    await act(async () => {
      (container.querySelector('.spec-drawer-close') as HTMLButtonElement).click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);

    onClose.mockClear();
    await act(async () => {
      (container.querySelector('.spec-drawer-scrim') as HTMLElement).click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

// @vitest-environment jsdom
/**
 * PopulationCenterInterface — hub chrome, population formatting, pioneer venue switch.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../PioneerOfficeVenue', () => ({
  default: ({ onBack }: { onBack: () => void }) => (
    <div data-testid="pioneer-venue">
      <button type="button" onClick={onBack}>
        Back
      </button>
    </div>
  ),
}));

import PopulationCenterInterface from '../PopulationCenterInterface';
import type { Planet } from '../../../contexts/GameContext';

const planet = {
  id: 'p1',
  name: 'New Earth',
  population: 2_500_000,
  habitability_score: 88.4,
} as Planet;

describe('PopulationCenterInterface', () => {
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

  it('shows hub welcome, formatted population, and clamped habitability', async () => {
    await act(async () => {
      root.render(<PopulationCenterInterface planet={planet} />);
    });

    expect(container.textContent).toContain('POPULATION HUB');
    expect(container.textContent).toContain('New Earth');
    expect(container.textContent).toContain('2.5M');
    expect(container.textContent).toContain('88.4%');
    expect(container.querySelector('[data-testid="pioneer-venue"]')).toBeNull();
  });

  it('opens Pioneer Office and returns to hub on Back', async () => {
    await act(async () => {
      root.render(<PopulationCenterInterface planet={planet} />);
    });

    const open = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('PIONEER OFFICE'),
    ) as HTMLButtonElement;
    await act(async () => {
      open.click();
    });
    expect(container.querySelector('[data-testid="pioneer-venue"]')).toBeTruthy();

    const back = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Back',
    ) as HTMLButtonElement;
    await act(async () => {
      back.click();
    });
    expect(container.querySelector('[data-testid="pioneer-venue"]')).toBeNull();
    expect(container.textContent).toContain('New Earth');
  });

  it('formats billion-scale population and clamps habitability to 0..100', async () => {
    await act(async () => {
      root.render(
        <PopulationCenterInterface
          planet={
            {
              ...planet,
              population: 1_200_000_000,
              habitability_score: 140,
            } as Planet
          }
        />,
      );
    });
    expect(container.textContent).toContain('1.2B');
    expect(container.textContent).toContain('100%');
  });
});

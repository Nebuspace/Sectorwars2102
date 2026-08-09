// @vitest-environment jsdom
/**
 * CitadelStructure — stratum state classes + aria-label for level/upgrade.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import CitadelStructure from '../CitadelStructure';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const stratumClass = (container: HTMLElement, n: number) =>
  container.querySelector(`[data-stratum="${n}"]`)?.getAttribute('class') ?? '';

describe('CitadelStructure', () => {
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

  it('marks lit / current / ghost strata for a mid-level citadel', async () => {
    await act(async () => {
      root.render(<CitadelStructure level={3} />);
    });

    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('aria-label')).toBe(
      'Citadel structure visualization, level 3 of 5',
    );
    expect(stratumClass(container, 1)).toContain('state-lit');
    expect(stratumClass(container, 2)).toContain('state-lit');
    expect(stratumClass(container, 3)).toContain('state-current');
    expect(stratumClass(container, 4)).toContain('state-ghost');
    expect(stratumClass(container, 5)).toContain('state-ghost');
  });

  it('marks the upgrading stratum as constructing and updates aria-label', async () => {
    await act(async () => {
      root.render(<CitadelStructure level={2} isUpgrading upgradingToLevel={3} />);
    });

    expect(container.querySelector('svg')?.getAttribute('aria-label')).toContain(
      'upgrading to level 3',
    );
    expect(stratumClass(container, 2)).toContain('state-current');
    expect(stratumClass(container, 3)).toContain('state-constructing');
    expect(stratumClass(container, 4)).toContain('state-ghost');
  });

  it('defaults upgradingToLevel to level + 1 when upgrading', async () => {
    await act(async () => {
      root.render(<CitadelStructure level={4} isUpgrading />);
    });
    expect(stratumClass(container, 5)).toContain('state-constructing');
    expect(container.querySelector('svg')?.getAttribute('aria-label')).toContain(
      'upgrading to level 5',
    );
  });
});

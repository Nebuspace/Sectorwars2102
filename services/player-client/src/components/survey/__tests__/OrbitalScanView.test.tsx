// @vitest-environment jsdom
/**
 * OrbitalScanView — presentational silhouette: deterministic per planetId.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import OrbitalScanView from '../OrbitalScanView';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('OrbitalScanView', () => {
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

  it('renders title, optional planet name, hint, and typed aria-label', async () => {
    await act(async () => {
      root.render(
        <OrbitalScanView planetId="p-1" planetName="Rylan Prime" planetType="terrestrial" />,
      );
    });

    expect(container.textContent).toContain('Orbital Scan');
    expect(container.querySelector('.orbital-scan-planet')?.textContent).toBe('Rylan Prime');
    expect(container.textContent).toContain('illustrative');
    const svg = container.querySelector('.orbital-scan-svg');
    expect(svg?.getAttribute('role')).toBe('img');
    expect(svg?.getAttribute('aria-label')).toBe(
      'Orbital scan silhouette of a terrestrial world',
    );
  });

  it('omits type clause from aria-label when planetType is absent', async () => {
    await act(async () => {
      root.render(<OrbitalScanView planetId="p-2" />);
    });
    expect(container.querySelector('.orbital-scan-svg')?.getAttribute('aria-label')).toBe(
      'Orbital scan silhouette',
    );
    expect(container.querySelector('.orbital-scan-planet')).toBeNull();
  });

  it('is deterministic for the same planetId and varies across ids', async () => {
    await act(async () => {
      root.render(<OrbitalScanView planetId="stable-seed" />);
    });
    const pathA = container.querySelector('.orbital-scan-silhouette')?.getAttribute('d');
    const dotsA = Array.from(container.querySelectorAll('.orbital-scan-dot')).map((el) => ({
      cx: el.getAttribute('cx'),
      cy: el.getAttribute('cy'),
    }));
    expect(pathA).toBeTruthy();
    expect(dotsA.length).toBeGreaterThanOrEqual(4);
    expect(dotsA.length).toBeLessThanOrEqual(7);

    await act(async () => {
      root.render(<OrbitalScanView planetId="stable-seed" />);
    });
    expect(container.querySelector('.orbital-scan-silhouette')?.getAttribute('d')).toBe(pathA);
    expect(
      Array.from(container.querySelectorAll('.orbital-scan-dot')).map((el) => ({
        cx: el.getAttribute('cx'),
        cy: el.getAttribute('cy'),
      })),
    ).toEqual(dotsA);

    await act(async () => {
      root.render(<OrbitalScanView planetId="other-seed" />);
    });
    expect(container.querySelector('.orbital-scan-silhouette')?.getAttribute('d')).not.toBe(pathA);
  });
});

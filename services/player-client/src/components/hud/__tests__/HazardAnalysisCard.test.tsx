// @vitest-environment jsdom
/**
 * HazardAnalysisCard — sector fields, null telemetry, close + Escape.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import HazardAnalysisCard from '../HazardAnalysisCard';
import type { Sector } from '../../../contexts/GameContext';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const sampleSector = {
  name: 'Rylan Reach',
  hazard_level: 7,
  radiation_level: 0.125,
  special_features: ['ion_storm', 'debris_field'],
  description: 'Unstable subspace shear.',
} as Sector;

describe('HazardAnalysisCard', () => {
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

  it('renders hazard/radiation/features/description for a sector', async () => {
    await act(async () => {
      root.render(<HazardAnalysisCard sector={sampleSector} onClose={vi.fn()} />);
    });

    const dialog = container.querySelector('.annunciator-card');
    expect(dialog?.getAttribute('role')).toBe('dialog');
    expect(container.textContent).toContain('HAZARD ANALYSIS — Rylan Reach');
    expect(container.textContent).toContain('7/10');
    expect(container.textContent).toContain('12.5%');
    expect(container.textContent).toContain('ION STORM');
    expect(container.textContent).toContain('DEBRIS FIELD');
    expect(container.textContent).toContain('Unstable subspace shear.');
  });

  it('shows no-telemetry copy when sector is null', async () => {
    await act(async () => {
      root.render(<HazardAnalysisCard sector={null} onClose={vi.fn()} />);
    });
    expect(container.textContent).toContain('No sector telemetry.');
    expect(container.textContent).not.toContain('/10');
  });

  it('invokes onClose from the close button', async () => {
    const onClose = vi.fn();
    await act(async () => {
      root.render(<HazardAnalysisCard sector={sampleSector} onClose={onClose} />);
    });

    await act(async () => {
      (container.querySelector('.annunciator-card-close') as HTMLButtonElement).click();
    });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('invokes onClose on Escape', async () => {
    const onClose = vi.fn();
    await act(async () => {
      root.render(<HazardAnalysisCard sector={sampleSector} onClose={onClose} />);
    });

    await act(async () => {
      container.querySelector('.annunciator-card')!.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      );
    });
    expect(onClose).toHaveBeenCalledOnce();
  });
});

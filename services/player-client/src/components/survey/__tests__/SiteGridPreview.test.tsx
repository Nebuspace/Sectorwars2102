// @vitest-environment jsdom
/**
 * SiteGridPreview — fogged silhouette vs revealed slot grid.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import SiteGridPreview from '../SiteGridPreview';
import type { SiteIntel } from '../expeditionTypes';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// 5 slots → 3×2 board (6 cells) so one cell is marked unusable.
const result = { usable_slots: 5, shape_class: 'compact' } as SiteIntel;

describe('SiteGridPreview', () => {
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

  it('renders fogged silhouette without slot count', async () => {
    await act(async () => {
      root.render(<SiteGridPreview result={null} fogged />);
    });

    expect(container.textContent).toContain('Site — Unrevealed');
    expect(container.querySelector('.site-grid-preview')?.classList.contains('fogged')).toBe(true);
    expect(container.querySelector('.site-grid-preview-slots')).toBeNull();
    expect(container.textContent).toContain('Launch (or await) an expedition');
    const board = container.querySelector('.site-grid-preview-board');
    expect(board?.getAttribute('aria-label')).toBe('Unrevealed site silhouette');
    expect(container.querySelectorAll('.site-grid-preview-cell.fog').length).toBe(9);
  });

  it('reveals slot count and marks overflow cells unusable', async () => {
    await act(async () => {
      root.render(<SiteGridPreview result={result} fogged={false} />);
    });

    expect(container.textContent).toContain('Site Preview');
    expect(container.querySelector('.site-grid-preview-slots')?.textContent).toBe('5 slots');
    const board = container.querySelector('.site-grid-preview-board');
    expect(board?.getAttribute('aria-label')).toBe('Site shape, 5 usable slots');
    expect(container.querySelectorAll('.site-grid-preview-cell.revealed').length).toBe(6);
    expect(container.querySelectorAll('.site-grid-preview-cell.unusable').length).toBe(1);
    expect(container.querySelector('.site-grid-preview-hint')).toBeNull();
  });
});

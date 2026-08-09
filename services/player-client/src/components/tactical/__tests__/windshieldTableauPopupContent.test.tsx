// @vitest-environment jsdom
/**
 * renderTableauPopupContent — star / procedural chrome (no land/dock gates).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderTableauPopupContent } from '../windshieldTableauPopupContent';
import type { PopupState } from '../windshieldTableauHelpers';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseParams = {
  sectorId: 42,
  planets: [],
  system: null,
  star: { xPct: 20, yPct: 50, sizeEm: 3 },
  shipPos: null,
  localTraveling: false,
  glideTargetId: null,
  onHaltApproach: vi.fn(),
  onClosePopup: vi.fn(),
  onApproachPlanet: vi.fn(),
  onApproachStation: vi.fn(),
};

describe('renderTableauPopupContent', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.clearAllMocks();
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

  it('renders star class chrome', async () => {
    const popup = {
      xPct: 20,
      yPct: 50,
      meta: { kind: 'star', label: 'Sol', color: '#ff0', starClass: 'G2' },
    } as unknown as PopupState;

    await act(async () => {
      root.render(<>{renderTableauPopupContent({ ...baseParams, popup })}</>);
    });

    expect(container.textContent).toContain('SOL');
    expect(container.textContent).toContain('CLASS G2');
    expect(container.textContent).toContain('SECTOR 42');
  });

  it('renders procedural unsurveyed status', async () => {
    const popup = {
      xPct: 30,
      yPct: 40,
      meta: {
        kind: 'procedural',
        designation: 'P-9',
        typeName: 'Rock',
        sizeDesc: 'Small',
      },
    } as unknown as PopupState;

    await act(async () => {
      root.render(<>{renderTableauPopupContent({ ...baseParams, popup })}</>);
    });

    expect(container.textContent).toContain('P-9');
    expect(container.textContent).toContain('UNSURVEYED');
    expect(container.textContent).toContain('NO LANDING SITE');
  });
});

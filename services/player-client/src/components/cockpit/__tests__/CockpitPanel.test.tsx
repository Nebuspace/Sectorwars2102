// @vitest-environment jsdom
/**
 * CockpitPanel — accent, title, optional readout, children body.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import CockpitPanel from '../CockpitPanel';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('CockpitPanel', () => {
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

  it('renders title, accent CSS var, readout, className, and children', async () => {
    await act(async () => {
      root.render(
        <CockpitPanel title="CITADEL" accent="#fbbf24" readout="Lv 3" className="extra">
          <div data-testid="body">panel body</div>
        </CockpitPanel>,
      );
    });

    const panel = container.querySelector('.cockpit-panel') as HTMLElement;
    expect(panel.classList.contains('extra')).toBe(true);
    expect(panel.style.getPropertyValue('--panel-accent')).toBe('#fbbf24');
    expect(container.querySelector('.cp-title')?.textContent).toBe('CITADEL');
    expect(container.querySelector('.cp-readout')?.textContent).toBe('Lv 3');
    expect(container.querySelector('[data-testid="body"]')?.textContent).toBe('panel body');
  });

  it('omits the readout span when readout is undefined or null', async () => {
    await act(async () => {
      root.render(
        <CockpitPanel title="GRID" accent="#a78bfa">
          <span>ok</span>
        </CockpitPanel>,
      );
    });
    expect(container.querySelector('.cp-readout')).toBeNull();

    await act(async () => {
      root.render(
        <CockpitPanel title="GRID" accent="#a78bfa" readout={null}>
          <span>ok</span>
        </CockpitPanel>,
      );
    });
    expect(container.querySelector('.cp-readout')).toBeNull();
  });
});

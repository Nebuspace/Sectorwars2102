// @vitest-environment jsdom
/**
 * CockpitInstrument — monitor frame chrome (title, accent, subtitle, children).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import CockpitInstrument from '../CockpitInstrument';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('CockpitInstrument', () => {
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

  it('renders title, children, accent CSS var, and optional subtitle/className', async () => {
    await act(async () => {
      root.render(
        <CockpitInstrument
          title="NAV CHART"
          accent="#00D9FF"
          subtitle="SECTOR 7"
          className="nav-page"
        >
          <div data-testid="body">chart body</div>
        </CockpitInstrument>,
      );
    });

    const frame = container.querySelector('.instrument-monitor') as HTMLElement;
    expect(frame.classList.contains('nav-page')).toBe(true);
    expect(frame.style.getPropertyValue('--instrument-accent')).toBe('#00D9FF');
    expect(container.querySelector('.instrument-title')?.textContent).toBe('NAV CHART');
    expect(container.querySelector('.instrument-subtitle')?.textContent).toBe('SECTOR 7');
    expect(container.querySelector('[data-testid="body"]')?.textContent).toBe('chart body');
    expect(container.querySelector('.monitor-bezel')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('omits subtitle when not provided', async () => {
    await act(async () => {
      root.render(
        <CockpitInstrument title="COMMS" accent="#ff0">
          <span>ok</span>
        </CockpitInstrument>,
      );
    });
    expect(container.querySelector('.instrument-subtitle')).toBeNull();
  });
});

// @vitest-environment jsdom
/**
 * ResearchPanel — flywheel readout + EmpireResearchPanel host.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../research/EmpireResearchPanel', () => ({
  default: () => <div data-testid="empire-research-panel" />,
}));

import ResearchPanel from '../ResearchPanel';

describe('ResearchPanel', () => {
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

  it('hosts EmpireResearchPanel inside a Research cockpit shell with flywheel readout', async () => {
    await act(async () => {
      root.render(<ResearchPanel />);
    });

    const panel = container.querySelector('.cockpit-panel') as HTMLElement;
    expect(panel.style.getPropertyValue('--panel-accent')).toBe('#22d3ee');
    expect(container.querySelector('.cp-title')?.textContent).toBe('Research');
    expect(container.querySelector('.cp-readout')?.textContent).toContain('flywheel');
    expect(container.querySelector('[data-testid="empire-research-panel"]')).toBeTruthy();
  });
});

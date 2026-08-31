// @vitest-environment jsdom
/**
 * LoadingState — polite status region + default/custom message (LEG-3182).
 * Mirrors SoftkeyRail.test.tsx seam: jsdom + createRoot + act(), no RTL.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import LoadingState from './LoadingState';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('LoadingState', () => {
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

  it('defaults the status message and exposes polite live region', async () => {
    await act(async () => {
      root.render(<LoadingState />);
    });
    const status = container.querySelector('.loading-state-container') as HTMLElement;
    expect(status.getAttribute('role')).toBe('status');
    expect(status.getAttribute('aria-live')).toBe('polite');
    expect(container.querySelector('.loading-state-message')?.textContent).toBe('Loading...');
  });

  it('renders a custom message', async () => {
    await act(async () => {
      root.render(<LoadingState message="Acquiring sector…" />);
    });
    expect(container.querySelector('.loading-state-message')?.textContent).toBe(
      'Acquiring sector…',
    );
  });
});

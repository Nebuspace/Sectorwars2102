// @vitest-environment jsdom
/**
 * EmptyState + LoadingState — common chrome primitives (zero prior coverage).
 * Mirrors SoftkeyRail.test.tsx seam: jsdom + createRoot + act(), no RTL.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import EmptyState from './EmptyState';
import LoadingState from './LoadingState';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('EmptyState', () => {
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

  it('renders icon, title, and message', async () => {
    await act(async () => {
      root.render(
        <EmptyState icon="📡" title="No contacts" message="Sector scan is empty." />,
      );
    });
    expect(container.querySelector('.empty-state-icon')?.textContent).toBe('📡');
    expect(container.querySelector('.empty-state-title')?.textContent).toBe('No contacts');
    expect(container.querySelector('.empty-state-message')?.textContent).toBe(
      'Sector scan is empty.',
    );
  });

  it('fires the optional action onClick and omits the button when action is absent', async () => {
    const onClick = vi.fn();
    await act(async () => {
      root.render(
        <EmptyState
          icon="—"
          title="Empty"
          message="Nothing here."
          action={{ label: 'Retry', onClick }}
        />,
      );
    });
    const btn = container.querySelector('.empty-state-action') as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(btn.textContent).toBe('Retry');
    await act(async () => {
      btn.click();
    });
    expect(onClick).toHaveBeenCalledTimes(1);

    await act(async () => {
      root.render(<EmptyState icon="—" title="Empty" message="Nothing here." />);
    });
    expect(container.querySelector('.empty-state-action')).toBeNull();
  });

  it('renders children in the extra slot', async () => {
    await act(async () => {
      root.render(
        <EmptyState icon="—" title="Empty" message="msg">
          <button type="button">Secondary</button>
        </EmptyState>,
      );
    });
    expect(container.querySelector('.empty-state-extra')?.textContent).toContain('Secondary');
  });
});

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

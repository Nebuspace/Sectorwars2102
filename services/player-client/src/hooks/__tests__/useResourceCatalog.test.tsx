// @vitest-environment jsdom
/**
 * useResourceCatalog — loading flag, subscribe refresh, label helpers.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getCachedResourceCatalog = vi.fn();
const getResourceCatalog = vi.fn();
const subscribeResourceCatalog = vi.fn();
const resourceLabel = vi.fn((name: string) => `label:${name}`);
const resourceIcon = vi.fn((name: string) => `icon:${name}`);
const resourceColor = vi.fn((name: string) => `color:${name}`);

vi.mock('../../services/resourceCatalog', () => ({
  getCachedResourceCatalog: (...args: unknown[]) => getCachedResourceCatalog(...args),
  getResourceCatalog: (...args: unknown[]) => getResourceCatalog(...args),
  subscribeResourceCatalog: (...args: unknown[]) => subscribeResourceCatalog(...args),
  resourceLabel: (...args: unknown[]) => resourceLabel(...(args as [string])),
  resourceIcon: (...args: unknown[]) => resourceIcon(...(args as [string])),
  resourceColor: (...args: unknown[]) => resourceColor(...(args as [string])),
}));

import { useResourceCatalog } from '../useResourceCatalog';

const Probe: React.FC = () => {
  const { catalog, loading, getLabel, getIcon, getColor } = useResourceCatalog();
  return (
    <div
      data-testid="probe"
      data-loading={String(loading)}
      data-count={String(catalog.length)}
      data-label={getLabel('fuel')}
      data-icon={getIcon('fuel')}
      data-color={getColor('fuel')}
    />
  );
};

describe('useResourceCatalog', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let notify: (() => void) | null = null;

  beforeEach(() => {
    vi.clearAllMocks();
    notify = null;
    getCachedResourceCatalog.mockReturnValue(null);
    getResourceCatalog.mockResolvedValue([{ name: 'fuel', label: 'Fuel' }]);
    subscribeResourceCatalog.mockImplementation((fn: () => void) => {
      notify = fn;
      return () => {
        notify = null;
      };
    });
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

  it('starts loading, fetches once, then exposes helpers', async () => {
    let resolveFetch: (v: unknown) => void = () => {};
    getResourceCatalog.mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );

    await act(async () => {
      root.render(<Probe />);
    });
    expect(container.querySelector('[data-testid="probe"]')?.getAttribute('data-loading')).toBe(
      'true',
    );
    expect(getResourceCatalog).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFetch([{ name: 'fuel', label: 'Fuel' }]);
    });

    const probe = container.querySelector('[data-testid="probe"]') as HTMLElement;
    expect(probe.dataset.loading).toBe('false');
    expect(probe.dataset.count).toBe('1');
    expect(probe.dataset.label).toBe('label:fuel');
    expect(probe.dataset.icon).toBe('icon:fuel');
    expect(probe.dataset.color).toBe('color:fuel');
  });

  it('refreshes from cache when a peer fetch notifies subscribers', async () => {
    getResourceCatalog.mockReturnValue(new Promise(() => {})); // never resolves
    await act(async () => {
      root.render(<Probe />);
    });
    expect(container.querySelector('[data-testid="probe"]')?.getAttribute('data-loading')).toBe(
      'true',
    );

    getCachedResourceCatalog.mockReturnValue([{ name: 'ore', label: 'Ore' }]);
    await act(async () => {
      notify?.();
    });
    const probe = container.querySelector('[data-testid="probe"]') as HTMLElement;
    expect(probe.dataset.loading).toBe('false');
    expect(probe.dataset.count).toBe('1');
  });

  it('skips fetch when cache is already warm', async () => {
    getCachedResourceCatalog.mockReturnValue([{ name: 'organics', label: 'Organics' }]);
    await act(async () => {
      root.render(<Probe />);
    });
    expect(getResourceCatalog).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="probe"]')?.getAttribute('data-loading')).toBe(
      'false',
    );
    expect(container.querySelector('[data-testid="probe"]')?.getAttribute('data-count')).toBe('1');
  });
});

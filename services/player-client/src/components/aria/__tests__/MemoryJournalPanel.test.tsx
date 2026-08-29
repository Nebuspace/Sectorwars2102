// @vitest-environment jsdom
/**
 * LEG-397 — MemoryJournalPanel Vitest (list / filter / empty / error).
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockGetMemories = vi.fn();
const mockGetDataIndex = vi.fn();

vi.mock('../../../services/api', () => ({
  ariaMemoryAPI: {
    getMemories: (...a: unknown[]) => mockGetMemories(...a),
    getDataIndex: (...a: unknown[]) => mockGetDataIndex(...a),
  },
}));

import MemoryJournalPanel, { formatAriaMemoryLoadError } from '../MemoryJournalPanel';

const sampleMemory = {
  id: 'mem-1',
  memory_type: 'market',
  importance_score: 0.72,
  confidence_level: 0.9,
  created_at: '2026-07-09T12:00:00Z',
  content: { event: 'trade_transaction', commodity: 'organics' },
};

describe('MemoryJournalPanel', () => {
  let container: HTMLElement;
  let root: Root;

  beforeEach(() => {
    mockGetMemories.mockReset();
    mockGetDataIndex.mockReset();
    mockGetDataIndex.mockResolvedValue([
      {
        key: 'market',
        domain: 'market',
        display_name: 'Market',
        retention_class: 'budget_pruned',
        transparency_visible: true,
      },
      {
        key: 'threat.combat',
        domain: 'threat',
        display_name: 'Combat',
        retention_class: 'budget_pruned',
        transparency_visible: true,
      },
    ]);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('lists memories from tip GET /memories (happy path)', async () => {
    mockGetMemories.mockResolvedValue([sampleMemory]);

    await act(async () => {
      root.render(<MemoryJournalPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(mockGetMemories).toHaveBeenCalledWith({ memoryType: undefined, limit: 50 });
    expect(container.textContent).toContain('Market');
    expect(container.textContent).toContain('organics');
    expect(container.textContent).toContain('imp 0.72');
  });

  it('re-fetches with memoryType when stream filter changes', async () => {
    mockGetMemories.mockResolvedValue([sampleMemory]);

    await act(async () => {
      root.render(<MemoryJournalPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const select = container.querySelector('#aria-memory-type-filter') as HTMLSelectElement;
    expect(select).toBeTruthy();

    await act(async () => {
      select.value = 'threat.combat';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(mockGetMemories).toHaveBeenCalledWith({
      memoryType: 'threat.combat',
      limit: 50,
    });
  });

  it('shows empty state when API returns []', async () => {
    mockGetMemories.mockResolvedValue([]);

    await act(async () => {
      root.render(<MemoryJournalPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).toContain('No memories recorded yet.');
  });

  it('shows error state when GET /memories fails', async () => {
    mockGetMemories.mockRejectedValue(new Error('ARIA memory recall temporarily unavailable'));

    await act(async () => {
      root.render(<MemoryJournalPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('ARIA memory recall temporarily unavailable');
  });

  it('surfaces 503 server detail on memory load failure', async () => {
    const err = new Error('ARIA memory recall temporarily unavailable');
    (err as { status?: number }).status = 503;
    mockGetMemories.mockRejectedValue(err);

    await act(async () => {
      root.render(<MemoryJournalPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.querySelector('[role="alert"]')?.textContent).toBe(
      'ARIA memory recall temporarily unavailable',
    );
    expect(formatAriaMemoryLoadError(err)).toBe('ARIA memory recall temporarily unavailable');
  });
});

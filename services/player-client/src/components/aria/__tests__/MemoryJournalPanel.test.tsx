// @vitest-environment jsdom
/**
 * LEG-397 — MemoryJournalPanel Vitest (list / filter / empty / error).
 * LEG-3121 — export + reset controls.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockGetMemories = vi.fn();
const mockGetDataIndex = vi.fn();
const mockExportPersonalStore = vi.fn();
const mockResetPersonalStore = vi.fn();

vi.mock('../../../services/api', () => ({
  ariaMemoryAPI: {
    getMemories: (...a: unknown[]) => mockGetMemories(...a),
    getDataIndex: (...a: unknown[]) => mockGetDataIndex(...a),
    exportPersonalStore: (...a: unknown[]) => mockExportPersonalStore(...a),
    resetPersonalStore: (...a: unknown[]) => mockResetPersonalStore(...a),
  },
}));

import MemoryJournalPanel, {
  ariaMemoryExportFilename,
  formatAriaMemoryLoadError,
} from '../MemoryJournalPanel';

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
    mockExportPersonalStore.mockReset();
    mockResetPersonalStore.mockReset();
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

  it('exports personal store and triggers JSON download', async () => {
    const exportPayload = {
      player_id: 'player-1',
      memories: [sampleMemory],
      related_row_counts: { aria_personal_memories: 1 },
    };
    mockGetMemories.mockResolvedValue([sampleMemory]);
    mockExportPersonalStore.mockResolvedValue(exportPayload);

    const createObjectURL = vi.fn(() => 'blob:aria-export');
    const revokeObjectURL = vi.fn();
    const click = vi.fn();
    const originalCreateElement = document.createElement.bind(document);
    const createElementSpy = vi
      .spyOn(document, 'createElement')
      .mockImplementation((tag: string, options?: ElementCreationOptions) => {
        if (tag === 'a') {
          return { click, download: '', href: '' } as unknown as HTMLAnchorElement;
        }
        return originalCreateElement(tag, options);
      });
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });

    await act(async () => {
      root.render(<MemoryJournalPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const exportBtn = Array.from(container.querySelectorAll('button')).find(
      (btn) => btn.textContent === 'Export',
    );
    expect(exportBtn).toBeTruthy();

    await act(async () => {
      exportBtn!.click();
      await Promise.resolve();
    });

    expect(mockExportPersonalStore).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    expect(container.textContent).toContain('ARIA memory export downloaded.');

    createElementSpy.mockRestore();
    vi.unstubAllGlobals();
  });

  it('resets personal store after confirmation and reloads journal', async () => {
    mockGetMemories.mockResolvedValueOnce([sampleMemory]).mockResolvedValueOnce([]);
    mockResetPersonalStore.mockResolvedValue({
      status: 'success',
      deleted: { aria_personal_memories: 1 },
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    await act(async () => {
      root.render(<MemoryJournalPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const resetBtn = Array.from(container.querySelectorAll('button')).find(
      (btn) => btn.textContent === 'Reset',
    );
    expect(resetBtn).toBeTruthy();

    await act(async () => {
      resetBtn!.click();
      await Promise.resolve();
    });

    expect(confirmSpy).toHaveBeenCalled();
    expect(mockResetPersonalStore).toHaveBeenCalledTimes(1);
    expect(mockGetMemories).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain('ARIA personal memory data reset.');

    confirmSpy.mockRestore();
  });

  it('does not reset when confirmation is declined', async () => {
    mockGetMemories.mockResolvedValue([sampleMemory]);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    await act(async () => {
      root.render(<MemoryJournalPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const resetBtn = Array.from(container.querySelectorAll('button')).find(
      (btn) => btn.textContent === 'Reset',
    );

    await act(async () => {
      resetBtn!.click();
      await Promise.resolve();
    });

    expect(mockResetPersonalStore).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('builds a stable export filename', () => {
    expect(ariaMemoryExportFilename(new Date('2026-08-31T03:00:00.000Z'))).toBe(
      'aria-memory-export-2026-08-31T03-00-00-000Z.json',
    );
  });
});

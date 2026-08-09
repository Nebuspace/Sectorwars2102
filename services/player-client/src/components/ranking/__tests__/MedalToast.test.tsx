// @vitest-environment jsdom
/**
 * MedalToast — WS medal_awarded signal → toast, dismiss, unknown-icon fallback.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockWsState = {
  medalAwardedSignal: 0,
  lastMedalAwarded: null as null | {
    medal_id: string;
    medal_name: string | null;
    medal_category: string | null;
    medal_description?: string | null;
    medal_icon?: string | null;
  },
};

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => mockWsState,
}));

import MedalToast from '../MedalToast';

describe('MedalToast', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockWsState.medalAwardedSignal = 0;
    mockWsState.lastMedalAwarded = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.useFakeTimers();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  it('renders nothing until medalAwardedSignal bumps with a payload', async () => {
    await act(async () => {
      root.render(<MedalToast />);
    });
    expect(container.querySelector('.medal-toast')).toBeNull();
  });

  it('shows the toast with name/category/icon when a medal arrives', async () => {
    mockWsState.medalAwardedSignal = 1;
    mockWsState.lastMedalAwarded = {
      medal_id: 'm1',
      medal_name: 'Star of Commerce',
      medal_category: 'Trade',
      medal_description: 'First profitable run',
      medal_icon: 'medal_trade',
    };

    await act(async () => {
      root.render(<MedalToast />);
    });

    const toast = container.querySelector('.medal-toast');
    expect(toast).toBeTruthy();
    expect(toast?.getAttribute('role')).toBe('status');
    expect(container.textContent).toContain('MEDAL EARNED');
    expect(container.textContent).toContain('Star of Commerce');
    expect(container.textContent).toContain('Trade');
    expect(container.textContent).toContain('First profitable run');
    expect(container.querySelector('.medal-toast-icon')?.textContent).toBe('🏅');
  });

  it('falls back to generic medal glyph for unknown icon keys', async () => {
    mockWsState.medalAwardedSignal = 2;
    mockWsState.lastMedalAwarded = {
      medal_id: 'm2',
      medal_name: 'Mystery',
      medal_category: null,
      medal_icon: 'not_a_real_key',
    };

    await act(async () => {
      root.render(<MedalToast />);
    });
    expect(container.querySelector('.medal-toast-icon')?.textContent).toBe('🏅');
  });

  it('dismiss button hides the toast immediately', async () => {
    mockWsState.medalAwardedSignal = 3;
    mockWsState.lastMedalAwarded = {
      medal_id: 'm3',
      medal_name: 'Explorer',
      medal_category: 'Exploration',
      medal_icon: 'badge_explorer',
    };

    await act(async () => {
      root.render(<MedalToast />);
    });
    expect(container.querySelector('.medal-toast')).toBeTruthy();

    await act(async () => {
      (container.querySelector('.medal-toast-close') as HTMLButtonElement).click();
    });
    expect(container.querySelector('.medal-toast')).toBeNull();
  });

  it('auto-dismisses after VISIBLE_MS', async () => {
    mockWsState.medalAwardedSignal = 4;
    mockWsState.lastMedalAwarded = {
      medal_id: 'm4',
      medal_name: 'Genesis',
      medal_category: 'Colony',
      medal_icon: 'award_genesis',
    };

    await act(async () => {
      root.render(<MedalToast />);
    });
    expect(container.querySelector('.medal-toast')).toBeTruthy();

    await act(async () => {
      vi.advanceTimersByTime(6000);
    });
    expect(container.querySelector('.medal-toast')).toBeNull();
  });
});

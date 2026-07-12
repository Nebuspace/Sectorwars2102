// @vitest-environment jsdom
/**
 * Teleprinter — live-mount console-error smoke (WO-UI1-TELEPRINTER sub-part
 * a). Mirrors StatusBar.smoke.test.tsx's seam (jsdom + react-dom/client
 * createRoot + act(), no RTL in this project).
 *
 * Proves, against the REAL ariaFeedStore (not mocked — it's a lightweight
 * module-level singleton, exercising it directly proves the genuine merge/
 * filter integration rather than a hand-rolled fake) plus a mocked
 * WebSocketContext (the real one owns a live WS singleton, too heavy for a
 * unit smoke):
 *   - narration renders + the log scrolls WITHIN the panel (scrollIntoView
 *     called with block:'nearest', never the page) — accept #1
 *   - the input box visibly expands on focus and both submits + echoes —
 *     accept #2
 *   - all three modes are switchable and show DISTINCT filtered content —
 *     accept #3
 *   - minimize -> strip -> restore preserves mode + in-progress input state,
 *     proving no remount occurred (a remount would reset useState to its
 *     defaults) — accept #4/#5
 *   - zero console errors throughout every scenario
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Known repo harness quirk (see StatusBar.smoke.test.tsx) — silence the
// React 18 "not configured to support act(...)" warning at the source.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom does not implement scrollIntoView — polyfill so the component's
// call doesn't log a "Not implemented" console error (which would poison
// this suite's zero-console-error assertions).
Element.prototype.scrollIntoView = vi.fn();

// ── Mocked WS transport — a FIXED conversational seed (dialogue) + a fixed
// server-pushed narration seed. sendARIAMessage is a controllable vi.fn()
// so tests can assert it was (or wasn't) called, and simulate an offline
// failure. ────────────────────────────────────────────────────────────────
const mockSendARIAMessage = vi.fn();
let mockIsConnected = true;

const SEED_ARIA_MESSAGES = [
  {
    id: 'user-1',
    type: 'user' as const,
    content: 'status report',
    timestamp: '2026-01-01T00:00:00.000Z',
  },
  {
    id: 'ai-1',
    type: 'ai' as const,
    content: 'Ship intelligence online, Commander.',
    timestamp: '2026-01-01T00:00:01.000Z',
  },
  {
    id: 'narr-1',
    type: 'ai' as const,
    content: 'Hazard field detected — shields answering.',
    timestamp: '2026-01-01T00:00:02.000Z',
    isNarration: true as const,
  },
];

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    ariaMessages: SEED_ARIA_MESSAGES,
    sendARIAMessage: (...args: unknown[]) => mockSendARIAMessage(...args),
    isConnected: mockIsConnected,
  }),
}));

import { ariaFeed } from '../../mfd/ariaFeedStore';
import Teleprinter from '../Teleprinter';

describe('Teleprinter — live-mount smoke', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  const flush = async () => {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  beforeEach(() => {
    mockSendARIAMessage.mockReset();
    mockSendARIAMessage.mockReturnValue(true);
    mockIsConnected = true;
    // The store is a module-level singleton — reset it so tests don't leak
    // nav messages into each other.
    ariaFeed.clearNav();
    ariaFeed.setConversationId(null);

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    errorSpy.mockRestore();
  });

  it('mounts with zero console errors; narration is the default mode and shows narration+nav lines, scrolling within the panel', async () => {
    ariaFeed.appendNav('Course laid in for Sector 12 — 2 hops.');
    ariaFeed.appendUserEcho('engage');

    await act(async () => {
      root.render(<Teleprinter />);
    });
    await flush();

    expect(container.querySelector('[data-testid="teleprinter"]')).not.toBeNull();

    // Default mode = narration.
    expect(container.querySelector('.tp-mode-narration.active')).not.toBeNull();
    const logText = container.querySelector('#tp-log')?.textContent ?? '';
    expect(logText).toContain('Hazard field detected'); // isNarration (WS)
    expect(logText).toContain('Course laid in for Sector 12'); // isNav ai (local store)
    expect(logText).not.toContain('status report'); // dialogue-only
    expect(logText).not.toContain('Ship intelligence online'); // dialogue-only
    expect(logText).not.toContain('engage'); // command-echo-only

    // scrollIntoView called for the log, confined to the panel (block:
    // 'nearest'), never a page-level scroll.
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith(
      expect.objectContaining({ behavior: 'smooth', block: 'nearest' })
    );

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('all 3 modes are switchable and show visually/structurally distinct content', async () => {
    ariaFeed.appendNav('Course laid in for Sector 12 — 2 hops.');
    ariaFeed.appendUserEcho('engage');

    await act(async () => {
      root.render(<Teleprinter />);
    });
    await flush();

    const clickTab = async (id: string) => {
      const tab = container.querySelector(`#tp-mode-tab-${id}`) as HTMLButtonElement;
      expect(tab).not.toBeNull();
      await act(async () => {
        tab.click();
      });
      await flush();
    };

    await clickTab('dialogue');
    expect(container.querySelector('.tp-mode-dialogue.active')).not.toBeNull();
    let logText = container.querySelector('#tp-log')?.textContent ?? '';
    expect(logText).toContain('status report');
    expect(logText).toContain('Ship intelligence online');
    expect(logText).not.toContain('Hazard field detected');
    expect(logText).not.toContain('engage');

    await clickTab('command-echo');
    expect(container.querySelector('.tp-mode-command-echo.active')).not.toBeNull();
    logText = container.querySelector('#tp-log')?.textContent ?? '';
    expect(logText).toContain('engage');
    expect(logText).not.toContain('status report');
    expect(logText).not.toContain('Hazard field detected');

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('input expands on focus; command-echo submit echoes locally (no WS call), dialogue submit reuses sendARIAMessage', async () => {
    await act(async () => {
      root.render(<Teleprinter />);
    });
    await flush();

    const input = container.querySelector('.tp-input') as HTMLInputElement;
    expect(input.className).not.toContain('tp-input-focused');

    // React 17+ listens for focus/blur via the bubbling focusin/focusout
    // events at the root, not the non-bubbling native focus/blur — a real
    // .focus() call fires both correctly under jsdom.
    await act(async () => {
      input.focus();
    });
    await flush();
    expect(input.className).toContain('tp-input-focused');

    // ── Command-echo: switch mode, type, submit via Enter ──
    const cmdTab = container.querySelector('#tp-mode-tab-command-echo') as HTMLButtonElement;
    await act(async () => {
      cmdTab.click();
    });
    await flush();

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value'
      )!.set!;
      setter.call(input, 'lay in course to 9');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await flush();
    await act(async () => {
      input.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
      );
    });
    await flush();

    expect(container.querySelector('#tp-log')?.textContent).toContain('lay in course to 9');
    expect(mockSendARIAMessage).not.toHaveBeenCalled();
    expect((container.querySelector('.tp-input') as HTMLInputElement).value).toBe('');

    // ── Dialogue: switch mode, type, submit via XMIT click ──
    const dialogueTab = container.querySelector('#tp-mode-tab-dialogue') as HTMLButtonElement;
    await act(async () => {
      dialogueTab.click();
    });
    await flush();

    const input2 = container.querySelector('.tp-input') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value'
      )!.set!;
      setter.call(input2, 'what is my hull status');
      input2.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await flush();

    const xmit = container.querySelector('.tp-xmit') as HTMLButtonElement;
    await act(async () => {
      xmit.click();
    });
    await flush();

    expect(mockSendARIAMessage).toHaveBeenCalledWith(
      'what is my hull status',
      undefined,
      'trading'
    );

    // Blur clears the focused-expand class.
    await act(async () => {
      input2.blur();
    });
    await flush();
    expect((container.querySelector('.tp-input') as HTMLInputElement).className).not.toContain(
      'tp-input-focused'
    );

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('offline fallback: a failed sendARIAMessage still preserves the typed line, VISIBLE IN THE ACTIVE MODE (Pixel a11y REVISE #2)', async () => {
    mockSendARIAMessage.mockReturnValue(false);
    mockIsConnected = false;

    await act(async () => {
      root.render(<Teleprinter />);
    });
    await flush();

    // Default mode is narration — submit stays here (not command-echo) to
    // prove the fallback line renders in THIS tab, not command-echo.
    const submitViaEnter = async (text: string) => {
      const input = container.querySelector('.tp-input') as HTMLInputElement;
      await act(async () => {
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype,
          'value'
        )!.set!;
        setter.call(input, text);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await flush();
      await act(async () => {
        input.dispatchEvent(
          new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
        );
      });
      await flush();
    };

    await submitViaEnter('abort');

    expect(mockSendARIAMessage).toHaveBeenCalled();
    // Visible immediately in narration — the tab the player actually typed
    // into — with NO tab switch.
    expect(container.querySelector('#tp-log')?.textContent).toContain('abort');
    expect(container.querySelector('.tp-mode-narration.active')).not.toBeNull();

    // Must NOT have leaked into command-echo (the old, wrong behavior).
    const cmdTab = container.querySelector('#tp-mode-tab-command-echo') as HTMLButtonElement;
    await act(async () => {
      cmdTab.click();
    });
    await flush();
    expect(container.querySelector('#tp-log')?.textContent).not.toContain('abort');

    // Repeat in dialogue mode — the fix covers both narration and dialogue.
    const dialogueTab = container.querySelector('#tp-mode-tab-dialogue') as HTMLButtonElement;
    await act(async () => {
      dialogueTab.click();
    });
    await flush();

    await submitViaEnter('what is my fuel');

    expect(container.querySelector('#tp-log')?.textContent).toContain('what is my fuel');
    expect(container.querySelector('.tp-mode-dialogue.active')).not.toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('mode tablist keyboard nav (Pixel a11y REVISE #1): Left/Right cycle (wrapping), Home/End jump, focus follows the active tab', async () => {
    await act(async () => {
      root.render(<Teleprinter />);
    });
    await flush();

    const tablist = container.querySelector('[role="tablist"][aria-label="Teleprinter mode"]') as HTMLElement;
    const tabs = Array.from(container.querySelectorAll('#tp-body [role="tab"]')) as HTMLButtonElement[];
    expect(tabs.length).toBe(3);
    expect(tabs.map((t) => t.id)).toEqual([
      'tp-mode-tab-narration',
      'tp-mode-tab-dialogue',
      'tp-mode-tab-command-echo',
    ]);

    const pressKey = async (key: string) => {
      await act(async () => {
        tablist.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
      });
      await flush();
    };

    // Default: narration (index 0) selected, roving tabindex = 0 there only.
    expect(tabs[0].tabIndex).toBe(0);
    expect(tabs[1].tabIndex).toBe(-1);
    expect(tabs[2].tabIndex).toBe(-1);

    // ArrowRight: narration -> dialogue, active tab AND focus both follow.
    await pressKey('ArrowRight');
    expect(tabs[1].getAttribute('aria-selected')).toBe('true');
    expect(tabs[0].getAttribute('aria-selected')).toBe('false');
    expect(document.activeElement).toBe(tabs[1]);
    expect(tabs[1].tabIndex).toBe(0);
    expect(tabs[0].tabIndex).toBe(-1);
    expect(container.querySelector('.tp-mode-dialogue.active')).not.toBeNull();

    // End: jump straight to the last tab (command-echo, index 2).
    await pressKey('End');
    expect(tabs[2].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs[2]);
    expect(container.querySelector('.tp-mode-command-echo.active')).not.toBeNull();

    // ArrowRight wraps from the last tab back to the first.
    await pressKey('ArrowRight');
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs[0]);

    // ArrowLeft wraps from the first tab back to the last.
    await pressKey('ArrowLeft');
    expect(tabs[2].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs[2]);

    // Home: jump straight back to the first tab.
    await pressKey('Home');
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs[0]);

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('input aria-label is mode-aware (Pixel a11y REVISE #3)', async () => {
    await act(async () => {
      root.render(<Teleprinter />);
    });
    await flush();

    expect((container.querySelector('.tp-input') as HTMLInputElement).getAttribute('aria-label')).toBe(
      'Narration ARIA'
    );

    const dialogueTab = container.querySelector('#tp-mode-tab-dialogue') as HTMLButtonElement;
    await act(async () => {
      dialogueTab.click();
    });
    await flush();
    expect((container.querySelector('.tp-input') as HTMLInputElement).getAttribute('aria-label')).toBe(
      'Message ARIA'
    );

    const cmdTab = container.querySelector('#tp-mode-tab-command-echo') as HTMLButtonElement;
    await act(async () => {
      cmdTab.click();
    });
    await flush();
    expect((container.querySelector('.tp-input') as HTMLInputElement).getAttribute('aria-label')).toBe(
      'Command ARIA'
    );

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('minimize -> strip -> restore preserves mode + in-progress input (no remount)', async () => {
    await act(async () => {
      root.render(<Teleprinter />);
    });
    await flush();

    const dialogueTab = container.querySelector('#tp-mode-tab-dialogue') as HTMLButtonElement;
    await act(async () => {
      dialogueTab.click();
    });
    await flush();
    expect(container.querySelector('.tp-mode-dialogue.active')).not.toBeNull();

    const input = container.querySelector('.tp-input') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value'
      )!.set!;
      setter.call(input, 'draft in progress');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await flush();
    expect(input.value).toBe('draft in progress');

    const toggle = container.querySelector('.tp-strip-toggle') as HTMLButtonElement;
    await act(async () => {
      toggle.click();
    });
    await flush();

    expect(container.querySelector('.teleprinter')?.className).toContain('tp-minimized');
    // #tp-body must still be IN THE DOM — a CSS display toggle, never a
    // conditional unmount (a remount would drop the mode/input state below).
    expect(container.querySelector('#tp-body')).not.toBeNull();

    await act(async () => {
      toggle.click();
    });
    await flush();

    expect(container.querySelector('.teleprinter')?.className).not.toContain('tp-minimized');
    // If this were a remount, mode would reset to 'narration' (default) and
    // the input would reset to ''.
    expect(container.querySelector('.tp-mode-dialogue.active')).not.toBeNull();
    expect((container.querySelector('.tp-input') as HTMLInputElement).value).toBe(
      'draft in progress'
    );

    expect(errorSpy).not.toHaveBeenCalled();
  });
});

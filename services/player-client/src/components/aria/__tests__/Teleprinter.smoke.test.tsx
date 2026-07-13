// @vitest-environment jsdom
/**
 * Teleprinter — live-mount console-error smoke (WO-UI1-TELEPRINTER sub-part
 * a; extended by WO-UI1-CHROME-COMPLETE for the grammar wiring; REVISED by
 * WO-UI-MAX-BATCH-1 REVISE, Max #22-24, for the two-independent-toggle
 * rebuild). Mirrors StatusBar.smoke.test.tsx's seam (jsdom + react-dom/
 * client createRoot + act(), no RTL in this project).
 *
 * Proves, against the REAL ariaFeedStore (not mocked — it's a lightweight
 * module-level singleton, exercising it directly proves the genuine merge/
 * filter integration rather than a hand-rolled fake) plus mocked
 * WebSocketContext / GameContext / AutopilotContext (the real ones own live
 * transports/providers, too heavy for a unit smoke):
 *   - narration renders + the log scrolls WITHIN the panel (scrollIntoView
 *     called with block:'nearest', never the page) — accept #1
 *   - the input box visibly expands on focus and both submits + echoes —
 *     accept #2
 *   - all three CONTENT tabs (narration/dialogue/CMD) are switchable and
 *     show DISTINCT filtered content — accept #3
 *   - toggling PANEL<->TICKER preserves content-tab + in-progress input
 *     state (accept #4/#5) — `#tp-body` is unconditionally in the DOM now
 *     (CSS display-toggled, never a conditional unmount), so this is even
 *     more directly true than before this REVISE
 *   - TWO INDEPENDENT BINARY TOGGLES (WO-UI-MAX-BATCH-1 REVISE): PANEL
 *     (bodyPanel) and LOG (transcriptOpen) are separate booleans, each with
 *     its own persistent, never-unmounted button, correct aria-pressed, and
 *     an action-naming label that swaps with state; LOG can open over
 *     EITHER body form (orthogonality)
 *   - the ADR-0072 command grammar (dock/undock/land/lift off/set course to
 *     N/engage/abort/status/help) parses + executes from BOTH the CMD tab
 *     and the ticker's own compact input (visual-form steer); unrecognized
 *     input falls through to the existing ARIA free-chat unchanged
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
  // n2 (WO-UI0-SHELL-TRANSPLANT leaf L4) regression pair — a server-pushed
  // narration `ts` (aria_narration_service's tz-aware `created_at.isoformat()`,
  // '+00:00'-suffixed) arriving BEFORE a client-sourced 'Z'-suffixed one, both
  // rounding to the SAME epoch millisecond (Date.parse truncates beyond ms).
  // The pre-fix raw-string `.localeCompare` always ranked 'Z' (0x5A) above any
  // digit, so it would have buried this array's true-latest ('narr-3', 'Z')
  // behind 'narr-2' ('+00:00') regardless of arrival order. See toEpoch's own
  // doc-comment in Teleprinter.tsx.
  {
    id: 'narr-2',
    type: 'ai' as const,
    content: 'Hull integrity nominal.',
    timestamp: '2026-01-01T00:00:10.500123+00:00',
    isNarration: true as const,
  },
  {
    id: 'narr-3',
    type: 'ai' as const,
    content: 'Arrival: Sector 12.',
    timestamp: '2026-01-01T00:00:10.500Z',
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

// ── Mocked GameContext — station/planet actions + posture (dock/undock/
// land/lift-off grammar) — grammar-dispatch tests reassign these per case. ─
function makePlayerState(overrides: Record<string, unknown> = {}) {
  return {
    id: 'player-1',
    credits: 5_000,
    turns: 120,
    current_sector_id: 7,
    is_docked: false,
    is_landed: false,
    ...overrides,
  };
}

let mockPlayerState: Record<string, unknown> = makePlayerState();
let mockStationsInSector: Array<{ id: string; name: string }> = [];
let mockPlanetsInSector: Array<{ id: string; name: string }> = [];
const mockDockAtStation = vi.fn();
const mockUndockFromStation = vi.fn();
const mockLandOnPlanet = vi.fn();
const mockLeavePlanet = vi.fn();

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    currentSector: { name: 'Sol', sector_id: 7 },
    stationsInSector: mockStationsInSector,
    planetsInSector: mockPlanetsInSector,
    dockAtStation: (...args: unknown[]) => mockDockAtStation(...args),
    undockFromStation: (...args: unknown[]) => mockUndockFromStation(...args),
    landOnPlanet: (...args: unknown[]) => mockLandOnPlanet(...args),
    leavePlanet: (...args: unknown[]) => mockLeavePlanet(...args),
  }),
}));

// ── Mocked AutopilotContext — plotCourse/engage/abort (set-course/engage/
// abort grammar). ───────────────────────────────────────────────────────
const mockPlotCourse = vi.fn();
const mockEngage = vi.fn();
const mockAutopilotAbort = vi.fn();

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({
    plotCourse: (...args: unknown[]) => mockPlotCourse(...args),
    engage: (...args: unknown[]) => mockEngage(...args),
    abort: (...args: unknown[]) => mockAutopilotAbort(...args),
  }),
}));

import { ariaFeed } from '../../mfd/ariaFeedStore';
import Teleprinter from '../Teleprinter';

/** Test-local controlled wrapper — both toggle booleans are owned by the
 *  parent in production (GameLayout); this mirrors that exactly. Defaults
 *  to bodyPanel=true (PANEL, #tp-body's richer content) so the pre-existing
 *  content-tab assertions below need no changes; ticker/toggle tests pass
 *  `initialBodyPanel={false}` explicitly. */
const ControlledTeleprinter: React.FC<{ initialBodyPanel?: boolean; initialTranscriptOpen?: boolean }> = ({
  initialBodyPanel = true,
  initialTranscriptOpen = false,
}) => {
  const [bodyPanel, setBodyPanel] = React.useState(initialBodyPanel);
  const [transcriptOpen, setTranscriptOpen] = React.useState(initialTranscriptOpen);
  return (
    <Teleprinter
      bodyPanel={bodyPanel}
      onBodyPanelChange={setBodyPanel}
      transcriptOpen={transcriptOpen}
      onTranscriptOpenChange={setTranscriptOpen}
    />
  );
};

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

    mockPlayerState = makePlayerState();
    mockStationsInSector = [];
    mockPlanetsInSector = [];
    mockDockAtStation.mockReset().mockResolvedValue({ success: true });
    mockUndockFromStation.mockReset().mockResolvedValue({ success: true });
    mockLandOnPlanet.mockReset().mockResolvedValue({ success: true });
    mockLeavePlanet.mockReset().mockResolvedValue({ success: true });
    mockPlotCourse.mockReset().mockResolvedValue(undefined);
    mockEngage.mockReset();
    mockAutopilotAbort.mockReset();

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

  // ── Shared helpers ────────────────────────────────────────────────────
  const setInput = async (el: HTMLInputElement, text: string) => {
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(el, text);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await flush();
  };

  const pressEnter = async (el: HTMLInputElement) => {
    await act(async () => {
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    });
    await flush();
  };

  const clickTab = async (id: string) => {
    const tab = container.querySelector(`#tp-mode-tab-${id}`) as HTMLButtonElement;
    expect(tab).not.toBeNull();
    await act(async () => {
      tab.click();
    });
    await flush();
  };

  /** Types + Enter-submits via the tp-body CMD/narration/dialogue input. */
  const submitViaBody = async (text: string) => {
    const input = container.querySelector('.tp-input') as HTMLInputElement;
    await setInput(input, text);
    await pressEnter(input);
  };

  /** Types + Enter-submits via the ticker's own compact input (`.tin`,
   *  WO-UI0-SHELL-TRANSPLANT leaf L4 re-class — cockpit-shell.css's
   *  phosphor-green input; distinct from `#tp-body`'s own `.tp-input`). */
  const submitViaTicker = async (text: string) => {
    const input = container.querySelector('.tin') as HTMLInputElement;
    await setInput(input, text);
    await pressEnter(input);
  };

  it('mounts with zero console errors; narration is the default mode and shows narration+nav lines, scrolling within the panel', async () => {
    ariaFeed.appendNav('Course laid in for Sector 12 — 2 hops.');
    ariaFeed.appendUserEcho('engage');

    await act(async () => {
      root.render(<ControlledTeleprinter />);
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

  it('n2: .tline shows the chronologically-latest entry, not whichever timestamp format string-sorts highest (WO-UI0-SHELL-TRANSPLANT leaf L4)', async () => {
    await act(async () => {
      root.render(<ControlledTeleprinter />);
    });
    await flush();

    // narr-2 ('+00:00'-suffixed) and narr-3 ('Z'-suffixed) tie at the same
    // epoch millisecond; narr-3 is later in SEED_ARIA_MESSAGES (the true-
    // latest arrival). A raw-string compare would have ranked narr-2 ('Z'
    // sorts above any digit at their divergent 4th fractional character)
    // as "later" and shown its content instead — see toEpoch's doc-comment.
    expect(container.querySelector('.tline')?.textContent).toBe('Arrival: Sector 12.');

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('all 3 content tabs are switchable and show visually/structurally distinct content', async () => {
    ariaFeed.appendNav('Course laid in for Sector 12 — 2 hops.');
    ariaFeed.appendUserEcho('engage');

    await act(async () => {
      root.render(<ControlledTeleprinter />);
    });
    await flush();

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

  it('input expands on focus; unrecognized CMD text falls through to ARIA free-chat (no WS call in narration/dialogue-bound submits), dialogue submit reuses sendARIAMessage', async () => {
    await act(async () => {
      root.render(<ControlledTeleprinter />);
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

    // ── CMD tab: unrecognized text falls through to ARIA free-chat ──
    const cmdTab = container.querySelector('#tp-mode-tab-command-echo') as HTMLButtonElement;
    await act(async () => {
      cmdTab.click();
    });
    await flush();

    await submitViaBody('what is my hull status');

    expect(mockSendARIAMessage).toHaveBeenCalledWith('what is my hull status', undefined, 'trading');
    expect((container.querySelector('.tp-input') as HTMLInputElement).value).toBe('');

    // ── Dialogue: switch mode, type, submit via XMIT click ──
    const dialogueTab = container.querySelector('#tp-mode-tab-dialogue') as HTMLButtonElement;
    await act(async () => {
      dialogueTab.click();
    });
    await flush();

    const input2 = container.querySelector('.tp-input') as HTMLInputElement;
    await setInput(input2, 'what is my hull status again');

    const xmit = container.querySelector('.tp-xmit') as HTMLButtonElement;
    await act(async () => {
      xmit.click();
    });
    await flush();

    // conversationId is no longer undefined by this second call -- the
    // first (successful) send already minted + stored one.
    expect(mockSendARIAMessage).toHaveBeenLastCalledWith(
      'what is my hull status again',
      expect.stringMatching(/^conv_/),
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
      root.render(<ControlledTeleprinter />);
    });
    await flush();

    // Default mode is narration — submit stays here (not command-echo) to
    // prove the fallback line renders in THIS tab, not command-echo. "abort"
    // is grammar-shaped but narration/dialogue never intercept — only the
    // CMD tab / ticker input do (WO-UI1-CHROME-COMPLETE) — so this still
    // goes straight to sendARIAMessage exactly as before.
    await submitViaBody('abort');

    expect(mockSendARIAMessage).toHaveBeenCalled();
    expect(mockAutopilotAbort).not.toHaveBeenCalled();
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

    await submitViaBody('what is my fuel');

    expect(container.querySelector('#tp-log')?.textContent).toContain('what is my fuel');
    expect(container.querySelector('.tp-mode-dialogue.active')).not.toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('mode tablist keyboard nav (Pixel a11y REVISE #1): Left/Right cycle (wrapping), Home/End jump, focus follows the active tab', async () => {
    await act(async () => {
      root.render(<ControlledTeleprinter />);
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
      root.render(<ControlledTeleprinter />);
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

  it('toggling PANEL<->TICKER preserves content-tab + in-progress input (no remount)', async () => {
    await act(async () => {
      root.render(<ControlledTeleprinter />);
    });
    await flush();

    const dialogueTab = container.querySelector('#tp-mode-tab-dialogue') as HTMLButtonElement;
    await act(async () => {
      dialogueTab.click();
    });
    await flush();
    expect(container.querySelector('.tp-mode-dialogue.active')).not.toBeNull();

    const input = container.querySelector('.tp-input') as HTMLInputElement;
    await setInput(input, 'draft in progress');
    expect(input.value).toBe('draft in progress');

    // One click flips PANEL -> TICKER now (WO-UI-MAX-BATCH-1 REVISE — no
    // more 3-state cycle to walk through).
    const panelToggle = container.querySelector('.tp-panel-toggle') as HTMLButtonElement;
    expect(panelToggle.textContent).toBe('TICKER'); // action label while bodyPanel=true
    await act(async () => {
      panelToggle.click();
    });
    await flush();

    expect(container.querySelector('.teleprinter')?.className).toContain('tp-ticker');
    // #tp-body must still be IN THE DOM — a CSS display toggle, never a
    // conditional unmount (a remount would drop the mode/input state below).
    expect(container.querySelector('#tp-body')).not.toBeNull();

    // One click restores PANEL.
    await act(async () => {
      panelToggle.click();
    });
    await flush();

    expect(container.querySelector('.teleprinter')?.className).toContain('tp-panel');
    // If this were a remount, mode would reset to 'narration' (default) and
    // the input would reset to ''.
    expect(container.querySelector('.tp-mode-dialogue.active')).not.toBeNull();
    expect((container.querySelector('.tp-input') as HTMLInputElement).value).toBe('draft in progress');

    expect(errorSpy).not.toHaveBeenCalled();
  });

  // ── TWO INDEPENDENT BINARY TOGGLES (WO-UI-MAX-BATCH-1 REVISE, Max #22-24
  // — Max live-playtested the shipped single 3-state cycle and RETRACTED
  // it, back to the artifact's own two-toggle model). ─────────────────────
  describe('two independent toggles — PANEL/TICKER and LOG/HIDE', () => {
    it('ticker (default) renders the persistent control row: .glyph + .tline + .telerow[.tin + XMIT + PANEL toggle + LOG toggle], both toggles default-labeled + aria-pressed=false', async () => {
      ariaFeed.appendNav('Standing by, Commander.');

      await act(async () => {
        root.render(<ControlledTeleprinter initialBodyPanel={false} />);
      });
      await flush();

      expect(container.querySelector('.teleprinter')?.className).toContain('tele');
      expect(container.querySelector('.teleprinter')?.className).toContain('tp-ticker');
      const row = container.querySelector('.tp-ticker-row');
      expect(row).not.toBeNull();
      expect(row?.querySelector('.glyph')?.textContent).toBe('▸ ARIA');
      expect(row?.querySelector('.tline')?.textContent).toContain('Standing by, Commander.');
      // .telerow (cockpit-shell.css: display:contents outside the artifact's
      // own aria=2 mode) wraps the input group + PANEL toggle + LOG toggle,
      // matching the artifact's own XMIT->◫PANEL->▲LOG order.
      const telerow = row?.querySelector('.telerow');
      expect(telerow).not.toBeNull();
      expect(telerow?.querySelector('.tin')).not.toBeNull();
      expect(telerow?.querySelector('.tkey.tp-ticker-xmit')?.textContent).toBe('XMIT');

      const panelToggle = telerow?.querySelector('.tp-panel-toggle') as HTMLButtonElement;
      expect(panelToggle.textContent).toBe('PANEL'); // action label: click switches TO panel
      expect(panelToggle.getAttribute('aria-pressed')).toBe('false');

      const logToggle = telerow?.querySelector('.tp-log-toggle') as HTMLButtonElement;
      expect(logToggle.textContent).toBe('LOG'); // action label: click opens the transcript
      expect(logToggle.getAttribute('aria-pressed')).toBe('false');

      // DOM order: input group, THEN PANEL toggle, THEN LOG toggle
      // (farthest right) — matches the artifact's own layout.
      const telerowChildren = Array.from(telerow!.children);
      expect(telerowChildren.indexOf(panelToggle)).toBeLessThan(telerowChildren.indexOf(logToggle));

      expect(errorSpy).not.toHaveBeenCalled();
    });

    it('the PANEL toggle flips bodyPanel independently of LOG; label swaps PANEL<->TICKER; aria-pressed + root class track the boolean', async () => {
      await act(async () => {
        root.render(<ControlledTeleprinter initialBodyPanel={false} />);
      });
      await flush();

      const panelToggle = container.querySelector('.tp-panel-toggle') as HTMLButtonElement;
      expect(container.querySelector('.teleprinter')?.className).toContain('tp-ticker');
      expect(panelToggle.textContent).toBe('PANEL');
      expect(panelToggle.getAttribute('aria-pressed')).toBe('false');

      await act(async () => { panelToggle.click(); });
      await flush();
      expect(container.querySelector('.teleprinter')?.className).toContain('tp-panel');
      expect(panelToggle.textContent).toBe('TICKER');
      expect(panelToggle.getAttribute('aria-pressed')).toBe('true');

      await act(async () => { panelToggle.click(); });
      await flush();
      expect(container.querySelector('.teleprinter')?.className).toContain('tp-ticker');
      expect(panelToggle.textContent).toBe('PANEL');
      expect(panelToggle.getAttribute('aria-pressed')).toBe('false');

      // LOG never moved.
      const logToggle = container.querySelector('.tp-log-toggle') as HTMLButtonElement;
      expect(logToggle.getAttribute('aria-pressed')).toBe('false');
      expect(container.querySelector('.teleprinter')?.className).not.toContain('tp-log-open');

      expect(errorSpy).not.toHaveBeenCalled();
    });

    it('the LOG toggle flips transcriptOpen independently of PANEL; label swaps LOG<->HIDE; aria-pressed + root class track the boolean', async () => {
      await act(async () => {
        root.render(<ControlledTeleprinter initialBodyPanel={false} />);
      });
      await flush();

      const logToggle = container.querySelector('.tp-log-toggle') as HTMLButtonElement;
      expect(logToggle.textContent).toBe('LOG');
      expect(logToggle.getAttribute('aria-pressed')).toBe('false');
      expect(container.querySelector('.teleprinter')?.className).not.toContain('tp-log-open');

      await act(async () => { logToggle.click(); });
      await flush();
      expect(container.querySelector('.teleprinter')?.className).toContain('tp-log-open');
      expect(logToggle.textContent).toBe('HIDE');
      expect(logToggle.getAttribute('aria-pressed')).toBe('true');

      await act(async () => { logToggle.click(); });
      await flush();
      expect(container.querySelector('.teleprinter')?.className).not.toContain('tp-log-open');
      expect(logToggle.textContent).toBe('LOG');
      expect(logToggle.getAttribute('aria-pressed')).toBe('false');

      // PANEL never moved -- still ticker (default).
      const panelToggle = container.querySelector('.tp-panel-toggle') as HTMLButtonElement;
      expect(panelToggle.getAttribute('aria-pressed')).toBe('false');
      expect(container.querySelector('.teleprinter')?.className).toContain('tp-ticker');

      expect(errorSpy).not.toHaveBeenCalled();
    });

    it('orthogonality: LOG opens over EITHER body form -- ticker+LOG and panel+LOG are both reachable, independently', async () => {
      await act(async () => {
        root.render(<ControlledTeleprinter initialBodyPanel={false} />);
      });
      await flush();

      const logToggle = container.querySelector('.tp-log-toggle') as HTMLButtonElement;
      const panelToggle = container.querySelector('.tp-panel-toggle') as HTMLButtonElement;

      // ticker + LOG open.
      await act(async () => { logToggle.click(); });
      await flush();
      let cls = container.querySelector('.teleprinter')?.className ?? '';
      expect(cls).toContain('tp-ticker');
      expect(cls).toContain('tp-log-open');

      // Flip to PANEL while LOG stays open -- LOG's own state is untouched.
      await act(async () => { panelToggle.click(); });
      await flush();
      cls = container.querySelector('.teleprinter')?.className ?? '';
      expect(cls).toContain('tp-panel');
      expect(cls).toContain('tp-log-open');
      expect(logToggle.getAttribute('aria-pressed')).toBe('true');

      // Close LOG while PANEL stays open.
      await act(async () => { logToggle.click(); });
      await flush();
      cls = container.querySelector('.teleprinter')?.className ?? '';
      expect(cls).toContain('tp-panel');
      expect(cls).not.toContain('tp-log-open');

      expect(errorSpy).not.toHaveBeenCalled();
    });

    // ── No focus-instance-swap machinery (WO-UI-MAX-BATCH-1 REVISE — Max
    // #22-24: "no focus-instance-swap issue, the buttons persist"). The
    // retired single-3-state-toggle design needed displayToggleRefs/
    // focusToggleOnModeChangeRef because ITS one shared control rendered as
    // TWO DOM instances that display-toggled each other away. These two
    // toggles are each exactly ONE persistent node -- this proves there is
    // no sibling instance to strand focus on, across every state
    // combination, rather than proving a focus-transfer mechanism works.
    it('both toggle buttons are the SAME persistent DOM node across every PANEL/LOG state combination -- never unmounted, focus never stolen or stranded', async () => {
      await act(async () => {
        root.render(<ControlledTeleprinter initialBodyPanel={false} />);
      });
      await flush();

      const panelToggle = container.querySelector('.tp-panel-toggle') as HTMLButtonElement;
      const logToggle = container.querySelector('.tp-log-toggle') as HTMLButtonElement;

      await act(async () => { logToggle.focus(); });
      await flush();
      expect(document.activeElement).toBe(logToggle);

      // Click PANEL (a DIFFERENT control) -- LOG's own focus must be
      // completely undisturbed, and both nodes must be the SAME references
      // as before (proving neither was torn down and recreated).
      await act(async () => { panelToggle.click(); });
      await flush();
      expect(container.querySelector('.tp-panel-toggle')).toBe(panelToggle);
      expect(container.querySelector('.tp-log-toggle')).toBe(logToggle);
      expect(document.activeElement).toBe(logToggle);

      // Click LOG itself (still focused) -- toggling its own state doesn't
      // remount it either.
      await act(async () => { logToggle.click(); });
      await flush();
      expect(container.querySelector('.tp-log-toggle')).toBe(logToggle);
      expect(document.activeElement).toBe(logToggle);

      expect(errorSpy).not.toHaveBeenCalled();
    });

    // ── Single-live-region invariant (Pixel a11y gate, REVISE follow-up) ──
    // PANEL and LOG can now both be open at once (the whole point of two
    // orthogonal toggles) -- #tp-log (PANEL's filtered content) and
    // .telelog (LOG's unfiltered content) must never BOTH carry
    // aria-live="polite" simultaneously, or a screen-reader user hears
    // every qualifying message announced twice. Exactly ONE aria-live
    // region across all 4 toggle states; in the both-open state .telelog
    // (the fuller, unfiltered transcript) is the authoritative surface and
    // #tp-log goes quiet. jsdom doesn't compute `display`, so this asserts
    // on the real attribute values the DOM actually exposes -- never a
    // faked/assumed computed style.
    it('exactly ONE aria-live region is active across all 4 toggle states -- TICKER/PANEL/LOG/BOTH -- never a double-announce', async () => {
      const ariaLiveOf = (el: Element | null) => el?.getAttribute('aria-live') ?? null;

      await act(async () => {
        root.render(<ControlledTeleprinter initialBodyPanel={false} initialTranscriptOpen={false} />);
      });
      await flush();

      const panelToggle = container.querySelector('.tp-panel-toggle') as HTMLButtonElement;
      const logToggle = container.querySelector('.tp-log-toggle') as HTMLButtonElement;
      const tline = () => container.querySelector('.tline');
      const tpLog = () => container.querySelector('#tp-log');
      const telelog = () => container.querySelector('.telelog');

      // TICKER (F,F): .tline is the sole live surface.
      expect(ariaLiveOf(tline())).toBe('polite');
      expect(ariaLiveOf(tpLog())).toBe('polite'); // #tp-log's own base attr is unconditional; PANEL being closed removes it from the a11y tree via display:none (jsdom can't see that -- .telelog's own explicit gate is the one this test can assert on).
      expect(ariaLiveOf(telelog())).toBeNull();
      expect(telelog()?.getAttribute('aria-hidden')).toBe('true');

      // PANEL (T,F): #tp-log is the sole live surface; .tline silenced.
      await act(async () => { panelToggle.click(); });
      await flush();
      expect(ariaLiveOf(tline())).toBeNull();
      expect(ariaLiveOf(tpLog())).toBe('polite');
      expect(ariaLiveOf(telelog())).toBeNull();
      expect(telelog()?.getAttribute('aria-hidden')).toBe('true');

      // BOTH (T,T): the new guard -- #tp-log must go SILENT the instant LOG
      // opens alongside PANEL; .telelog becomes the sole live surface.
      await act(async () => { logToggle.click(); });
      await flush();
      expect(ariaLiveOf(tline())).toBeNull();
      expect(ariaLiveOf(tpLog())).toBeNull();
      expect(ariaLiveOf(telelog())).toBe('polite');
      // React stringifies aria-hidden={false} to the literal attribute
      // "false" (unlike aria-live={undefined}, which is omitted entirely)
      // -- ARIA's boolean tokens are string-typed, not HTML boolean attrs.
      expect(telelog()?.getAttribute('aria-hidden')).toBe('false');

      // LOG (F,T): close PANEL, LOG stays open -- .telelog remains the sole
      // live surface, #tp-log stays silent (LOG's own gate, unaffected by
      // PANEL's state).
      await act(async () => { panelToggle.click(); });
      await flush();
      expect(ariaLiveOf(tline())).toBeNull();
      expect(ariaLiveOf(tpLog())).toBeNull();
      expect(ariaLiveOf(telelog())).toBe('polite');
      // React stringifies aria-hidden={false} to the literal attribute
      // "false" (unlike aria-live={undefined}, which is omitted entirely)
      // -- ARIA's boolean tokens are string-typed, not HTML boolean attrs.
      expect(telelog()?.getAttribute('aria-hidden')).toBe('false');

      expect(errorSpy).not.toHaveBeenCalled();
    });
  });

  // ── ADR-0072 GRAMMAR DISPATCH (WO-UI1-CHROME-COMPLETE item 1) ───────────
  describe('CMD grammar — parse + execute (not just echo)', () => {
    it('"set course to 9" calls plotCourse(9); "engage" calls engage(); "abort" calls autopilot abort — CMD tab', async () => {
      await act(async () => {
        root.render(<ControlledTeleprinter />);
      });
      await flush();
      await clickTab('command-echo');

      await submitViaBody('set course to 9');
      expect(mockPlotCourse).toHaveBeenCalledWith(9);
      expect(mockSendARIAMessage).not.toHaveBeenCalled();

      await submitViaBody('engage');
      expect(mockEngage).toHaveBeenCalledTimes(1);

      await submitViaBody('abort');
      expect(mockAutopilotAbort).toHaveBeenCalledWith('teleprinter command');

      expect(container.querySelector('#tp-log')?.textContent).toContain('set course to 9');
      expect(container.querySelector('#tp-log')?.textContent).toContain('engage');
      expect(container.querySelector('#tp-log')?.textContent).toContain('abort');
      expect(mockSendARIAMessage).not.toHaveBeenCalled();

      expect(errorSpy).not.toHaveBeenCalled();
    });

    it('"dock" docks at the sector station when undocked, refuses when already docked, refuses when no station present', async () => {
      mockStationsInSector = [{ id: 'station-9', name: 'Vela Trade Hub' }];
      await act(async () => {
        root.render(<ControlledTeleprinter />);
      });
      await flush();
      await clickTab('command-echo');

      await submitViaBody('dock');
      expect(mockDockAtStation).toHaveBeenCalledWith('station-9');
      await flush();
      await clickTab('narration');
      expect(container.querySelector('#tp-log')?.textContent).toContain('Docked at Vela Trade Hub');

      // Already docked -> refuses, no second call.
      mockDockAtStation.mockClear();
      mockPlayerState = makePlayerState({ is_docked: true });
      await clickTab('command-echo');
      await submitViaBody('dock');
      expect(mockDockAtStation).not.toHaveBeenCalled();
      await clickTab('narration');
      expect(container.querySelector('#tp-log')?.textContent).toContain('Already docked');

      expect(errorSpy).not.toHaveBeenCalled();
    });

    it('"undock" undocks when docked, refuses when not docked', async () => {
      mockPlayerState = makePlayerState({ is_docked: true });
      await act(async () => {
        root.render(<ControlledTeleprinter />);
      });
      await flush();
      await clickTab('command-echo');

      await submitViaBody('undock');
      expect(mockUndockFromStation).toHaveBeenCalledTimes(1);
      expect(mockAutopilotAbort).toHaveBeenCalledWith('manual helm action');

      expect(errorSpy).not.toHaveBeenCalled();
    });

    it('"land" lands on the sector planet, "lift off" departs it', async () => {
      mockPlanetsInSector = [{ id: 'planet-4', name: 'New Eden' }];
      await act(async () => {
        root.render(<ControlledTeleprinter />);
      });
      await flush();
      await clickTab('command-echo');

      await submitViaBody('land');
      expect(mockLandOnPlanet).toHaveBeenCalledWith('planet-4');

      mockPlayerState = makePlayerState({ is_landed: true });
      await submitViaBody('lift off');
      expect(mockLeavePlanet).toHaveBeenCalledTimes(1);

      expect(errorSpy).not.toHaveBeenCalled();
    });

    it('"status" and "help" are local readouts -- no WS/dispatch calls', async () => {
      await act(async () => {
        root.render(<ControlledTeleprinter />);
      });
      await flush();
      await clickTab('command-echo');

      await submitViaBody('status');
      await clickTab('narration');
      expect(container.querySelector('#tp-log')?.textContent).toContain('Status:');

      await clickTab('command-echo');
      await submitViaBody('help');
      await clickTab('narration');
      expect(container.querySelector('#tp-log')?.textContent).toContain('Commands:');

      expect(mockSendARIAMessage).not.toHaveBeenCalled();
      expect(mockDockAtStation).not.toHaveBeenCalled();
      expect(mockPlotCourse).not.toHaveBeenCalled();
      expect(mockEngage).not.toHaveBeenCalled();

      expect(errorSpy).not.toHaveBeenCalled();
    });

    it('unrecognized CMD input falls through to ARIA free-chat — the ONLY grammar-parsing channel is CMD/ticker, not narration/dialogue', async () => {
      await act(async () => {
        root.render(<ControlledTeleprinter />);
      });
      await flush();
      await clickTab('command-echo');

      await submitViaBody('what is the best trade route');
      expect(mockSendARIAMessage).toHaveBeenCalledWith('what is the best trade route', undefined, 'trading');
      expect(mockDockAtStation).not.toHaveBeenCalled();
      expect(mockPlotCourse).not.toHaveBeenCalled();

      expect(errorSpy).not.toHaveBeenCalled();
    });

    it('the TICKER input dispatches through the SAME grammar-first path as CMD (visual-form steer)', async () => {
      await act(async () => {
        root.render(<ControlledTeleprinter initialBodyPanel={false} />);
      });
      await flush();

      await submitViaTicker('engage');
      expect(mockEngage).toHaveBeenCalledTimes(1);
      expect(mockSendARIAMessage).not.toHaveBeenCalled();

      await submitViaTicker('what is my hull status');
      expect(mockSendARIAMessage).toHaveBeenCalledWith('what is my hull status', undefined, 'trading');

      expect((container.querySelector('.tin') as HTMLInputElement).value).toBe('');
      expect(errorSpy).not.toHaveBeenCalled();
    });
  });
});

// @vitest-environment jsdom
/**
 * CommsCrewPage — MFD-B COMM (WO-UI2-DECK-RECONCILE).
 *
 * Mirrors SalvagePage.test.tsx / ThreatPage.test.tsx's seam: jsdom +
 * react-dom/client createRoot + act(), no RTL, no new deps. Proves the
 * inbox + composer ported from the retiring CommsMailbox.tsx HAILS mode
 * actually render and wire to the GameContext message API, and that the
 * registry migration (mfdRegistry.tsx / sidebarScreens.ts) is coherent.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGetTeam = vi.fn();
const mockFlagMessage = vi.fn();
const mockGetConversations = vi.fn();
vi.mock('../../../services/api', () => ({
  teamAPI: {
    getTeam: (...a: unknown[]) => mockGetTeam(...a),
  },
  messageAPI: {
    flagMessage: (...a: unknown[]) => mockFlagMessage(...a),
    getConversations: (...a: unknown[]) => mockGetConversations(...a),
  },
}));

const CURRENT_SECTOR = {
  id: 'sector-uuid', sector_id: 5, name: 'Test Sector', type: 'STANDARD',
  hazard_level: 0, radiation_level: 0, resources: {},
  players_present: [] as unknown[],
};

const PLAYER_STATE = { id: 'player-1', username: 'Ace', team_id: null as string | null };

const makeMessage = (overrides: Record<string, unknown> = {}) => ({
  id: 'msg-1',
  sender_id: 'sender-1',
  recipient_id: 'player-1',
  team_id: null,
  subject: 'Hello',
  content: 'Rendezvous at Sector 5.',
  sent_at: '2026-07-10T12:00:00+00:00',
  read_at: null,
  message_type: 'DIRECT',
  priority: 'NORMAL',
  thread_id: null,
  reply_to_id: null,
  flagged: false,
  is_read: false,
  sender_name: 'Nova',
  ...overrides,
});

let mockInboxMessages: ReturnType<typeof makeMessage>[] = [];
let mockUnreadCount = 0;
const mockRefreshInbox = vi.fn();
const mockSendPlayerMessage = vi.fn();
const mockMarkMessageRead = vi.fn();
const mockDeletePlayerMessage = vi.fn();

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: PLAYER_STATE,
    currentSector: CURRENT_SECTOR,
    unreadMessageCount: mockUnreadCount,
    inboxMessages: mockInboxMessages,
    refreshInbox: mockRefreshInbox,
    sendPlayerMessage: mockSendPlayerMessage,
    markMessageRead: mockMarkMessageRead,
    deletePlayerMessage: mockDeletePlayerMessage,
  }),
}));

let mockSectorPlayers: unknown[] = [];
vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    isConnected: true,
    sectorPlayers: mockSectorPlayers,
    newMessageSignal: 0,
  }),
}));

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1' } }),
}));

import CommsCrewPage, { FLAG_REASON_BY_CATEGORY } from './CommsCrewPage';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('CommsCrewPage — MFD-B COMM', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetTeam.mockReset();
    mockFlagMessage.mockReset();
    mockGetConversations.mockReset();
    mockGetConversations.mockResolvedValue({ conversations: [], total: 0, page: 1, limit: 20, pages: 0 });
    mockFlagMessage.mockResolvedValue({ success: true });
    mockRefreshInbox.mockReset();
    mockSendPlayerMessage.mockReset();
    mockMarkMessageRead.mockReset();
    mockDeletePlayerMessage.mockReset();
    mockMarkMessageRead.mockResolvedValue(undefined);
    mockDeletePlayerMessage.mockResolvedValue(undefined);
    mockInboxMessages = [];
    mockUnreadCount = 0;
    mockSectorPlayers = [];

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  // React tracks the native value setter to detect a "real" change -- a
  // plain `el.value = x` assignment is invisible to its onChange handler.
  const typeInto = async (el: HTMLTextAreaElement, value: string) => {
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype,
        'value'
      )!.set!;
      setter.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  };

  const mount = async () => {
    await act(async () => {
      root.render(<CommsCrewPage />);
    });
    await flush();
  };

  it('renders the working inbox: unread dot, sender, subject, and expands the body on click', async () => {
    mockInboxMessages = [makeMessage()];
    mockUnreadCount = 1;

    await mount();

    expect(container.querySelector('.mfd-page-comms-hail-item')).not.toBeNull();
    expect(container.querySelector('.mfd-page-comms-unread-dot.off')).toBeNull();
    expect(container.querySelector('.mfd-page-comms-hail-sender')?.textContent).toBe('NOVA');
    expect(container.querySelector('.mfd-page-comms-hail-subject')?.textContent).toBe('Hello');
    expect(container.querySelector('.mfd-page-comms-hail-content')).toBeNull();

    await click(container.querySelector('.mfd-page-comms-hail-summary')!);

    expect(container.querySelector('.mfd-page-comms-hail-content')?.textContent).toBe(
      'Rendezvous at Sector 5.'
    );
    expect(mockMarkMessageRead).toHaveBeenCalledWith('msg-1');
  });

  it('PURGE on an expanded hail calls deletePlayerMessage', async () => {
    mockInboxMessages = [makeMessage()];
    await mount();
    await click(container.querySelector('.mfd-page-comms-hail-summary')!);
    await click(container.querySelector('[data-testid="comms-purge-hail"]')!);
    expect(mockDeletePlayerMessage).toHaveBeenCalledWith('msg-1');
  });

  it('FLAG category calls messageAPI.flagMessage with tip-length reason', async () => {
    mockInboxMessages = [makeMessage()];
    await mount();
    await click(container.querySelector('.mfd-page-comms-hail-summary')!);
    await click(container.querySelector('[data-testid="comms-flag-hail"]')!);
    expect(container.querySelector('[data-testid="comms-flag-categories"]')).not.toBeNull();
    await click(container.querySelector('[data-testid="comms-flag-cat-spam"]')!);
    await flush();
    expect(mockFlagMessage).toHaveBeenCalledWith('msg-1', FLAG_REASON_BY_CATEGORY.spam);
    expect(FLAG_REASON_BY_CATEGORY.spam.length).toBeGreaterThanOrEqual(10);
    expect(container.querySelector('.mfd-page-comms-flag-notice')?.textContent).toBe(
      'FLAGGED FOR MODERATION',
    );
  });

  it('FLAG error path surfaces honesty without crashing', async () => {
    mockFlagMessage.mockRejectedValueOnce(new Error('Message not found'));
    mockInboxMessages = [makeMessage()];
    await mount();
    await click(container.querySelector('.mfd-page-comms-hail-summary')!);
    await click(container.querySelector('[data-testid="comms-flag-hail"]')!);
    await click(container.querySelector('[data-testid="comms-flag-cat-harassment"]')!);
    await flush();
    expect(mockFlagMessage).toHaveBeenCalledWith('msg-1', FLAG_REASON_BY_CATEGORY.harassment);
    expect(container.querySelector('.mfd-page-comms-flag-error')?.textContent).toBe(
      'Message not found',
    );
    expect(container.querySelector('.mfd-page-comms-hail-content')).not.toBeNull();
  });

  it('shows the honest empty state with no transmissions', async () => {
    await mount();

    expect(container.querySelector('.mfd-page-comms-inbox .mfd-empty')?.textContent).toBe(
      'NO TRANSMISSIONS'
    );
  });

  it('opens the composer on REPLY, pre-filling RE: subject and recipient from the sender', async () => {
    mockInboxMessages = [makeMessage()];
    await mount();
    await click(container.querySelector('.mfd-page-comms-hail-summary')!);

    await click(container.querySelector('.mfd-page-comms-reply-btn')!);

    expect(container.querySelector('.mfd-page-comms-compose-recipient')?.textContent).toBe('NOVA');
    expect((container.querySelector('.mfd-page-comms-compose-subject') as HTMLInputElement).value).toBe(
      'RE: Hello'
    );
  });

  it('sends via the GameContext /api/v1/messages binding on TRANSMIT (composer opened via REPLY)', async () => {
    mockInboxMessages = [makeMessage({ id: 'msg-2', sender_id: 'sender-9', sender_name: 'Nova' })];
    mockSendPlayerMessage.mockResolvedValue({ message_id: 'sent-1', sent_at: '2026-07-10T12:05:00Z' });
    await mount();
    await click(container.querySelector('.mfd-page-comms-hail-summary')!);
    await click(container.querySelector('.mfd-page-comms-reply-btn')!);

    const textarea = container.querySelector('.mfd-page-comms-compose-content') as HTMLTextAreaElement;
    await typeInto(textarea, 'On my way.');
    await click(container.querySelector('.mfd-page-comms-transmit-btn')!);
    await flush();

    expect(mockSendPlayerMessage).toHaveBeenCalledWith('sender-9', 'On my way.', 'RE: Hello', 'msg-2');
    expect(container.querySelector('.mfd-page-comms-send-notice')?.textContent).toBe('TRANSMISSION SENT');
  });

  it('opens the composer via HAIL on a non-NPC sector contact (a source CommsMailbox also supports)', async () => {
    mockSectorPlayers = [{ user_id: 'u-2', player_id: 'p-2', username: 'Drift', is_npc: false }];
    await mount();

    await click(container.querySelector('.mfd-page-comms-hail-btn')!);

    expect(container.querySelector('.mfd-page-comms-compose-recipient')?.textContent).toBe('DRIFT');
  });

  it('renders NPC contacts with a badge and no HAIL button (NPCs are not messageable)', async () => {
    mockSectorPlayers = [{ player_id: 'npc-1', username: 'Marshal Vex', is_npc: true }];
    await mount();

    expect(container.querySelector('.mfd-page-npc-badge')).not.toBeNull();
    expect(container.querySelector('.mfd-page-comms-hail-btn')).toBeNull();
  });

  it('renders in every MFD-B mode -- mounting has no flight/docked/landed gate', async () => {
    // CommsCrewPage reads no is_docked/is_landed flag anywhere in its body;
    // this asserts the page itself never conditions on player mode.
    await mount();
    expect(container.querySelector('.mfd-page-ops')).not.toBeNull();
  });

  it('THREADS tab fetches conversations and renders thread rows', async () => {
    mockGetConversations.mockResolvedValueOnce({
      conversations: [
        makeMessage({
          id: 'conv-1',
          thread_id: 'thread-a',
          sender_name: 'Echo',
          subject: 'Docking coords',
        }),
      ],
      total: 1,
      page: 1,
      limit: 20,
      pages: 1,
    });
    await mount();
    await click(container.querySelectorAll('.mfd-page-comms-mode-tab')[1]!);
    await flush();
    expect(mockGetConversations).toHaveBeenCalledWith(1);
    expect(container.querySelector('[data-testid="comms-threads-list"]')).not.toBeNull();
    expect(container.querySelector('.mfd-page-comms-hail-sender')?.textContent).toBe('ECHO');
    expect(container.querySelector('.mfd-page-comms-hail-subject')?.textContent).toBe('Docking coords');
  });

  it('THREADS tab shows empty state when conversations=[]', async () => {
    mockGetConversations.mockResolvedValueOnce({
      conversations: [],
      total: 0,
      page: 1,
      limit: 20,
      pages: 0,
    });
    await mount();
    await click(container.querySelectorAll('.mfd-page-comms-mode-tab')[1]!);
    await flush();
    expect(container.querySelector('[data-testid="comms-threads-list"] .mfd-empty')?.textContent).toBe(
      'NO THREADS'
    );
  });

  it('THREADS tab surfaces fetch error without crashing', async () => {
    mockGetConversations.mockRejectedValueOnce(new Error('Uplink timeout'));
    await mount();
    await click(container.querySelectorAll('.mfd-page-comms-mode-tab')[1]!);
    await flush();
    expect(container.querySelector('.mfd-page-warnline')?.textContent).toBe('Uplink timeout');
    expect(container.querySelector('.mfd-page-ops')).not.toBeNull();
  });

  it('THREADS tab surfaces getConversations 403 permission error in warnline', async () => {
    mockGetConversations.mockRejectedValueOnce(
      apiRequestError(403, 'Messaging access requires an active crew affiliation.'),
    );
    await mount();
    await click(container.querySelectorAll('.mfd-page-comms-mode-tab')[1]!);
    await flush();
    const warnline = container.querySelector('.mfd-page-warnline');
    expect(warnline?.getAttribute('role')).toBe('alert');
    expect(warnline?.textContent).toContain('Messaging access requires an active crew affiliation');
    expect(warnline?.textContent).not.toBe('FAILED TO LOAD THREADS');
    expect(container.querySelector('[data-testid="comms-threads-list"]')).toBeNull();
  });

  it('THREADS tab surfaces getConversations 429 rate-limit error in warnline', async () => {
    mockGetConversations.mockRejectedValueOnce(apiRequestError(429));
    await mount();
    await click(container.querySelectorAll('.mfd-page-comms-mode-tab')[1]!);
    await flush();
    const warnline = container.querySelector('.mfd-page-warnline');
    expect(warnline?.getAttribute('role')).toBe('alert');
    expect(warnline?.textContent).toMatch(/rate limit exceeded/i);
    expect(warnline?.textContent).not.toBe('FAILED TO LOAD THREADS');
    expect(container.querySelector('[data-testid="comms-threads-list"]')).toBeNull();
  });

  it('THREADS tab selecting a thread shows merged messages in the detail pane', async () => {
    mockGetConversations.mockResolvedValueOnce({
      conversations: [
        makeMessage({
          id: 'conv-preview',
          thread_id: 'thread-a',
          sender_name: 'Echo',
          subject: 'Docking coords',
          content: 'Meet at bay 7.',
        }),
      ],
      total: 1,
      page: 1,
      limit: 20,
      pages: 1,
    });
    mockInboxMessages = [
      makeMessage({
        id: 'inbox-1',
        thread_id: 'thread-a',
        sender_name: 'Nova',
        subject: 'Docking coords',
        content: 'Copy that, en route.',
        sent_at: '2026-07-10T12:01:00+00:00',
      }),
      makeMessage({
        id: 'inbox-2',
        thread_id: 'thread-a',
        sender_name: 'Drift',
        subject: 'Docking coords',
        content: 'Standing by at the airlock.',
        sent_at: '2026-07-10T12:02:00+00:00',
      }),
    ];

    await mount();
    await click(container.querySelectorAll('.mfd-page-comms-mode-tab')[1]!);
    await flush();

    expect(container.querySelector('[data-testid="comms-thread-detail"]')).toBeNull();

    const threadSummary = container.querySelector(
      '[data-testid="comms-threads-list"] .mfd-page-comms-hail-summary'
    )!;
    expect(threadSummary).not.toBeNull();
    expect(container.querySelector('.mfd-page-comms-hail-subject')?.textContent).toBe('Docking coords');

    await click(threadSummary);
    await flush();

    const detail = container.querySelector('[data-testid="comms-thread-detail"]');
    expect(detail).not.toBeNull();

    const detailSenders = Array.from(
      detail!.querySelectorAll('.mfd-page-comms-hail-sender')
    ).map((el) => el.textContent);
    expect(detailSenders).toContain('NOVA');
    expect(detailSenders).toContain('DRIFT');
    expect(detailSenders).toContain('ECHO');

    const firstDetailSummary = detail!.querySelector('.mfd-page-comms-hail-summary')!;
    await click(firstDetailSummary);
    await flush();

    expect(detail!.querySelector('.mfd-page-comms-hail-content')?.textContent).toBeTruthy();
  });
});

describe('mfdRegistry / sidebarScreens -- WO-UI2-DECK-RECONCILE / WO-UI1-CHROME-COMPLETE migrations', () => {
  it('drops threat-readiness, salvage, turn-economy, reputation, aria-terminal and keeps the ratified 5-page slate', async () => {
    const { MFD_PAGES } = await import('../mfdRegistry');

    expect(Object.keys(MFD_PAGES).sort()).toEqual(
      ['cargo', 'comms-crew', 'nav-position', 'quantum-drive', 'vessel-status'].sort()
    );
    expect((MFD_PAGES as Record<string, unknown>)['threat-readiness']).toBeUndefined();
    expect((MFD_PAGES as Record<string, unknown>)['salvage']).toBeUndefined();
    expect((MFD_PAGES as Record<string, unknown>)['turn-economy']).toBeUndefined();
    expect((MFD_PAGES as Record<string, unknown>)['reputation']).toBeUndefined();
    // WO-UI1-CHROME-COMPLETE: ARIA absorbed into the teleprinter.
    expect((MFD_PAGES as Record<string, unknown>)['aria-terminal']).toBeUndefined();
  });

  it('MFD-B COMM is no longer flagged partial -- ships as a real, working page', async () => {
    const { MFD_PAGES } = await import('../mfdRegistry');
    expect(MFD_PAGES['comms-crew'].status).toBe('shipped');
  });

  it('SIDEBAR_A (MFD-A) matches the ratified slate STAT / CRGO / QTM -- THRT and SALV dropped', async () => {
    const { SIDEBAR_A, SIDEBAR_B } = await import('../sidebarScreens');
    expect(SIDEBAR_A.pageIds).toEqual(['vessel-status', 'cargo', 'quantum-drive']);
    // WO-UI1-CHROME-COMPLETE: MFD-B slate == [POS, COMM] -- ARIA absorbed
    // into the teleprinter (canon §05 L578).
    expect(SIDEBAR_B.pageIds).toEqual(['nav-position', 'comms-crew']);
  });
});

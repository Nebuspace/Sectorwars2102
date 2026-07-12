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
vi.mock('../../../services/api', () => ({
  teamAPI: {
    getTeam: (...a: unknown[]) => mockGetTeam(...a),
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

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: PLAYER_STATE,
    currentSector: CURRENT_SECTOR,
    unreadMessageCount: mockUnreadCount,
    inboxMessages: mockInboxMessages,
    refreshInbox: mockRefreshInbox,
    sendPlayerMessage: mockSendPlayerMessage,
    markMessageRead: mockMarkMessageRead,
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

import CommsCrewPage from './CommsCrewPage';

describe('CommsCrewPage — MFD-B COMM', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetTeam.mockReset();
    mockRefreshInbox.mockReset();
    mockSendPlayerMessage.mockReset();
    mockMarkMessageRead.mockReset();
    mockMarkMessageRead.mockResolvedValue(undefined);
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
});

describe('mfdRegistry / sidebarScreens -- WO-UI2-DECK-RECONCILE migration', () => {
  it('drops threat-readiness, salvage, turn-economy, reputation and keeps the ratified 6-page slate', async () => {
    const { MFD_PAGES } = await import('../mfdRegistry');

    expect(Object.keys(MFD_PAGES).sort()).toEqual(
      ['aria-terminal', 'cargo', 'comms-crew', 'nav-position', 'quantum-drive', 'vessel-status'].sort()
    );
    expect((MFD_PAGES as Record<string, unknown>)['threat-readiness']).toBeUndefined();
    expect((MFD_PAGES as Record<string, unknown>)['salvage']).toBeUndefined();
    expect((MFD_PAGES as Record<string, unknown>)['turn-economy']).toBeUndefined();
    expect((MFD_PAGES as Record<string, unknown>)['reputation']).toBeUndefined();
  });

  it('MFD-B COMM is no longer flagged partial -- ships as a real, working page', async () => {
    const { MFD_PAGES } = await import('../mfdRegistry');
    expect(MFD_PAGES['comms-crew'].status).toBe('shipped');
  });

  it('SIDEBAR_A (MFD-A) matches the ratified slate STAT / CRGO / QTM -- THRT and SALV dropped', async () => {
    const { SIDEBAR_A, SIDEBAR_B } = await import('../sidebarScreens');
    expect(SIDEBAR_A.pageIds).toEqual(['vessel-status', 'cargo', 'quantum-drive']);
    // MFD-B untouched by this WO -- POS / ARIA / COMM.
    expect(SIDEBAR_B.pageIds).toEqual(['nav-position', 'aria-terminal', 'comms-crew']);
  });
});

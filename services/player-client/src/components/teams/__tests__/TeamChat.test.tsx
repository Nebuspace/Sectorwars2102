// @vitest-environment jsdom
/**
 * TeamChat — load/reorder messages, empty state, send + error.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getMessages = vi.fn();
const sendMessage = vi.fn();

vi.mock('../../../services/api', () => ({
  gameAPI: {
    team: {
      getMessages: (...args: unknown[]) => getMessages(...args),
      sendMessage: (...args: unknown[]) => sendMessage(...args),
    },
  },
}));

import { TeamChat } from '../TeamChat';
import type { TeamMember, TeamMessageApiResponse } from '../../../types/team';

const members: TeamMember[] = [
  {
    id: 'm1',
    playerId: 'p1',
    playerName: 'Ada',
    role: 'leader',
    joinedAt: '2026-01-01T00:00:00Z',
    contributions: { credits: 0, resources: 0, combatKills: 0 },
    online: true,
    location: { sectorId: 's1', sectorName: 'Alpha' },
    shipType: 'Scout',
    combatRating: 1,
  },
  {
    id: 'm2',
    playerId: 'p2',
    playerName: 'Nova',
    role: 'officer',
    joinedAt: '2026-01-01T00:00:00Z',
    contributions: { credits: 0, resources: 0, combatKills: 0 },
    online: false,
    location: { sectorId: 's1', sectorName: 'Alpha' },
    shipType: 'Hauler',
    combatRating: 1,
  },
];

const msg = (
  over: Partial<TeamMessageApiResponse> & Pick<TeamMessageApiResponse, 'id' | 'sender_id' | 'content'>,
): TeamMessageApiResponse => ({
  sender_name: 'Nova',
  subject: '',
  sent_at: new Date().toISOString(),
  read_at: null,
  priority: 'normal',
  is_read: true,
  ...over,
});

describe('TeamChat', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.clearAllMocks();
    getMessages.mockResolvedValue([]);
    sendMessage.mockResolvedValue(undefined);
    // scrollIntoView is missing in jsdom
    Element.prototype.scrollIntoView = vi.fn();
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

  it('shows loading then empty state with member count', async () => {
    let resolveLoad: (v: TeamMessageApiResponse[]) => void = () => {};
    getMessages.mockReturnValue(
      new Promise<TeamMessageApiResponse[]>((resolve) => {
        resolveLoad = resolve;
      }),
    );

    await act(async () => {
      root.render(<TeamChat teamId="t1" playerId="p1" members={members} />);
    });
    expect(container.textContent).toContain('Loading chat');

    await act(async () => {
      resolveLoad([]);
    });
    expect(container.textContent).toContain('No messages yet');
    expect(container.textContent).toContain('2 members');
  });

  it('reverses newest-first API payload and badges roles', async () => {
    getMessages.mockResolvedValue([
      msg({ id: '2', sender_id: 'p1', sender_name: 'Ada', content: 'second' }),
      msg({ id: '1', sender_id: 'p2', sender_name: 'Nova', content: 'first' }),
    ]);

    await act(async () => {
      root.render(<TeamChat teamId="t1" playerId="p1" members={members} />);
    });

    const contents = Array.from(container.querySelectorAll('.message-content')).map(
      (el) => el.textContent,
    );
    expect(contents).toEqual(['first', 'second']);
    expect(container.textContent).toContain('👑');
    expect(container.textContent).toContain('⭐');
    expect(container.querySelector('.chat-message.own')).toBeTruthy();
  });

  const typeInto = async (input: HTMLInputElement, value: string) => {
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
      setter?.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  };

  it('sends a trimmed message and refreshes the thread', async () => {
    getMessages.mockResolvedValueOnce([]).mockResolvedValueOnce([
      msg({ id: '3', sender_id: 'p1', sender_name: 'Ada', content: 'hello crew' }),
    ]);

    await act(async () => {
      root.render(<TeamChat teamId="t1" playerId="p1" members={members} />);
    });

    await typeInto(container.querySelector('input') as HTMLInputElement, '  hello crew  ');
    await act(async () => {
      (container.querySelector('button[type="submit"]') as HTMLButtonElement).click();
    });

    expect(sendMessage).toHaveBeenCalledWith('t1', 'hello crew');
    expect(container.textContent).toContain('hello crew');
  });

  it('surfaces send errors', async () => {
    getMessages.mockResolvedValue([]);
    sendMessage.mockRejectedValue(new Error('radio silence'));

    await act(async () => {
      root.render(<TeamChat teamId="t1" playerId="p1" members={members} />);
    });

    await typeInto(container.querySelector('input') as HTMLInputElement, 'ping');
    await act(async () => {
      (container.querySelector('form') as HTMLFormElement).requestSubmit();
    });

    expect(container.querySelector('.form-error')?.textContent).toContain('radio silence');
  });
});

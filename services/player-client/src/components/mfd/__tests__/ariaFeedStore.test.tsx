// @vitest-environment jsdom
/**
 * ariaFeedStore — module-level pub/sub store backing the ARIA terminal MFD
 * page's feed (locally-generated nav narration only, never sent to the WS
 * pipe). Exercises the `ariaFeed` store object directly, plus the
 * `useAriaFeed` hook via the host-component-capture harness convention
 * (useAnnunciatorState.test.tsx). State is module-level (not per-instance),
 * so every test resets it in beforeEach/afterEach.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ariaFeed, useAriaFeed } from '../ariaFeedStore';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let latest: ReturnType<typeof useAriaFeed> | null = null;

function Harness() {
  latest = useAriaFeed();
  return null;
}

let container: HTMLElement;
let root: ReturnType<typeof createRoot>;

const render = async () => {
  await act(async () => {
    root.render(<Harness />);
  });
};

const resetStore = () => {
  ariaFeed.clearNav();
  ariaFeed.setConversationId(null);
};

beforeEach(() => {
  resetStore();
  latest = null;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  resetStore();
});

describe('ariaFeed — appendNav', () => {
  it('appends an AI-typed nav line with the given content', () => {
    ariaFeed.appendNav('Autopilot engaged.');
    const messages = ariaFeed.getNavMessages();
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({ type: 'ai', content: 'Autopilot engaged.', isNav: true });
    expect(messages[0].id).toMatch(/^nav-/);
    expect(typeof messages[0].timestamp).toBe('string');
  });

  it('does not mutate the previously-returned array reference', () => {
    ariaFeed.appendNav('first');
    const before = ariaFeed.getNavMessages();
    ariaFeed.appendNav('second');
    const after = ariaFeed.getNavMessages();
    expect(before).toHaveLength(1);
    expect(after).toHaveLength(2);
    expect(before).not.toBe(after);
  });

  it('assigns distinct ids to two appends in the same tick', () => {
    ariaFeed.appendNav('a');
    ariaFeed.appendNav('b');
    const [first, second] = ariaFeed.getNavMessages();
    expect(first.id).not.toBe(second.id);
  });
});

describe('ariaFeed — appendUserEcho', () => {
  it('appends a user-typed line with a nav-you- id prefix', () => {
    ariaFeed.appendUserEcho('warp 5');
    const messages = ariaFeed.getNavMessages();
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({ type: 'user', content: 'warp 5', isNav: true });
    expect(messages[0].id).toMatch(/^nav-you-/);
  });

  it('interleaves with appendNav in a single feed', () => {
    ariaFeed.appendUserEcho('warp 5');
    ariaFeed.appendNav('Course plotted.');
    const messages = ariaFeed.getNavMessages();
    expect(messages.map((m) => m.type)).toEqual(['user', 'ai']);
  });
});

describe('ariaFeed — conversationId', () => {
  it('defaults to null', () => {
    expect(ariaFeed.getConversationId()).toBeNull();
  });

  it('sets and clears the conversation id', () => {
    ariaFeed.setConversationId('conv-1');
    expect(ariaFeed.getConversationId()).toBe('conv-1');
    ariaFeed.setConversationId(null);
    expect(ariaFeed.getConversationId()).toBeNull();
  });
});

describe('ariaFeed — clearNav', () => {
  it('empties the nav message list without touching conversationId', () => {
    ariaFeed.appendNav('one');
    ariaFeed.setConversationId('conv-1');
    ariaFeed.clearNav();
    expect(ariaFeed.getNavMessages()).toEqual([]);
    expect(ariaFeed.getConversationId()).toBe('conv-1');
  });
});

describe('ariaFeed — subscribe', () => {
  it('notifies a listener on appendNav, setConversationId, and clearNav', () => {
    let calls = 0;
    const unsubscribe = ariaFeed.subscribe(() => {
      calls += 1;
    });

    ariaFeed.appendNav('x');
    ariaFeed.setConversationId('conv-1');
    ariaFeed.clearNav();
    expect(calls).toBe(3);

    unsubscribe();
  });

  it('stops delivering after unsubscribe', () => {
    let calls = 0;
    const unsubscribe = ariaFeed.subscribe(() => {
      calls += 1;
    });
    unsubscribe();

    ariaFeed.appendNav('x');
    expect(calls).toBe(0);
  });
});

describe('useAriaFeed', () => {
  it('reflects the current store snapshot on mount', async () => {
    ariaFeed.appendNav('pre-existing');
    ariaFeed.setConversationId('conv-1');
    await render();
    expect(latest!.navMessages).toHaveLength(1);
    expect(latest!.conversationId).toBe('conv-1');
  });

  it('re-renders with the new message when appendNav fires after mount', async () => {
    await render();
    expect(latest!.navMessages).toHaveLength(0);

    await act(async () => {
      ariaFeed.appendNav('live update');
    });
    expect(latest!.navMessages).toHaveLength(1);
    expect(latest!.navMessages[0].content).toBe('live update');
  });

  it('re-renders when conversationId changes after mount', async () => {
    await render();
    expect(latest!.conversationId).toBeNull();

    await act(async () => {
      ariaFeed.setConversationId('conv-2');
    });
    expect(latest!.conversationId).toBe('conv-2');
  });

  it('reflects clearNav after mount', async () => {
    ariaFeed.appendNav('one');
    await render();
    expect(latest!.navMessages).toHaveLength(1);

    await act(async () => {
      ariaFeed.clearNav();
    });
    expect(latest!.navMessages).toEqual([]);
  });
});

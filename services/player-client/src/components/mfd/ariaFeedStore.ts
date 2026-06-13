/**
 * ariaFeedStore — module-level store for the ARIA terminal feed.
 *
 * The retired console strip was permanently mounted, so its local nav
 * messages and conversation id lived happily in component state. As an
 * MFD page the terminal unmounts on every softkey switch — feed state
 * held in the page would be wiped, and autopilot transitions occurring
 * while another page is shown would never be narrated. This store keeps
 * the feed alive for the session, and the always-mounted MFDAlertWiring
 * (GameLayout) narrates autopilot transitions into it regardless of
 * which page is visible.
 */

import { useSyncExternalStore } from 'react';

/** Local nav message — same shape as an ariaMessages entry so they render
 *  identically in the log. Never sent to the WS pipe. */
export interface NavMessage {
  id: string;
  type: 'ai' | 'user';
  content: string;
  timestamp: string;
  isNav: true; // discriminator — used internally only, not rendered
}

let navMessages: NavMessage[] = [];
let conversationId: string | null = null;
let navSeq = 0;

const listeners = new Set<() => void>();
const emit = (): void => {
  listeners.forEach((listener) => listener());
};

function makeNavLine(content: string): NavMessage {
  return {
    id: `nav-${Date.now()}-${navSeq++}`,
    type: 'ai',
    content,
    timestamp: new Date().toISOString(),
    isNav: true,
  };
}

export const ariaFeed = {
  appendNav(content: string): void {
    navMessages = [...navMessages, makeNavLine(content)];
    emit();
  },
  /** YOU> echo for intercepted nav commands (never sent to the WS pipe). */
  appendUserEcho(content: string): void {
    navMessages = [
      ...navMessages,
      {
        id: `nav-you-${Date.now()}-${navSeq++}`,
        type: 'user',
        content,
        timestamp: new Date().toISOString(),
        isNav: true,
      },
    ];
    emit();
  },
  setConversationId(id: string | null): void {
    conversationId = id;
    emit();
  },
  clearNav(): void {
    navMessages = [];
    emit();
  },
  getNavMessages: (): NavMessage[] => navMessages,
  getConversationId: (): string | null => conversationId,
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export const useAriaFeed = (): {
  navMessages: NavMessage[];
  conversationId: string | null;
} => {
  const messages = useSyncExternalStore(ariaFeed.subscribe, ariaFeed.getNavMessages);
  const convId = useSyncExternalStore(ariaFeed.subscribe, ariaFeed.getConversationId);
  return { navMessages: messages, conversationId: convId };
};

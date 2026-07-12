import { createContext, useContext } from 'react';

/**
 * ShellContext — lets GameLayout detect when it is being rendered INSIDE an
 * already-persistent outer shell (WO-UI0-PERSISTENT-SHELL) so a nested call
 * to <GameLayout> (e.g. a page component that wraps itself in its own
 * GameLayout, mirroring EmbeddedContext's HangarShell/ColonialShell pattern)
 * becomes a no-op passthrough instead of nesting a second cockpit chrome.
 * Default false → unchanged standalone behaviour. Dormant until the
 * persistent-shell lane (Lane A) sets it true; correct-but-inert until then.
 */
export const ShellPresenceContext = createContext<boolean>(false);

export const useShellPresent = (): boolean => useContext(ShellPresenceContext);

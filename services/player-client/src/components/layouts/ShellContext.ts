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

/**
 * ShellSlotsContext (WO-UI0-SHELL-TRANSPLANT) — the two portal targets a
 * page rendered as GameLayout's `{children}` can teleport its real content
 * into: `.band` (the ambient scene row) and `.deck` (the instrument deck,
 * inside `.lower`). GameLayout owns both DOM nodes (empty grid slots it
 * always renders) and publishes them here as ELEMENT-IN-STATE (not a plain
 * ref) — a callback-ref (`ref={setBandEl}`) triggers a GameLayout re-render
 * the instant the node mounts, which is what lets a consumer's very next
 * render see a non-null target and portal into it.
 *
 * Default `{ bandEl: null, deckEl: null }` so a consumer rendered WITHOUT a
 * real GameLayout ancestor (every GameDashboard.*.test.tsx mocks GameLayout
 * out entirely and mounts `<GameDashboard/>` standalone) gets null slots —
 * the consumer's own guard is expected to fall back to rendering its
 * content INLINE in that case (not to render nothing), so those tests keep
 * seeing the exact same DOM shape they always have. In production the
 * fallback is only ever visible for the first paint of a route that renders
 * content needing these slots (band/deck ref callbacks fire in the same
 * commit as their own mount), and the very next commit portals correctly.
 */
export interface ShellSlots {
  bandEl: HTMLDivElement | null;
  deckEl: HTMLDivElement | null;
}

export const ShellSlotsContext = createContext<ShellSlots>({ bandEl: null, deckEl: null });

export const useShellSlots = (): ShellSlots => useContext(ShellSlotsContext);

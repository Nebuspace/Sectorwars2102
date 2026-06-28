import { createContext, useContext } from 'react';

/**
 * EmbeddedContext — lets a full-page cockpit view (one that wraps itself in
 * <GameLayout> via a shell, e.g. ShipSelector's HangarShell / PlanetManager's
 * ColonialShell) render its CONTENT ONLY when it's being embedded inside another
 * view (WO-PLAYERINFO id=144: the consolidated PlayerInfo composes those views
 * as sections). Default false → unchanged standalone behaviour (its own
 * GameLayout shell on /game/ships, /game/planets, …). A composer sets
 * <EmbeddedContext.Provider value={true}> around the embedded component, and its
 * shell skips GameLayout so we don't nest two cockpit shells.
 */
export const EmbeddedContext = createContext<boolean>(false);

export const useEmbedded = (): boolean => useContext(EmbeddedContext);

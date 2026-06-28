/**
 * MFDContext — shared selection + alert state for both sidebar MFDs.
 *
 * Reducer actions:
 *   REGISTER_SCREEN — a screen announces its config + hydrated page
 *   SELECT_PAGE     — tap a softkey; enforces page uniqueness across
 *                     screens (the other screen falls back, never blanks)
 *   RAISE_ALERT     — badge channel pages that are not currently visible
 *   CLEAR_ALERT     — drop a page's badge
 *
 * The public surface is the frozen MFDContextValue; screen registration
 * is a B1-internal hook (useMFDScreenInternal) used only by MFDScreen.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
} from 'react';
import type {
  MFDAlertChannel,
  MFDContextValue,
  MFDPageId,
} from './mfdTypes';
import { pagesForChannel } from './mfdRegistry';
import { persistScreens } from './persistence';

// ── State ──────────────────────────────────────────────────────────────────

interface ScreenState {
  pageIds: MFDPageId[];
  defaultPageId: MFDPageId;
  activePageId: MFDPageId;
  previousPageId: MFDPageId | null;
}

interface MFDState {
  screens: Record<string, ScreenState>;
  alerts: Partial<Record<MFDPageId, boolean>>;
}

type MFDAction =
  | {
      type: 'REGISTER_SCREEN';
      screenId: string;
      pageIds: MFDPageId[];
      defaultPageId: MFDPageId;
      initialPageId: MFDPageId;
    }
  | { type: 'SELECT_PAGE'; screenId: string; pageId: MFDPageId }
  | { type: 'RAISE_ALERT'; pageIds: MFDPageId[] }
  | { type: 'CLEAR_ALERT'; pageId: MFDPageId };

const INITIAL_STATE: MFDState = { screens: {}, alerts: {} };

// ── Reducer ────────────────────────────────────────────────────────────────

const isVisibleAnywhere = (screens: MFDState['screens'], pageId: MFDPageId): boolean =>
  Object.values(screens).some((screen) => screen.activePageId === pageId);

/** Where a screen retreats to when its active page is claimed elsewhere. */
const fallbackFor = (screen: ScreenState, contested: MFDPageId): MFDPageId => {
  if (
    screen.previousPageId !== null &&
    screen.previousPageId !== contested &&
    screen.pageIds.includes(screen.previousPageId)
  ) {
    return screen.previousPageId;
  }
  if (screen.defaultPageId !== contested) return screen.defaultPageId;
  return screen.pageIds.find((id) => id !== contested) ?? contested;
};

const reducer = (state: MFDState, action: MFDAction): MFDState => {
  switch (action.type) {
    case 'REGISTER_SCREEN': {
      // Configs are frozen; a re-register (StrictMode double-mount, remount
      // on collapse) must not clobber a selection the pilot already made.
      if (state.screens[action.screenId] !== undefined) return state;
      const screens: MFDState['screens'] = {
        ...state.screens,
        [action.screenId]: {
          pageIds: action.pageIds,
          defaultPageId: action.defaultPageId,
          activePageId: action.initialPageId,
          previousPageId: null,
        },
      };
      // The hydrated page is now visible — its pending badge (if any) clears.
      const alerts = { ...state.alerts };
      delete alerts[action.initialPageId];
      return { screens, alerts };
    }

    case 'SELECT_PAGE': {
      const target = state.screens[action.screenId];
      if (target === undefined || !target.pageIds.includes(action.pageId)) return state;
      if (target.activePageId === action.pageId) {
        if (state.alerts[action.pageId] !== true) return state;
        const alerts = { ...state.alerts };
        delete alerts[action.pageId];
        return { ...state, alerts };
      }

      const screens: MFDState['screens'] = { ...state.screens };

      // Uniqueness swap: any other screen showing this page falls back.
      // previousPageId resets to null after a forced retreat so two
      // screens can never enter a steal loop over the same pair.
      for (const [screenId, screen] of Object.entries(state.screens)) {
        if (screenId !== action.screenId && screen.activePageId === action.pageId) {
          screens[screenId] = {
            ...screen,
            activePageId: fallbackFor(screen, action.pageId),
            previousPageId: null,
          };
        }
      }

      screens[action.screenId] = {
        ...target,
        activePageId: action.pageId,
        previousPageId: target.activePageId,
      };

      const alerts = { ...state.alerts };
      delete alerts[action.pageId];
      return { screens, alerts };
    }

    case 'RAISE_ALERT': {
      const pending = action.pageIds.filter(
        (pageId) => !isVisibleAnywhere(state.screens, pageId) && state.alerts[pageId] !== true,
      );
      if (pending.length === 0) return state;
      const alerts = { ...state.alerts };
      for (const pageId of pending) alerts[pageId] = true;
      return { ...state, alerts };
    }

    case 'CLEAR_ALERT': {
      if (state.alerts[action.pageId] !== true) return state;
      const alerts = { ...state.alerts };
      delete alerts[action.pageId];
      return { ...state, alerts };
    }

    default:
      return state;
  }
};

// ── Contexts ───────────────────────────────────────────────────────────────

interface MFDInternalValue {
  registerScreen: (
    screenId: string,
    pageIds: MFDPageId[],
    defaultPageId: MFDPageId,
    initialPageId: MFDPageId,
  ) => void;
}

const MFDValueContext = createContext<MFDContextValue | undefined>(undefined);
const MFDInternalContext = createContext<MFDInternalValue | undefined>(undefined);

// ── Provider ───────────────────────────────────────────────────────────────

export const MFDProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  // Persist every selection change (including hydration corrections —
  // the validate-and-rewrite leg of the persistence contract).
  useEffect(() => {
    const screenIds = Object.keys(state.screens);
    if (screenIds.length === 0) return;
    const selections: Record<string, string> = {};
    for (const screenId of screenIds) {
      selections[screenId] = state.screens[screenId].activePageId;
    }
    persistScreens(selections);
  }, [state.screens]);

  const activeFor = useCallback(
    (screenId: string): MFDPageId | undefined => state.screens[screenId]?.activePageId,
    [state.screens],
  );

  const selectPage = useCallback((screenId: string, pageId: MFDPageId) => {
    dispatch({ type: 'SELECT_PAGE', screenId, pageId });
  }, []);

  const hasAlert = useCallback(
    (pageId: MFDPageId): boolean => state.alerts[pageId] === true,
    [state.alerts],
  );

  const raiseAlert = useCallback((channel: MFDAlertChannel) => {
    dispatch({ type: 'RAISE_ALERT', pageIds: pagesForChannel(channel) });
  }, []);

  const clearAlert = useCallback((pageId: MFDPageId) => {
    dispatch({ type: 'CLEAR_ALERT', pageId });
  }, []);

  const value = useMemo<MFDContextValue>(
    () => ({ activeFor, selectPage, hasAlert, raiseAlert, clearAlert }),
    [activeFor, selectPage, hasAlert, raiseAlert, clearAlert],
  );

  const internal = useMemo<MFDInternalValue>(
    () => ({
      registerScreen: (screenId, pageIds, defaultPageId, initialPageId) => {
        dispatch({ type: 'REGISTER_SCREEN', screenId, pageIds, defaultPageId, initialPageId });
      },
    }),
    [],
  );

  return (
    <MFDInternalContext.Provider value={internal}>
      <MFDValueContext.Provider value={value}>{children}</MFDValueContext.Provider>
    </MFDInternalContext.Provider>
  );
};

// ── Hooks ──────────────────────────────────────────────────────────────────

export const useMFD = (): MFDContextValue => {
  const ctx = useContext(MFDValueContext);
  if (!ctx) {
    throw new Error('useMFD must be used within an MFDProvider');
  }
  return ctx;
};

/** B1-internal: screen registration. Only MFDScreen should use this. */
export const useMFDScreenInternal = (): MFDInternalValue => {
  const ctx = useContext(MFDInternalContext);
  if (!ctx) {
    throw new Error('useMFDScreenInternal must be used within an MFDProvider');
  }
  return ctx;
};

import React, { createContext, useContext, useState, useLayoutEffect, useCallback, ReactNode } from 'react';

/**
 * SettingsContext — client-side player preferences.
 *
 * Holds purely-local UI preferences (no server round-trip), persisted to
 * localStorage so a choice survives reloads and applies before first paint
 * on the next boot. Currently exposes the global UI scale; structured to
 * grow additional display / accessibility prefs over time.
 */

export interface SettingsContextType {
  /** Global UI scale as a fraction. 1.0 = 100% (the default no-op). */
  uiScale: number;
  /** Update the UI scale; persists to localStorage and applies live. */
  setUiScale: (n: number) => void;
}

const UI_SCALE_STORAGE_KEY = 'uiScale';
const DEFAULT_UI_SCALE = 1.0;
/** Allowed scale fractions; anything outside this set falls back to default. */
// Matches the Settings UI-scale slider range. A previously-persisted value from
// the old discrete select (which offered 1.25 / 1.5) is clamped into this range
// on read so the slider thumb and the % label can't disagree.
const MIN_UI_SCALE = 0.6;
const MAX_UI_SCALE = 1.2;

/**
 * Read + sanitize the persisted scale. Guards against absent / corrupt /
 * out-of-range values so a bad localStorage entry can never wedge the UI
 * at an unusable zoom (defaults to 100%).
 */
const readStoredUiScale = (): number => {
  try {
    const raw = localStorage.getItem(UI_SCALE_STORAGE_KEY);
    if (raw === null) return DEFAULT_UI_SCALE;
    const parsed = parseFloat(raw);
    if (!Number.isFinite(parsed)) return DEFAULT_UI_SCALE;
    // Clamp (don't reject) so an out-of-range persisted value — e.g. an old
    // 1.25/1.5 from the pre-slider select — snaps to the nearest slider bound
    // (1.2) rather than the thumb pinning at max while the label shows 150%.
    return Math.min(MAX_UI_SCALE, Math.max(MIN_UI_SCALE, parsed));
  } catch {
    // localStorage can throw (private mode / disabled) — fall back gracefully.
    return DEFAULT_UI_SCALE;
  }
};

const SettingsContext = createContext<SettingsContextType | undefined>(undefined);

export const SettingsProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  // Lazy initializer reads localStorage exactly once on boot.
  const [uiScale, setUiScaleState] = useState<number>(() => readStoredUiScale());

  const setUiScale = useCallback((n: number) => {
    const clamped = Number.isFinite(n)
      ? Math.min(MAX_UI_SCALE, Math.max(MIN_UI_SCALE, n))
      : DEFAULT_UI_SCALE;
    setUiScaleState(clamped);
    try {
      localStorage.setItem(UI_SCALE_STORAGE_KEY, String(clamped));
    } catch {
      // Persistence is best-effort; a write failure must not break the live apply.
    }
  }, []);

  // Apply the scale to the document root as a CSS custom property. The app
  // shell (#root) consumes it via `zoom: var(--ui-scale)` (see index.css).
  // Setting the var here keeps the single source of truth in React state.
  // useLayoutEffect (not useEffect) so a persisted non-100% scale is applied
  // BEFORE the browser's first paint — no one-frame 100% flash on reload.
  useLayoutEffect(() => {
    document.documentElement.style.setProperty('--ui-scale', String(uiScale));
  }, [uiScale]);

  const value: SettingsContextType = { uiScale, setUiScale };

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
};

// Hook for using the settings context
export const useSettings = (): SettingsContextType => {
  const context = useContext(SettingsContext);
  if (context === undefined) {
    throw new Error('useSettings must be used within a SettingsProvider');
  }
  return context;
};

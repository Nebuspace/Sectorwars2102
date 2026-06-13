/**
 * persistence — ONE localStorage key, versioned envelope:
 *   sw2102.mfd.v1 → { version: 1, screens: { 'sidebar-a': pageId, ... } }
 *
 * Reads are validated by the caller (MFDScreen) against the screen's
 * pageIds + available/hidden predicates; corrected values flow back in
 * through persistScreens. Write failures are silently swallowed —
 * persistence is a convenience, never a dependency.
 */

const STORAGE_KEY = 'sw2102.mfd.v1';
const ENVELOPE_VERSION = 1;

interface MFDPersistEnvelope {
  version: number;
  screens: Record<string, string>;
}

const readEnvelope = (): MFDPersistEnvelope | null => {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== 'object') return null;
    const envelope = parsed as { version?: unknown; screens?: unknown };
    if (envelope.version !== ENVELOPE_VERSION) return null;
    if (envelope.screens === null || typeof envelope.screens !== 'object') return null;
    const screens: Record<string, string> = {};
    for (const [key, value] of Object.entries(envelope.screens as Record<string, unknown>)) {
      if (typeof value === 'string') screens[key] = value;
    }
    return { version: ENVELOPE_VERSION, screens };
  } catch {
    return null;
  }
};

/** Raw persisted page id for a screen, or null. Caller validates. */
export const readPersistedPage = (screenId: string): string | null => {
  const envelope = readEnvelope();
  if (envelope === null) return null;
  return envelope.screens[screenId] ?? null;
};

/** Merge the given selections into the envelope and rewrite it. */
export const persistScreens = (selections: Record<string, string>): void => {
  try {
    const existing = readEnvelope();
    const envelope: MFDPersistEnvelope = {
      version: ENVELOPE_VERSION,
      screens: { ...(existing?.screens ?? {}), ...selections },
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(envelope));
  } catch {
    // Quota / privacy-mode failures are not the pilot's problem.
  }
};

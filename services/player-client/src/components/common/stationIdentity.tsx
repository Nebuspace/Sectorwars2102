import React from 'react';
import './station-identity.css';

/**
 * stationIdentity — shared visual identity for station classes.
 *
 * Mirrors the canonical per-class trade patterns defined server-side in
 * `services/gameserver/src/core/station_class_map.py` (CLASS_TRADE_PATTERNS).
 * Every class gets a canonical name, a one-line trade-pattern blurb, an
 * accent color, and a compact SVG mark. Eight marks cover twelve classes —
 * the four pure-trading classes share the arrows mark with Sol Hub.
 *
 * All consumers must render-guard: `getStationClassInfo` returns null for
 * unknown/missing classes and `StationClassBadge` renders nothing, so
 * stations without class data degrade silently.
 */

export type StationClassGroup =
  | 'mining'
  | 'agri'
  | 'industrial'
  | 'trading'
  | 'blackhole'
  | 'nova'
  | 'luxury'
  | 'tech';

export interface StationClassInfo {
  /** Canonical enum key, e.g. 'CLASS_4'. */
  key: string;
  /** Numeric class, 0–11. */
  classNumber: number;
  /** Canonical display name. */
  name: string;
  /** One-line trade-pattern blurb from canon. */
  blurb: string;
  /** Accent color (CSS var-friendly). */
  accent: string;
  /** Which of the 8 SVG marks this class uses. */
  group: StationClassGroup;
}

export const STATION_CLASSES: Record<string, StationClassInfo> = {
  CLASS_0: {
    key: 'CLASS_0',
    classNumber: 0,
    name: 'Sol Hub',
    blurb: 'trades special goods · sells colonists',
    accent: '#ffd700',
    group: 'trading',
  },
  CLASS_1: {
    key: 'CLASS_1',
    classNumber: 1,
    name: 'Mining Operation',
    blurb: 'buys ore · sells organics & equipment',
    accent: '#ff8c42',
    group: 'mining',
  },
  CLASS_2: {
    key: 'CLASS_2',
    classNumber: 2,
    name: 'Agricultural Center',
    blurb: 'buys organics · sells ore & equipment',
    accent: '#4ade80',
    group: 'agri',
  },
  CLASS_3: {
    key: 'CLASS_3',
    classNumber: 3,
    name: 'Industrial Hub',
    blurb: 'buys equipment · sells ore & organics',
    accent: '#b0bec5',
    group: 'industrial',
  },
  CLASS_4: {
    key: 'CLASS_4',
    classNumber: 4,
    name: 'Distribution Center',
    blurb: 'buys exotic tech · sells ore, organics, equipment & fuel',
    accent: '#00d9ff',
    group: 'trading',
  },
  CLASS_5: {
    key: 'CLASS_5',
    classNumber: 5,
    name: 'Collection Hub',
    blurb: 'buys ore, organics, equipment & fuel · sells luxury goods',
    accent: '#38bdf8',
    group: 'trading',
  },
  CLASS_6: {
    key: 'CLASS_6',
    classNumber: 6,
    name: 'Mixed Market',
    blurb: 'buys ore & organics · sells equipment & fuel',
    accent: '#2dd4bf',
    group: 'trading',
  },
  CLASS_7: {
    key: 'CLASS_7',
    classNumber: 7,
    name: 'Resource Exchange',
    blurb: 'buys equipment & fuel · sells ore & organics',
    accent: '#818cf8',
    group: 'trading',
  },
  CLASS_8: {
    key: 'CLASS_8',
    classNumber: 8,
    name: 'Black Hole Exchange',
    blurb: 'buys everything at premium',
    accent: '#8b5cf6',
    group: 'blackhole',
  },
  CLASS_9: {
    key: 'CLASS_9',
    classNumber: 9,
    name: 'Nova Market',
    blurb: 'sells everything at premium',
    accent: '#ff6b4a',
    group: 'nova',
  },
  CLASS_10: {
    key: 'CLASS_10',
    classNumber: 10,
    name: 'Luxury Market',
    blurb: 'buys gourmet food · sells luxury goods & exotic tech',
    accent: '#f472b6',
    group: 'luxury',
  },
  CLASS_11: {
    key: 'CLASS_11',
    classNumber: 11,
    name: 'Premium Tech Hub',
    blurb: 'buys exotic tech · sells advanced components',
    accent: '#00ffcc',
    group: 'tech',
  },
};

/**
 * Normalize a station class in any backend shape (4, '4', 'CLASS_4',
 * 'class_4') to its metadata. Returns null for anything unrecognized so
 * callers can render-guard.
 */
export function getStationClassInfo(
  value?: string | number | null
): StationClassInfo | null {
  if (value === undefined || value === null || value === '') return null;
  let classNumber: number;
  if (typeof value === 'number') {
    classNumber = value;
  } else {
    const match = /^(?:class[_\s-]?)?(\d{1,2})$/i.exec(value.trim());
    if (!match) return null;
    classNumber = parseInt(match[1], 10);
  }
  return STATION_CLASSES[`CLASS_${classNumber}`] ?? null;
}

/** Trader personality flavor chips (purely informational). */
export const TRADER_PERSONALITIES: Record<string, { label: string }> = {
  FEDERATION: { label: 'By the book' },
  BORDER: { label: 'Pragmatic' },
  FRONTIER: { label: 'Rugged' },
  LUXURY: { label: 'Exclusive' },
  BLACK_MARKET: { label: 'Discreet' },
};

export function getTraderPersonality(
  value?: string | null
): { key: string; label: string } | null {
  if (!value) return null;
  const key = value.trim().toUpperCase().replace(/[\s-]+/g, '_');
  const entry = TRADER_PERSONALITIES[key];
  return entry ? { key, label: entry.label } : null;
}

/* ---- The 8 class-group marks (compact 20×20 line art) ---- */

const MARKS: Record<StationClassGroup, React.ReactElement> = {
  // Pick over asteroid
  mining: (
    <>
      <path d="M3.5 14.5 L5.5 11 L9 9.8 L12.5 11.2 L13.5 14 L11 16.5 L6 16.8 Z" />
      <circle cx="7.2" cy="13.6" r="0.9" />
      <path d="M10.5 10.5 L16.5 3.5" />
      <path d="M13.5 2 Q17.5 3 18 7" />
    </>
  ),
  // Leaf inside a habitat dome
  agri: (
    <>
      <line x1="3" y1="15.5" x2="17" y2="15.5" />
      <path d="M4.5 15.5 A5.5 5.5 0 0 1 15.5 15.5" />
      <path
        d="M9.5 13.5 C9.5 10.5 11.3 9 13.5 9 C13.2 11.6 11.7 13.2 9.5 13.5 Z"
        fill="currentColor"
        fillOpacity="0.35"
      />
      <path d="M9.5 15.5 Q9.5 13.8 10.5 12.2" />
    </>
  ),
  // Gear
  industrial: (
    <>
      <circle cx="10" cy="10" r="4" />
      <circle cx="10" cy="10" r="1.3" fill="currentColor" />
      <line x1="10" y1="5.6" x2="10" y2="3.4" />
      <line x1="10" y1="14.4" x2="10" y2="16.6" />
      <line x1="5.6" y1="10" x2="3.4" y2="10" />
      <line x1="14.4" y1="10" x2="16.6" y2="10" />
      <line x1="13.1" y1="6.9" x2="14.7" y2="5.3" />
      <line x1="6.9" y1="6.9" x2="5.3" y2="5.3" />
      <line x1="6.9" y1="13.1" x2="5.3" y2="14.7" />
      <line x1="13.1" y1="13.1" x2="14.7" y2="14.7" />
    </>
  ),
  // Opposing exchange arrows
  trading: (
    <>
      <path d="M4 7 H15" />
      <path d="M12.5 4.2 L15.8 7 L12.5 9.8" />
      <path d="M16 13 H5" />
      <path d="M7.5 10.2 L4.2 13 L7.5 15.8" />
    </>
  ),
  // Accretion ring around the void
  blackhole: (
    <>
      <ellipse cx="10" cy="10" rx="7.5" ry="3" />
      <circle cx="10" cy="10" r="2.4" fill="currentColor" />
    </>
  ),
  // Stellar burst
  nova: (
    <>
      <circle cx="10" cy="10" r="2.4" fill="currentColor" />
      <line x1="10" y1="5.5" x2="10" y2="2" />
      <line x1="10" y1="14.5" x2="10" y2="18" />
      <line x1="5.5" y1="10" x2="2" y2="10" />
      <line x1="14.5" y1="10" x2="18" y2="10" />
      <line x1="13.2" y1="6.8" x2="15.7" y2="4.3" />
      <line x1="6.8" y1="6.8" x2="4.3" y2="4.3" />
      <line x1="6.8" y1="13.2" x2="4.3" y2="15.7" />
      <line x1="13.2" y1="13.2" x2="15.7" y2="15.7" />
    </>
  ),
  // Cut gem
  luxury: (
    <>
      <polygon points="6.5 3.5, 13.5 3.5, 17 8, 10 17, 3 8" />
      <line x1="3" y1="8" x2="17" y2="8" />
      <path d="M6.5 3.5 L8.2 8 L10 17 M13.5 3.5 L11.8 8 L10 17" />
    </>
  ),
  // Microchip
  tech: (
    <>
      <rect x="6" y="6" width="8" height="8" rx="1" />
      <rect x="8.6" y="8.6" width="2.8" height="2.8" />
      <line x1="8" y1="6" x2="8" y2="3" />
      <line x1="12" y1="6" x2="12" y2="3" />
      <line x1="8" y1="14" x2="8" y2="17" />
      <line x1="12" y1="14" x2="12" y2="17" />
      <line x1="6" y1="8" x2="3" y2="8" />
      <line x1="6" y1="12" x2="3" y2="12" />
      <line x1="14" y1="8" x2="17" y2="8" />
      <line x1="14" y1="12" x2="17" y2="12" />
    </>
  ),
};

interface StationClassMarkProps {
  group: StationClassGroup;
  /** Pixel size of the square mark (default 16). */
  size?: number;
  className?: string;
}

/** Compact inline SVG mark for a station class group (inherits currentColor). */
export const StationClassMark: React.FC<StationClassMarkProps> = ({
  group,
  size = 16,
  className,
}) => (
  <svg
    className={`station-class-mark mark-${group}${className ? ` ${className}` : ''}`}
    viewBox="0 0 20 20"
    width={size}
    height={size}
    aria-hidden="true"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.5"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    {MARKS[group]}
  </svg>
);

interface StationClassBadgeProps {
  /** Backend-shaped class value; badge renders nothing if unrecognized. */
  station_class?: string | number | null;
  size?: 'sm' | 'md';
}

/** Mark + canonical-name chip tinted with the class accent. */
export const StationClassBadge: React.FC<StationClassBadgeProps> = ({
  station_class,
  size = 'sm',
}) => {
  const info = getStationClassInfo(station_class);
  if (!info) return null;
  return (
    <span
      className={`station-class-badge size-${size}`}
      style={{ '--sc-accent': info.accent } as React.CSSProperties}
      title={`Class ${info.classNumber} — ${info.name}: ${info.blurb}`}
    >
      <StationClassMark group={info.group} size={size === 'md' ? 18 : 14} />
      <span className="station-class-badge-name">{info.name}</span>
    </span>
  );
};

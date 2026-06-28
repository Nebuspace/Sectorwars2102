/**
 * MineIcon — shared inline SVG for the mines metric in the cockpit HUD.
 *
 * Design: a naval mine — a solid filled sphere with 8 short radial spikes at
 * 45° intervals. `currentColor` throughout so it auto-tints with the palette
 * per context. At small HUD sizes (~0.8rem / 12-13px) the filled circle body
 * and rounded spike tips remain crisp.
 *
 * Mirrors the TurnsIcon pattern exactly:
 *   • named export · size prop (default '1em') · currentColor
 *   • role="img" + <title> · viewBox 0 0 16 16 · SVGProps passthrough
 *   • baked verticalAlign: -0.125em baseline correction
 *
 * Usage:
 *   import { MineIcon } from '../../components/icons/MineIcon';
 *
 *   <MineIcon />              // inherits font-size via 1em default
 *   <MineIcon size="0.8rem" aria-hidden="true" /> {count} mines
 */

import React from 'react';

export interface MineIconProps extends React.SVGProps<SVGSVGElement> {
  /** Icon dimensions; defaults to '1em' so it scales with surrounding text. */
  size?: number | string;
}

/**
 * Inline SVG mine icon — naval-mine sphere with 8 radial spikes.
 *
 * viewBox 0 0 16 16, center at (8, 8).
 * Body: filled circle, r=3.
 * Spikes: 8 lines at 45° intervals, from circle edge (r=3) to spike tip (r=6.5).
 * All strokes strokeLinecap="round" for clean rendering at small sizes.
 */
export const MineIcon: React.FC<MineIconProps> = ({
  size = '1em',
  className,
  style,
  ...rest
}) => (
  <svg
    role="img"
    aria-label="Mine"
    {...rest}
    viewBox="0 0 16 16"
    width={size}
    height={size}
    fill="none"
    className={className}
    style={{ verticalAlign: '-0.125em', ...style }}
  >
    <title>Mine</title>

    {/* Naval-mine sphere body — solid filled circle. */}
    <circle cx="8" cy="8" r="3" fill="currentColor" />

    {/*
      8 radial spikes at 45° intervals.
      Each line runs from the circle edge (r=3) outward to r=6.5.
      The inner endpoint is hidden behind the filled circle body.
      strokeLinecap="round" gives rounded spike tips for crispness at tiny sizes.

      Angle    inner (r=3)        outer (r=6.5)
      ──────   ─────────────────  ─────────────────
        0°     (11.00,  8.00)     (14.50,  8.00)   right
       45°     (10.12, 10.12)     (12.60, 12.60)   bottom-right
       90°     ( 8.00, 11.00)     ( 8.00, 14.50)   bottom
      135°     ( 5.88, 10.12)     ( 3.40, 12.60)   bottom-left
      180°     ( 5.00,  8.00)     ( 1.50,  8.00)   left
      225°     ( 5.88,  5.88)     ( 3.40,  3.40)   top-left
      270°     ( 8.00,  5.00)     ( 8.00,  1.50)   top
      315°     (10.12,  5.88)     (12.60,  3.40)   top-right
    */}
    <line x1="11.00" y1="8.00"  x2="14.50" y2="8.00"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    <line x1="10.12" y1="10.12" x2="12.60" y2="12.60" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    <line x1="8.00"  y1="11.00" x2="8.00"  y2="14.50" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    <line x1="5.88"  y1="10.12" x2="3.40"  y2="12.60" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    <line x1="5.00"  y1="8.00"  x2="1.50"  y2="8.00"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    <line x1="5.88"  y1="5.88"  x2="3.40"  y2="3.40"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    <line x1="8.00"  y1="5.00"  x2="8.00"  y2="1.50"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    <line x1="10.12" y1="5.88"  x2="12.60" y2="3.40"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

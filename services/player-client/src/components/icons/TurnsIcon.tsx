/**
 * TurnsIcon — shared inline SVG for the turns metric throughout the cockpit.
 *
 * Design: a clockwise near-full ring (330° arc, 30° gap at 12 o'clock) with a
 * filled arrowhead at the leading edge (11:30 position), suggesting cycle /
 * regen cadence (the +N/hr turns-per-hour rhythm). CRT line style — stroke +
 * fill use `currentColor` so the icon auto-tints with the palette per context
 * (HUD green, amber warning, etc.).
 *
 * Swap the path/polygon here to restyle the turns glyph globally across the
 * cockpit (mirrors CREDITS_SYMBOL / formatCredits pattern in
 * src/utils/formatters.ts).
 */

import React from 'react';

export interface TurnsIconProps extends React.SVGProps<SVGSVGElement> {
  /** Icon dimensions; defaults to '1em' so it scales with surrounding text. */
  size?: number | string;
}

/**
 * Inline SVG turns icon — a clockwise near-full ring with arrowhead.
 *
 * Self-labeled as "Turns" (aria-label + <title>); renders inline with a baked-in
 * vertical-align: -0.125em baseline correction.
 *
 * Usage:
 *   import { TurnsIcon } from '../../components/icons/TurnsIcon';
 *
 *   // Inline beside a count — icon is already labeled "Turns"; no redundant word:
 *   <TurnsIcon /> {count}
 *
 *   // Sized / tinted:
 *   <TurnsIcon size={14} className="hud-green" />
 *   <TurnsIcon />  // inherits font-size via 1em default
 *
 *   // Purely decorative (beside descriptive text that already conveys meaning):
 *   <TurnsIcon aria-hidden="true" /> {count} turns
 */
export const TurnsIcon: React.FC<TurnsIconProps> = ({
  size = '1em',
  className,
  style,
  ...rest
}) => (
  <svg
    role="img"
    aria-label="Turns"
    {...rest}
    viewBox="0 0 16 16"
    width={size}
    height={size}
    fill="none"
    className={className}
    style={{ verticalAlign: '-0.125em', ...style }}
  >
    <title>Turns</title>

    {/*
     * Arc: clockwise 330° from (9.42, 2.69) [θ=285°, just right of 12 o'clock]
     * to (6.58, 2.69) [θ=255°, just left of 12 o'clock].
     * large-arc=1 selects the 330° path; sweep=1 = clockwise on screen.
     * Center (8,8), radius 5.5, stroke-width 1.5.
     */}
    <path
      d="M 9.42 2.69 A 5.5 5.5 0 1 1 6.58 2.69"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      fill="none"
    />

    {/*
     * Arrowhead: filled triangle at the arc's leading edge (11:30 position).
     * Tip at (6.58, 2.69); base wings ±1.2px perpendicular to the clockwise
     * tangent (0.966, −0.259) at θ=255° — points rightward toward 12 o'clock.
     */}
    <polygon
      points="6.58,2.69 4.34,2.05 4.96,4.37"
      fill="currentColor"
    />
  </svg>
);

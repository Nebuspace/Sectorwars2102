/**
 * TurnsIcon — shared inline SVG for the turns metric throughout the cockpit.
 *
 * Design: fast-forward mark (two right-pointing solid triangles), suggesting
 * advance / spend a turn. Both triangles use `currentColor` so the icon
 * auto-tints with the palette per context (HUD green, amber warning, etc.).
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
 * Inline SVG turns icon — fast-forward (two right triangles).
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

    {/* Fast-forward turns mark: two solid right-pointing triangles (▶▶), 1-unit gap between them. */}
    <polygon points="1.5,4 1.5,12 7.5,8" fill="currentColor" />
    <polygon points="8.5,4 8.5,12 14.5,8" fill="currentColor" />
  </svg>
);

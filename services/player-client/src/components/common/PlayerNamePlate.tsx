import React from 'react';
import './player-name-plate.css';

/**
 * PlayerNamePlate — pinned public medal identity (LEG-33 / medals.md).
 * Renders name + optional pinned medal icon + earned-count badge.
 * Pin write is gameserver-gated (no player settings endpoint yet) — callers
 * pass pinnedMedalId/count when known.
 */

export interface PlayerNamePlateProps {
  name: string;
  pinnedMedalId?: string | null;
  pinnedMedalIcon?: string | null;
  pinnedMedalName?: string | null;
  medalCount?: number | null;
  /** Compact for roster / contact rows */
  size?: 'sm' | 'md';
  className?: string;
  onClick?: () => void;
}

const PlayerNamePlate: React.FC<PlayerNamePlateProps> = ({
  name,
  pinnedMedalId,
  pinnedMedalIcon,
  pinnedMedalName,
  medalCount,
  size = 'md',
  className = '',
  onClick,
}) => {
  const showPin = !!(pinnedMedalId || pinnedMedalIcon);
  const Tag = onClick ? 'button' : 'div';

  return (
    <Tag
      type={onClick ? 'button' : undefined}
      className={`player-name-plate size-${size} ${className}`.trim()}
      data-testid="player-name-plate"
      data-pinned-medal={pinnedMedalId || undefined}
      onClick={onClick}
      title={pinnedMedalName || undefined}
    >
      <span className="pnp-name">{name}</span>
      {showPin && (
        <span
          className="pnp-medal"
          data-testid="player-name-plate-medal"
          aria-label={pinnedMedalName || pinnedMedalId || 'Pinned medal'}
        >
          {pinnedMedalIcon || '🏅'}
        </span>
      )}
      {typeof medalCount === 'number' && medalCount > 0 && (
        <span className="pnp-count" data-testid="player-name-plate-count">
          {medalCount}
        </span>
      )}
    </Tag>
  );
};

export default PlayerNamePlate;

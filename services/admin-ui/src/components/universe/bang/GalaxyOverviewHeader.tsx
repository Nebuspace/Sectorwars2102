import React from 'react';
import { useTranslation } from 'react-i18next';

import './galaxy-overview-header.css';

export interface GalaxyOverviewSummary {
  /** Display name of the active galaxy. */
  name: string;
  /** Galaxy primary key (used for the wipe-by-id endpoint). */
  id: string;
  /** Bang generator version that produced this galaxy (e.g. "1.3.0"). */
  bangVersion?: string | null;
  /** Master seed (bang_seed column). */
  bangSeed?: number | string | null;
  /** Approx galaxy diameter in sectors / units. */
  diameter?: number | null;
  /** Fraction of unreachable / island sectors (0..1) — rendered as %. */
  islandPercent?: number | null;
  /** Total cluster count. */
  clusterCount?: number | null;
}

interface GalaxyOverviewHeaderProps {
  /** The active galaxy summary, or null if no galaxy has been generated. */
  galaxy: GalaxyOverviewSummary | null;
  /** Current gameserver-side BANG_VERSION env, for drift detection. */
  serverBangVersion?: string | null;
  /** Optional "Wipe galaxy" callback. If absent the button is hidden. */
  onWipe?: () => void;
}

/**
 * Read-only header strip summarising the active galaxy's bang provenance.
 *
 * Rendered above the form on the BangGalaxyPage. Designed to be drop-in
 * compatible with the project's existing card-grid styling so it sits
 * naturally above the existing `UniverseManager` content if the page
 * embeds it side-by-side.
 */
const GalaxyOverviewHeader: React.FC<GalaxyOverviewHeaderProps> = ({
  galaxy,
  serverBangVersion = null,
  onWipe,
}) => {
  const { t } = useTranslation('admin');

  if (!galaxy) {
    return (
      <div className="galaxy-overview-header empty">
        <h3>{t('bang.overview.title')}</h3>
        <p className="overview-empty">{t('bang.overview.noGalaxy')}</p>
      </div>
    );
  }

  const versionMismatch =
    galaxy.bangVersion &&
    serverBangVersion &&
    galaxy.bangVersion !== serverBangVersion;

  const islandPct =
    typeof galaxy.islandPercent === 'number'
      ? `${(galaxy.islandPercent * 100).toFixed(1)}%`
      : '—';

  return (
    <div className="galaxy-overview-header">
      <div className="overview-title-row">
        <h3>{t('bang.overview.title')}</h3>
        {onWipe && (
          <button
            type="button"
            className="overview-wipe-btn"
            onClick={onWipe}
          >
            {t('bang.wipe.title')}
          </button>
        )}
      </div>

      <div className="overview-grid">
        <div className="overview-stat">
          <span className="overview-stat-label">
            {t('bang.overview.bangVersion')}
          </span>
          <span className="overview-stat-value">
            {galaxy.bangVersion ?? '—'}
          </span>
        </div>
        <div className="overview-stat">
          <span className="overview-stat-label">
            {t('bang.overview.bangSeed')}
          </span>
          <span className="overview-stat-value">
            {galaxy.bangSeed ?? '—'}
          </span>
        </div>
        <div className="overview-stat">
          <span className="overview-stat-label">
            {t('bang.overview.diameter')}
          </span>
          <span className="overview-stat-value">
            {galaxy.diameter ?? '—'}
          </span>
        </div>
        <div className="overview-stat">
          <span className="overview-stat-label">
            {t('bang.overview.islandPercent')}
          </span>
          <span className="overview-stat-value">{islandPct}</span>
        </div>
        <div className="overview-stat">
          <span className="overview-stat-label">
            {t('bang.overview.clusterCount')}
          </span>
          <span className="overview-stat-value">
            {galaxy.clusterCount ?? '—'}
          </span>
        </div>
      </div>

      {versionMismatch && (
        <p className="overview-version-mismatch">
          {t('bang.overview.versionMismatchWarning', {
            galaxyVersion: galaxy.bangVersion,
            serverVersion: serverBangVersion,
          })}
        </p>
      )}
    </div>
  );
};

export default GalaxyOverviewHeader;

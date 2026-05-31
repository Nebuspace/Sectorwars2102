import React, { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAdmin } from '../../../contexts/AdminContext';
import { useAuth } from '../../../contexts/AuthContext';
import { previewBangConfig } from '../../../services/bangGalaxyApi';
import { i18nKeyForBangCode } from './errorCodeMap';
import {
  BangConfig,
  BangPreviewResponse,
  BangRegionType,
  BangValidatorStrictness,
  DEFAULT_BANG_CONFIG,
} from './types';
import './galaxy-generation-form.css';

interface GalaxyGenerationFormProps {
  /** Called with the new job id once a commit succeeds. */
  onJobStarted?: (jobId: string) => void;
  /** Externally-supplied config (e.g. "Regenerate with same seed"). */
  initialConfig?: BangConfig;
}

type FormConfig = BangConfig & { galaxy_name?: string; raw_config_json?: string };

const REGION_TYPES: BangRegionType[] = [
  'player_owned',
  'terran_space',
  'central_nexus',
];

const VALIDATOR_OPTIONS: BangValidatorStrictness[] = [
  'lenient',
  'standard',
  'strict',
];

/**
 * Three-tier galaxy generation form: Common / Advanced / Expert.
 *
 * Common params: seed, region type, total sectors.
 * Advanced: zone %s, density %s.
 * Expert: max warps, one-way warps, validator strictness, raw JSON.
 *
 * "Preview" calls POST /admin/galaxy/preview and displays the stats card
 * inline below the form. "Commit" calls POST /admin/galaxy/jobs via
 * AdminContext.bangGalaxy and bubbles the new job id up to the parent
 * page so it can mount the live log panel.
 */
const GalaxyGenerationForm: React.FC<GalaxyGenerationFormProps> = ({
  onJobStarted,
  initialConfig,
}) => {
  const { t } = useTranslation('admin');
  const { token } = useAuth();
  const { bangGalaxy } = useAdmin();

  const [config, setConfig] = useState<FormConfig>(() => ({
    ...DEFAULT_BANG_CONFIG,
    ...(initialConfig ?? {}),
  }));
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showExpert, setShowExpert] = useState(false);
  const [preview, setPreview] = useState<BangPreviewResponse | null>(null);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isCommitting, setIsCommitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // Update helper — typed so each call has to use a real field name.
  const update = useCallback(
    <K extends keyof FormConfig>(key: K, value: FormConfig[K]) => {
      setConfig((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  // The zone-percent sum is shown as a soft warning, not a hard block.
  const zoneSum = useMemo(() => {
    const f = config.federation_percent ?? 0;
    const b = config.border_percent ?? 0;
    const fr = config.frontier_percent ?? 0;
    return f + b + fr;
  }, [config.federation_percent, config.border_percent, config.frontier_percent]);

  /**
   * Build the final BangConfig sent to the backend.
   * If raw_config_json is provided we trust it as the canonical payload
   * (Expert tier override). Otherwise we strip the form-only fields.
   */
  const buildPayload = useCallback((): {
    config: BangConfig;
    galaxy_name?: string;
  } => {
    const { galaxy_name, raw_config_json, ...rest } = config;
    if (raw_config_json && raw_config_json.trim().length > 0) {
      const parsed = JSON.parse(raw_config_json) as BangConfig;
      return { config: parsed, galaxy_name };
    }
    return { config: rest as BangConfig, galaxy_name };
  }, [config]);

  const handlePreview = async () => {
    setError(null);
    setPreviewError(null);
    setIsPreviewing(true);
    try {
      const { config: payload } = buildPayload();
      const result = await previewBangConfig(payload, token);
      setPreview(result);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setPreviewError(t('bang.form.preview.previewFailed', { error: message }));
    } finally {
      setIsPreviewing(false);
    }
  };

  const handleCommit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsCommitting(true);
    try {
      const payload = buildPayload();
      const job = await bangGalaxy(payload.config, payload.galaxy_name);
      if (job && onJobStarted) onJobStarted(job.id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(t('bang.form.errors.submitFailed', { error: message }));
    } finally {
      setIsCommitting(false);
    }
  };

  const regenerateSeed = () => {
    // 53-bit safe random — enough entropy for bang's deterministic seed input.
    const s = Math.floor(Math.random() * Number.MAX_SAFE_INTEGER);
    update('seed', s);
  };

  const copySeed = async () => {
    try {
      await navigator.clipboard.writeText(String(config.seed));
    } catch {
      /* clipboard unavailable; silently ignore */
    }
  };

  const resetForm = () => {
    setConfig({ ...DEFAULT_BANG_CONFIG });
    setPreview(null);
    setError(null);
    setPreviewError(null);
  };

  return (
    <form className="galaxy-generation-form" onSubmit={handleCommit}>
      <h2 className="form-title">{t('bang.form.title')}</h2>

      {/* --- Common tier --- */}
      <fieldset className="form-tier form-tier-common">
        <legend>{t('bang.form.common.section')}</legend>

        <div className="form-row">
          <label className="form-field">
            <span className="form-label">{t('bang.form.common.seed')}</span>
            <div className="form-seed-row">
              <input
                type="number"
                value={config.seed}
                min={0}
                onChange={(e) => update('seed', Number(e.target.value))}
                required
              />
              <button type="button" onClick={copySeed} className="form-mini-btn">
                {t('bang.form.common.copySeed')}
              </button>
              <button
                type="button"
                onClick={regenerateSeed}
                className="form-mini-btn"
              >
                {t('bang.form.common.regenerateSeed')}
              </button>
            </div>
            <small className="form-hint">{t('bang.form.common.seedHelp')}</small>
          </label>

          <label className="form-field">
            <span className="form-label">{t('bang.form.common.regionType')}</span>
            <select
              value={config.region_type}
              onChange={(e) => update('region_type', e.target.value as BangRegionType)}
            >
              {REGION_TYPES.map((r) => (
                <option key={r} value={r}>
                  {t(
                    `bang.form.common.region${r
                      .split('_')
                      .map((p) => p[0].toUpperCase() + p.slice(1))
                      .join('')}`,
                  )}
                </option>
              ))}
            </select>
          </label>

          <label className="form-field">
            <span className="form-label">{t('bang.form.common.sectors')}</span>
            <input
              type="number"
              value={config.sectors}
              min={20}
              max={20000}
              onChange={(e) => update('sectors', Number(e.target.value))}
              required
            />
          </label>

          <label className="form-field">
            <span className="form-label">{t('bang.form.common.galaxyName')}</span>
            <input
              type="text"
              value={config.galaxy_name ?? ''}
              placeholder={t('bang.form.common.galaxyNamePlaceholder')}
              onChange={(e) => update('galaxy_name', e.target.value)}
              maxLength={100}
            />
          </label>
        </div>
      </fieldset>

      {/* --- Advanced tier --- */}
      <fieldset className="form-tier form-tier-advanced">
        <legend>
          <button
            type="button"
            className="tier-toggle"
            onClick={() => setShowAdvanced((v) => !v)}
            aria-expanded={showAdvanced}
          >
            {showAdvanced
              ? t('bang.form.advanced.collapse')
              : t('bang.form.advanced.expand')}
          </button>
        </legend>

        {showAdvanced && (
          <>
            <div className="form-row">
              <label className="form-field">
                <span className="form-label">
                  {t('bang.form.advanced.federationPercent')}
                </span>
                <input
                  type="number"
                  step="0.1"
                  min={0}
                  max={100}
                  value={config.federation_percent ?? ''}
                  onChange={(e) =>
                    update(
                      'federation_percent',
                      e.target.value === '' ? undefined : Number(e.target.value),
                    )
                  }
                />
              </label>
              <label className="form-field">
                <span className="form-label">
                  {t('bang.form.advanced.borderPercent')}
                </span>
                <input
                  type="number"
                  step="0.1"
                  min={0}
                  max={100}
                  value={config.border_percent ?? ''}
                  onChange={(e) =>
                    update(
                      'border_percent',
                      e.target.value === '' ? undefined : Number(e.target.value),
                    )
                  }
                />
              </label>
              <label className="form-field">
                <span className="form-label">
                  {t('bang.form.advanced.frontierPercent')}
                </span>
                <input
                  type="number"
                  step="0.1"
                  min={0}
                  max={100}
                  value={config.frontier_percent ?? ''}
                  onChange={(e) =>
                    update(
                      'frontier_percent',
                      e.target.value === '' ? undefined : Number(e.target.value),
                    )
                  }
                />
              </label>
            </div>
            {Math.abs(zoneSum - 100) > 0.01 && zoneSum > 0 && (
              <p className="form-hint form-hint-warning">
                {t('bang.form.advanced.zoneSumWarning', { total: zoneSum })}
              </p>
            )}

            <div className="form-row">
              <label className="form-field">
                <span className="form-label">
                  {t('bang.form.advanced.portPercent')}
                </span>
                <input
                  type="number"
                  step="0.1"
                  min={0}
                  max={100}
                  value={config.port_percent ?? ''}
                  onChange={(e) =>
                    update(
                      'port_percent',
                      e.target.value === '' ? undefined : Number(e.target.value),
                    )
                  }
                />
              </label>
              <label className="form-field">
                <span className="form-label">
                  {t('bang.form.advanced.planetPercent')}
                </span>
                <input
                  type="number"
                  step="0.1"
                  min={0}
                  max={100}
                  value={config.planet_percent ?? ''}
                  onChange={(e) =>
                    update(
                      'planet_percent',
                      e.target.value === '' ? undefined : Number(e.target.value),
                    )
                  }
                />
              </label>
              <label className="form-field">
                <span className="form-label">
                  {t('bang.form.advanced.nebulaPercent')}
                </span>
                <input
                  type="number"
                  step="0.1"
                  min={0}
                  max={100}
                  value={config.nebula_percent ?? ''}
                  onChange={(e) =>
                    update(
                      'nebula_percent',
                      e.target.value === '' ? undefined : Number(e.target.value),
                    )
                  }
                />
              </label>
            </div>
          </>
        )}
      </fieldset>

      {/* --- Expert tier --- */}
      <fieldset className="form-tier form-tier-expert">
        <legend>
          <button
            type="button"
            className="tier-toggle"
            onClick={() => setShowExpert((v) => !v)}
            aria-expanded={showExpert}
          >
            {showExpert
              ? t('bang.form.expert.section')
              : t('bang.form.expert.toggle')}
          </button>
        </legend>

        {showExpert && (
          <>
            <div className="form-row">
              <label className="form-field">
                <span className="form-label">{t('bang.form.expert.maxWarps')}</span>
                <input
                  type="number"
                  min={1}
                  max={12}
                  value={config.max_warps ?? ''}
                  onChange={(e) =>
                    update(
                      'max_warps',
                      e.target.value === '' ? undefined : Number(e.target.value),
                    )
                  }
                />
              </label>
              <label className="form-field">
                <span className="form-label">
                  {t('bang.form.expert.oneWayWarpPercent')}
                </span>
                <input
                  type="number"
                  step="0.1"
                  min={0}
                  max={100}
                  value={config.one_way_warp_percent ?? ''}
                  onChange={(e) =>
                    update(
                      'one_way_warp_percent',
                      e.target.value === '' ? undefined : Number(e.target.value),
                    )
                  }
                />
              </label>
              <label className="form-field">
                <span className="form-label">
                  {t('bang.form.expert.validatorStrictness')}
                </span>
                <select
                  value={config.validator_strictness ?? ''}
                  onChange={(e) =>
                    update(
                      'validator_strictness',
                      (e.target.value || undefined) as BangValidatorStrictness | undefined,
                    )
                  }
                >
                  <option value="">—</option>
                  {VALIDATOR_OPTIONS.map((v) => (
                    <option key={v} value={v}>
                      {t(`bang.form.expert.strictness${v[0].toUpperCase()}${v.slice(1)}`)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="form-field form-field-checkbox">
                <input
                  type="checkbox"
                  checked={config.stardock_enabled ?? false}
                  onChange={(e) => update('stardock_enabled', e.target.checked)}
                />
                <span className="form-label">{t('bang.form.expert.stardockEnabled')}</span>
              </label>
            </div>

            <label className="form-field form-field-full">
              <span className="form-label">{t('bang.form.expert.rawConfigJson')}</span>
              <textarea
                rows={5}
                placeholder='{"seed":42,"sectors":1000,"region_type":"player_owned"}'
                value={config.raw_config_json ?? ''}
                onChange={(e) => update('raw_config_json', e.target.value)}
              />
              <small className="form-hint">
                {t('bang.form.expert.rawConfigJsonHelp')}
              </small>
            </label>
          </>
        )}
      </fieldset>

      {/* --- Actions --- */}
      <div className="form-actions">
        <button
          type="button"
          className="form-btn form-btn-secondary"
          onClick={handlePreview}
          disabled={isPreviewing || isCommitting}
        >
          {isPreviewing
            ? t('bang.form.actions.previewing')
            : t('bang.form.actions.preview')}
        </button>
        <button
          type="button"
          className="form-btn form-btn-tertiary"
          onClick={resetForm}
          disabled={isPreviewing || isCommitting}
        >
          {t('bang.form.actions.reset')}
        </button>
        <button
          type="submit"
          className="form-btn form-btn-primary"
          disabled={isPreviewing || isCommitting}
        >
          {isCommitting
            ? t('bang.form.actions.committing')
            : t('bang.form.actions.commit')}
        </button>
      </div>

      {error && <p className="form-error">{error}</p>}

      {/* --- Preview stats card --- */}
      <div className="form-preview-card">
        <h3>{t('bang.form.preview.title')}</h3>
        {previewError && <p className="form-error">{previewError}</p>}
        {!preview && !previewError && (
          <p className="form-hint">{t('bang.form.preview.noPreview')}</p>
        )}
        {preview && (
          <>
            <div className="preview-stats-grid">
              <div className="preview-stat">
                <span className="preview-stat-label">
                  {t('bang.form.preview.totalSectors')}
                </span>
                <span className="preview-stat-value">
                  {String(preview.stats.total_sectors ?? '—')}
                </span>
              </div>
              <div className="preview-stat">
                <span className="preview-stat-label">
                  {t('bang.form.preview.diameter')}
                </span>
                <span className="preview-stat-value">
                  {String(preview.stats.diameter ?? '—')}
                </span>
              </div>
              <div className="preview-stat">
                <span className="preview-stat-label">
                  {t('bang.form.preview.clusterCount')}
                </span>
                <span className="preview-stat-value">
                  {String(preview.stats.cluster_count ?? '—')}
                </span>
              </div>
              <div className="preview-stat">
                <span className="preview-stat-label">
                  {t('bang.form.preview.validatorPasses')}
                </span>
                <span className="preview-stat-value">
                  {String(preview.stats.validator_pass_count ?? '—')}
                </span>
              </div>
              <div className="preview-stat">
                <span className="preview-stat-label">
                  {t('bang.form.preview.islandPercent')}
                </span>
                <span className="preview-stat-value">
                  {typeof preview.stats.island_percent === 'number'
                    ? `${(preview.stats.island_percent * 100).toFixed(1)}%`
                    : '—'}
                </span>
              </div>
            </div>

            {preview.stats.max_warps_histogram && (
              <div className="preview-block">
                <h4>{t('bang.form.preview.maxWarpsHistogram')}</h4>
                <ul className="preview-list">
                  {Object.entries(preview.stats.max_warps_histogram).map(
                    ([bucket, count]) => (
                      <li key={bucket}>
                        <span>{bucket}</span>
                        <span>{count}</span>
                      </li>
                    ),
                  )}
                </ul>
              </div>
            )}

            {preview.stats.formation_counts && (
              <div className="preview-block">
                <h4>{t('bang.form.preview.formationCounts')}</h4>
                <ul className="preview-list">
                  {Object.entries(preview.stats.formation_counts).map(
                    ([formation, count]) => (
                      <li key={formation}>
                        <span>{formation}</span>
                        <span>{count}</span>
                      </li>
                    ),
                  )}
                </ul>
              </div>
            )}

            {preview.warnings.length > 0 && (
              <div className="preview-block">
                <h4>{t('bang.form.preview.warningsByCategory')}</h4>
                <ul className="preview-warning-list">
                  {preview.warnings.map((w, idx) => (
                    <li key={`${w.code}-${idx}`} className="preview-warning-item">
                      <span className="preview-warning-code">{w.code}</span>
                      <span className="preview-warning-message">
                        {t(i18nKeyForBangCode(w.code), {
                          code: w.code,
                          message: w.message,
                          defaultValue: w.message,
                        })}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </form>
  );
};

export default GalaxyGenerationForm;

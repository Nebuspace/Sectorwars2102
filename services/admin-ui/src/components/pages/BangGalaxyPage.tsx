import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAdmin } from '../../contexts/AdminContext';
import GalaxyGenerationForm from '../universe/bang/GalaxyGenerationForm';
import GalaxyGenerationHistory from '../universe/bang/GalaxyGenerationHistory';
import GalaxyOverviewHeader, {
  GalaxyOverviewSummary,
} from '../universe/bang/GalaxyOverviewHeader';
import GenerationLogPanel from '../universe/bang/GenerationLogPanel';
import WipeGalaxyConfirmDialog from '../universe/bang/WipeGalaxyConfirmDialog';
import AddRegionDialog from '../universe/bang/AddRegionDialog';
import { useAuth } from '../../contexts/AuthContext';
import { addPlayerOwnedRegion } from '../../services/bangGalaxyApi';
import type { BangConfig } from '../universe/bang/types';
import './bang-galaxy-page.css';

type TabKey = 'form' | 'history';

/**
 * BangGalaxyPage — single-stop UI for the sw2102-bang admin workflow.
 *
 * Tab layout (form / history) mirrors the audit's recommendation of a
 * "sub-route under /universe/bang" without committing us to two distinct
 * routes (so that "Regenerate with same seed" and "View log" can stay on
 * one page and share state). The live log panel docks below the form
 * once a job is started.
 */
const BangGalaxyPage: React.FC = () => {
  const { t } = useTranslation('admin');
  const { galaxyState, loadGalaxyInfo, wipeGalaxy } = useAdmin();
  const [activeTab, setActiveTab] = useState<TabKey>('form');
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [prefillConfig, setPrefillConfig] = useState<BangConfig | undefined>(
    undefined,
  );
  const [wipeOpen, setWipeOpen] = useState(false);
  const [wipeBusy, setWipeBusy] = useState(false);
  const [wipeError, setWipeError] = useState<string | null>(null);
  const [addRegionOpen, setAddRegionOpen] = useState(false);
  const [addRegionBusy, setAddRegionBusy] = useState(false);
  const [addRegionError, setAddRegionError] = useState<string | null>(null);
  const { token } = useAuth();

  useEffect(() => {
    loadGalaxyInfo();
  }, [loadGalaxyInfo]);

  const overview: GalaxyOverviewSummary | null = galaxyState
    ? {
        id: galaxyState.id,
        name: galaxyState.name,
        // `bang_*` fields are part of Phase 1B's galaxy audit columns;
        // the gameserver may or may not surface them on this endpoint yet.
        bangVersion:
          (galaxyState as unknown as { bang_version?: string | null }).bang_version ?? null,
        bangSeed:
          (galaxyState as unknown as { bang_seed?: number | string | null }).bang_seed ?? null,
        diameter: null,
        islandPercent: null,
        clusterCount: null,
      }
    : null;

  // BANG_VERSION is injected at build time on the gameserver; the admin UI
  // can't read its env directly. Phase 4 may expose it on `/admin/health`.
  const serverBangVersion: string | null = null;

  const handleRegenerate = (config: BangConfig) => {
    setPrefillConfig(config);
    setActiveTab('form');
  };

  const handleWipe = async (confirmName: string) => {
    if (!overview) return;
    setWipeBusy(true);
    setWipeError(null);
    try {
      await wipeGalaxy(overview.id, confirmName);
      setWipeOpen(false);
      await loadGalaxyInfo();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setWipeError(t('bang.wipe.failure', { error: message }));
    } finally {
      setWipeBusy(false);
    }
  };

  return (
    <div className="bang-galaxy-page">
      <header className="bang-page-header">
        <h1>{t('bang.page.title')}</h1>
        <p className="bang-page-subtitle">{t('bang.page.subtitle')}</p>
      </header>

      <GalaxyOverviewHeader
        galaxy={overview}
        serverBangVersion={serverBangVersion}
        onWipe={overview ? () => setWipeOpen(true) : undefined}
        onAddRegion={overview ? () => setAddRegionOpen(true) : undefined}
      />

      <nav className="bang-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={activeTab === 'form'}
          className={`bang-tab ${activeTab === 'form' ? 'active' : ''}`}
          onClick={() => setActiveTab('form')}
        >
          {t('bang.page.tabForm')}
        </button>
        <button
          role="tab"
          aria-selected={activeTab === 'history'}
          className={`bang-tab ${activeTab === 'history' ? 'active' : ''}`}
          onClick={() => setActiveTab('history')}
        >
          {t('bang.page.tabHistory')}
        </button>
      </nav>

      {activeTab === 'form' && (
        <div className="bang-tab-panel">
          <GalaxyGenerationForm
            initialConfig={prefillConfig}
            onJobStarted={(jobId) => setActiveJobId(jobId)}
          />
          {activeJobId && (
            <GenerationLogPanel jobId={activeJobId} />
          )}
        </div>
      )}

      {activeTab === 'history' && (
        <div className="bang-tab-panel">
          <GalaxyGenerationHistory
            onRegenerate={handleRegenerate}
            onSelectJob={(jobId) => {
              setActiveJobId(jobId);
              setActiveTab('form');
            }}
          />
        </div>
      )}

      {addRegionOpen && overview && (
        <AddRegionDialog
          onCancel={() => {
            setAddRegionOpen(false);
            setAddRegionError(null);
          }}
          onConfirm={async (seed, sectors) => {
            setAddRegionBusy(true);
            setAddRegionError(null);
            try {
              const job = await addPlayerOwnedRegion(
                overview.id,
                { config: { seed, sectors, region_type: 'player_owned' } },
                token,
              );
              setActiveJobId(job.id);
              setAddRegionOpen(false);
              // Refresh galaxy info to surface the new region.
              setTimeout(() => { void loadGalaxyInfo(); }, 500);
            } catch (err) {
              const message = err instanceof Error ? err.message : String(err);
              setAddRegionError(message);
            } finally {
              setAddRegionBusy(false);
            }
          }}
          busy={addRegionBusy}
          error={addRegionError}
        />
      )}

      {wipeOpen && overview && (
        <WipeGalaxyConfirmDialog
          galaxyName={overview.name}
          onCancel={() => {
            setWipeOpen(false);
            setWipeError(null);
          }}
          onConfirm={handleWipe}
          busy={wipeBusy}
          error={wipeError}
        />
      )}
    </div>
  );
};

export default BangGalaxyPage;

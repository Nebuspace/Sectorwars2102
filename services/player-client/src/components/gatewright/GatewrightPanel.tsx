import React, { useState, useEffect, useCallback, useMemo } from 'react';
import apiClient from '../../services/apiClient';
import { useGame } from '../../contexts/GameContext';
import './gatewright-panel.css';

/**
 * GatewrightPanel — the Gatewright Guild console.
 *
 * Self-contained overlay component for the warp-gate construction ritual
 * (FEATURES/galaxy/warp-gates.md construction pipeline):
 *   Phase 1 — deploy beacon at the source sector (Warp Jumper required)
 *   Phase 2 — transit window (beacon invulnerable 48h)
 *   Phase 3 — anchor focus at the destination; the Jumper hull is consumed
 *             at the end of a 1-hour harmonization.
 *
 * Contract: props = { onClose? } only. Self-fetches via the shared api
 * client; reads useGame() for playerState/currentShip only.
 */

// --- Contract types (warp-gates + quantum APIs) ---

type GatePhase =
  | 'BEACON_DEPLOYED'
  | 'HARMONIZING'
  | 'ACTIVE'
  | 'EXPIRED'
  | 'CANCELLED';

interface GateProject {
  beacon_id: string;
  gate_id?: string | null;
  phase: GatePhase | string;
  source_sector_id: number;
  source_name?: string | null;
  destination_sector_id: number;
  destination_name?: string | null;
  invulnerable_until?: string | null;
  harmonization_completes_at?: string | null;
  created_at?: string | null;
}

interface SectorGateEntry {
  gate_id: string;
  destination_sector_id: number;
  destination_name?: string | null;
  owner_name?: string | null;
  is_public?: boolean;
  toll?: number;
}

interface SectorBeaconEntry {
  beacon_id?: string;
  owner_name?: string | null;
  destination_sector_id?: number;
  destination_name?: string | null;
  invulnerable_until?: string | null;
}

interface QuantumStatus {
  quantum_shards: number;
  quantum_crystals: number;
  quantum_charges: number;
  jump_cooldown_until?: string | null;
  scan_cooldown_until?: string | null;
  can_jump: boolean;
  is_warp_jumper: boolean;
  sensor_level: number;
}

interface GatewrightPanelProps {
  onClose?: () => void;
}

// --- Cost manifests (canon: warp-gates.md Phase 1 / Phase 3 tables) ---

type ManifestSource = 'turns' | 'credits' | 'cargo' | 'quantum_crystals';

interface ManifestItem {
  label: string;
  need: number;
  source: ManifestSource;
  cargoKey?: string;
}

const PHASE1_MANIFEST: ManifestItem[] = [
  { label: 'TURNS', need: 50, source: 'turns' },
  { label: 'CREDITS', need: 10000, source: 'credits' },
  { label: 'ORE', need: 1000, source: 'cargo', cargoKey: 'ore' },
  { label: 'EQUIPMENT', need: 500, source: 'cargo', cargoKey: 'equipment' },
  { label: 'QUANTUM CRYSTAL', need: 1, source: 'quantum_crystals' },
];

const PHASE3_MANIFEST: ManifestItem[] = [
  { label: 'TURNS', need: 100, source: 'turns' },
  { label: 'CREDITS', need: 10000, source: 'credits' },
  { label: 'ORE', need: 1000, source: 'cargo', cargoKey: 'ore' },
  { label: 'EQUIPMENT', need: 500, source: 'cargo', cargoKey: 'equipment' },
  { label: 'LUMEN CRYSTALS', need: 30, source: 'cargo', cargoKey: 'lumen_crystals' },
];

// --- Phase ribbon (5 ritual steps) ---

const RIBBON_STEPS = [
  'BEACON DEPLOYED',
  'TRANSIT',
  'ANCHORING',
  'HARMONIZING',
  'GATE ACTIVE',
] as const;

// --- Helpers ---

// Pull the backend's verbatim detail string out of an axios error
const errDetail = (e: unknown, fallback: string): string => {
  if (e && typeof e === 'object') {
    const resp = (e as { response?: { data?: unknown } }).response;
    const data = resp?.data;
    if (data && typeof data === 'object') {
      const detail = (data as Record<string, unknown>).detail;
      if (typeof detail === 'string' && detail) return detail;
    }
  }
  return fallback;
};

// Countdown formatting against a ticking clock
const fmtCountdown = (
  iso: string,
  nowMs: number
): { text: string; expired: boolean; urgent: boolean } => {
  const target = Date.parse(iso);
  if (Number.isNaN(target)) return { text: '—', expired: false, urgent: false };
  let diff = Math.floor((target - nowMs) / 1000);
  if (diff <= 0) return { text: 'EXPIRED', expired: true, urgent: true };
  const urgent = diff < 3600;
  const days = Math.floor(diff / 86400);
  diff %= 86400;
  const hours = Math.floor(diff / 3600);
  diff %= 3600;
  const minutes = Math.floor(diff / 60);
  const seconds = diff % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  const text =
    days > 0
      ? `${days}d ${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`
      : `${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`;
  return { text, expired: false, urgent };
};

const fmtSector = (id: number, name?: string | null): string =>
  name ? `${name} (${id})` : `SECTOR ${id}`;

const fmtNumber = (n: number): string => n.toLocaleString();

const GatewrightPanel: React.FC<GatewrightPanelProps> = ({ onClose }) => {
  const { playerState, currentShip, refreshPlayerState } = useGame();

  // --- Server data ---
  const [projects, setProjects] = useState<GateProject[] | null>(null);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [projectsError, setProjectsError] = useState<string | null>(null);

  const [sectorGates, setSectorGates] = useState<SectorGateEntry[] | null>(null);
  const [sectorBeacons, setSectorBeacons] = useState<SectorBeaconEntry[] | null>(null);
  const [sectorLoading, setSectorLoading] = useState(false);
  const [sectorError, setSectorError] = useState<string | null>(null);

  const [quantumStatus, setQuantumStatus] = useState<QuantumStatus | null>(null);

  // --- Deploy flow (D2) ---
  // Raw input string so the field can be transiently empty/partial; parsed and
  // clamped on blur/commit. Empty = no valid destination (DEPLOY disabled).
  const [destInput, setDestInput] = useState<string>('1');
  const [destTouched, setDestTouched] = useState(false);
  const [deployArmed, setDeployArmed] = useState(false);
  const [deployBusy, setDeployBusy] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);
  const [deployNotice, setDeployNotice] = useState<string | null>(null);

  // --- Anchor flow (D3): two-step arm -> commit, per beacon ---
  const [armedAnchorId, setArmedAnchorId] = useState<string | null>(null);
  const [anchorBusyId, setAnchorBusyId] = useState<string | null>(null);

  // --- Cancel flow (D1): inline confirm, per project ---
  const [armedCancelId, setArmedCancelId] = useState<string | null>(null);
  const [cancelBusyId, setCancelBusyId] = useState<string | null>(null);

  // Per-project action errors (verbatim backend detail strings)
  const [projectErrors, setProjectErrors] = useState<Record<string, string>>({});

  // Ticking clock for countdowns
  const [nowMs, setNowMs] = useState<number>(Date.now());

  const currentSectorId = playerState?.current_sector_id ?? null;

  // Quantum status is authoritative for "am I piloting a Warp Jumper";
  // ship type is the synchronous fallback while status loads.
  const shipTypeIsJumper = useMemo(() => {
    const t = (currentShip?.type || '').toUpperCase().replace(/[\s-]+/g, '_');
    return t === 'WARP_JUMPER';
  }, [currentShip]);
  const isWarpJumper = quantumStatus?.is_warp_jumper ?? shipTypeIsJumper;
  // Both deploy and anchor require the Jumper free in open space — docked OR
  // landed counts as grounded (server enforces, mirror it in the UI).
  const isGrounded = (playerState?.is_docked ?? false) || (playerState?.is_landed ?? false);

  // Cargo contents from the active ship — cargo JSONB is
  // {used, capacity, contents:{...}} but older payloads may put goods at the
  // top level; feature-detect both (same pattern as ConstructionVenue).
  const cargoContents = useMemo((): Record<string, number> => {
    const cargo: unknown = currentShip?.cargo;
    if (!cargo || typeof cargo !== 'object') return {};
    const c = cargo as Record<string, unknown>;
    const source =
      c.contents && typeof c.contents === 'object'
        ? (c.contents as Record<string, unknown>)
        : c;
    const out: Record<string, number> = {};
    for (const [key, value] of Object.entries(source)) {
      const k = key.toLowerCase();
      if (typeof value === 'number' && k !== 'capacity' && k !== 'used') {
        out[k] = value;
      }
    }
    return out;
  }, [currentShip]);

  // Resolve a manifest line's "have" value; null = unknown to the client
  const manifestHave = useCallback(
    (item: ManifestItem): number | null => {
      switch (item.source) {
        case 'turns':
          return playerState ? playerState.turns : null;
        case 'credits':
          return playerState ? playerState.credits : null;
        case 'cargo':
          return currentShip ? cargoContents[item.cargoKey ?? ''] ?? 0 : null;
        case 'quantum_crystals':
          return quantumStatus ? quantumStatus.quantum_crystals : null;
        default:
          return null;
      }
    },
    [playerState, currentShip, cargoContents, quantumStatus]
  );

  // --- Fetching ---

  const fetchProjects = useCallback(async () => {
    setProjectsLoading(true);
    try {
      const response = await apiClient.get('/api/v1/warp-gates/mine');
      const data: unknown = response.data;
      const list =
        data && typeof data === 'object' && Array.isArray((data as Record<string, unknown>).projects)
          ? ((data as { projects: GateProject[] }).projects)
          : [];
      setProjects(list);
      setProjectsError(null);
    } catch (e) {
      console.error('Gatewright projects error:', e);
      setProjectsError(errDetail(e, 'Guild registry unreachable. Try again.'));
    } finally {
      setProjectsLoading(false);
    }
  }, []);

  const fetchSector = useCallback(async (sectorId: number) => {
    setSectorLoading(true);
    try {
      const response = await apiClient.get(`/api/v1/warp-gates/sector/${sectorId}`);
      const data: unknown = response.data;
      const body = data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
      setSectorGates(Array.isArray(body.gates) ? (body.gates as SectorGateEntry[]) : []);
      setSectorBeacons(Array.isArray(body.beacons) ? (body.beacons as SectorBeaconEntry[]) : []);
      setSectorError(null);
    } catch (e) {
      console.error('Gatewright sector scan error:', e);
      setSectorError(errDetail(e, 'Sector gate scan failed. Try again.'));
    } finally {
      setSectorLoading(false);
    }
  }, []);

  const fetchQuantumStatus = useCallback(async () => {
    try {
      const response = await apiClient.get('/api/v1/quantum/status');
      setQuantumStatus(response.data as QuantumStatus);
    } catch (e) {
      // Non-fatal: crystal counts render as unknown
      console.error('Gatewright quantum status error:', e);
      setQuantumStatus(null);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    // Also refresh shared player state — deploy/anchor/cancel consume turns,
    // credits, cargo and may consume the Jumper, so the cockpit must update.
    const jobs: Promise<void>[] = [fetchProjects(), fetchQuantumStatus(), refreshPlayerState()];
    if (currentSectorId !== null) jobs.push(fetchSector(currentSectorId));
    await Promise.allSettled(jobs);
  }, [fetchProjects, fetchQuantumStatus, fetchSector, currentSectorId, refreshPlayerState]);

  // On open
  useEffect(() => {
    fetchProjects();
    fetchQuantumStatus();
  }, [fetchProjects, fetchQuantumStatus]);

  // Sector gates: on open + whenever the player's sector changes (D4)
  useEffect(() => {
    if (currentSectorId !== null) fetchSector(currentSectorId);
  }, [currentSectorId, fetchSector]);

  // Reading /mine lazily advances harmonization + expiry server-side —
  // poll while the console is open so timer completions surface.
  useEffect(() => {
    const interval = setInterval(() => {
      fetchProjects();
    }, 30000);
    return () => clearInterval(interval);
  }, [fetchProjects]);

  // 1s tick while anything is counting down
  const hasCountdowns = useMemo(() => {
    const projectTimers = (projects ?? []).some(
      (p) =>
        (p.phase === 'BEACON_DEPLOYED' && p.invulnerable_until) ||
        (p.phase === 'HARMONIZING' && p.harmonization_completes_at)
    );
    const beaconTimers = (sectorBeacons ?? []).some((b) => b.invulnerable_until);
    return projectTimers || beaconTimers;
  }, [projects, sectorBeacons]);

  useEffect(() => {
    if (!hasCountdowns) return;
    const interval = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [hasCountdowns]);

  // Sensible default destination once we know where we are
  useEffect(() => {
    if (!destTouched && currentSectorId !== null) {
      setDestInput(String(currentSectorId + 50));
    }
  }, [currentSectorId, destTouched]);

  // Parsed/clamped destination; null while the field is empty or invalid.
  const destSector = useMemo((): number | null => {
    const parsed = parseInt(destInput, 10);
    if (Number.isNaN(parsed)) return null;
    return Math.max(1, parsed);
  }, [destInput]);

  // --- Actions ---

  const handleDeploy = async () => {
    if (deployBusy || destSector === null) return;
    setDeployBusy(true);
    setDeployError(null);
    setDeployNotice(null);
    try {
      await apiClient.post('/api/v1/warp-gates/deploy-beacon', {
        destination_sector_id: destSector,
      });
      setDeployArmed(false);
      setDeployNotice(
        `Beacon deployed — fly the Jumper to sector ${destSector} and anchor the focus before the invulnerability window (see the project countdown) closes.`
      );
      await refreshAll();
    } catch (e) {
      setDeployError(errDetail(e, 'Beacon deploy rejected.'));
    } finally {
      setDeployBusy(false);
    }
  };

  const handleAnchor = async (beaconId: string) => {
    if (anchorBusyId) return;
    setAnchorBusyId(beaconId);
    setProjectErrors((prev) => {
      const next = { ...prev };
      delete next[beaconId];
      return next;
    });
    try {
      await apiClient.post('/api/v1/warp-gates/anchor-focus', { beacon_id: beaconId });
      setArmedAnchorId(null);
      await refreshAll();
    } catch (e) {
      setProjectErrors((prev) => ({
        ...prev,
        [beaconId]: errDetail(e, 'Anchor focus rejected.'),
      }));
    } finally {
      setAnchorBusyId(null);
    }
  };

  const handleCancel = async (project: GateProject) => {
    const cancelId = project.gate_id || project.beacon_id;
    if (cancelBusyId) return;
    setCancelBusyId(project.beacon_id);
    setProjectErrors((prev) => {
      const next = { ...prev };
      delete next[project.beacon_id];
      return next;
    });
    try {
      await apiClient.post(`/api/v1/warp-gates/${cancelId}/cancel`);
      setArmedCancelId(null);
      await refreshAll();
    } catch (e) {
      setProjectErrors((prev) => ({
        ...prev,
        [project.beacon_id]: errDetail(e, 'Cancel rejected.'),
      }));
    } finally {
      setCancelBusyId(null);
    }
  };

  // --- Derived render data ---

  const ribbonIndex = (project: GateProject, anchorReady: boolean): number => {
    switch (project.phase) {
      case 'BEACON_DEPLOYED':
        return anchorReady ? 2 : 1;
      case 'HARMONIZING':
        return 3;
      case 'ACTIVE':
        return 4;
      default:
        return -1; // EXPIRED / CANCELLED
    }
  };

  const liveProjects = useMemo(
    () => (projects ?? []).filter((p) => p.phase !== 'CANCELLED'),
    [projects]
  );

  const adjustDest = (delta: number) => {
    setDestTouched(true);
    setDeployArmed(false);
    setDestInput((prev) => {
      const base = parseInt(prev, 10);
      const next = (Number.isNaN(base) ? 1 : base) + delta;
      return String(Math.max(1, next));
    });
  };

  // Normalize the raw field to a clamped integer (or empty) when the pilot
  // tabs away — keeps transient partial input from being clobbered mid-type.
  const commitDestBlur = () => {
    if (destInput.trim() === '') return;
    const parsed = parseInt(destInput, 10);
    setDestInput(Number.isNaN(parsed) ? '' : String(Math.max(1, parsed)));
  };

  // --- Render helpers ---

  const renderManifest = (manifest: ManifestItem[], keyPrefix: string) => (
    <ul className="gw-manifest">
      {manifest.map((item) => {
        const have = manifestHave(item);
        const status = have === null ? 'unknown' : have >= item.need ? 'ok' : 'short';
        return (
          <li key={`${keyPrefix}-${item.label}`} className={`gw-manifest-row ${status}`}>
            <span className="gw-manifest-check" aria-hidden="true">
              {status === 'ok' ? '◆' : status === 'short' ? '◇' : '·'}
            </span>
            <span className="gw-manifest-label">{item.label}</span>
            <span className="gw-manifest-need">{fmtNumber(item.need)}</span>
            <span className="gw-manifest-have">
              {have === null ? 'HAVE —' : `HAVE ${fmtNumber(have)}`}
            </span>
          </li>
        );
      })}
    </ul>
  );

  const renderRibbon = (project: GateProject, anchorReady: boolean) => {
    const current = ribbonIndex(project, anchorReady);
    const dead = current === -1;
    return (
      <ol className={`gw-ribbon ${dead ? 'dead' : ''}`}>
        {RIBBON_STEPS.map((step, idx) => {
          const state = dead
            ? 'inert'
            : idx < current
              ? 'done'
              : idx === current
                ? 'current'
                : 'pending';
          return (
            <li key={step} className={`gw-ribbon-step ${state}`}>
              <span className="gw-ribbon-node" aria-hidden="true" />
              <span className="gw-ribbon-label">{step}</span>
            </li>
          );
        })}
      </ol>
    );
  };

  const renderProjectCard = (project: GateProject) => {
    const atDestination = currentSectorId === project.destination_sector_id;
    const anchorReady =
      project.phase === 'BEACON_DEPLOYED' && atDestination && isWarpJumper && !isGrounded;
    const actionError = projectErrors[project.beacon_id];
    const cancelArmed = armedCancelId === project.beacon_id;
    const anchorArmed = armedAnchorId === project.beacon_id;
    const busy = anchorBusyId === project.beacon_id || cancelBusyId === project.beacon_id;

    const invuln = project.invulnerable_until
      ? fmtCountdown(project.invulnerable_until, nowMs)
      : null;
    const fusion = project.harmonization_completes_at
      ? fmtCountdown(project.harmonization_completes_at, nowMs)
      : null;

    return (
      <article
        key={project.beacon_id}
        className={`gw-project-card phase-${String(project.phase).toLowerCase()}`}
      >
        <header className="gw-project-route">
          <span className="gw-route-end">{fmtSector(project.source_sector_id, project.source_name)}</span>
          <span className="gw-route-arrow" aria-hidden="true">⟶</span>
          <span className="gw-route-end">
            {fmtSector(project.destination_sector_id, project.destination_name)}
          </span>
          {project.phase === 'EXPIRED' && <span className="gw-phase-flag expired">EXPIRED</span>}
        </header>

        {renderRibbon(project, anchorReady)}

        {project.phase === 'BEACON_DEPLOYED' && invuln && !invuln.expired && (
          <div className="gw-project-line">
            <span className="gw-line-label">BEACON SHIELD</span>
            <span className={`gw-line-value ${invuln.urgent ? 'urgent' : ''}`}>
              INVULNERABLE {invuln.text}
            </span>
          </div>
        )}
        {project.phase === 'BEACON_DEPLOYED' && invuln?.expired && (
          <div className="gw-project-line">
            <span className="gw-line-label">BEACON SHIELD</span>
            <span className="gw-line-value urgent">WINDOW CLOSED — beacon expiring; the gate-in-progress is abandoned</span>
          </div>
        )}

        {project.phase === 'BEACON_DEPLOYED' && !anchorReady && (
          <p className="gw-project-hint">
            {isWarpJumper
              ? atDestination
                ? isGrounded
                  ? 'Undock or lift off to anchor the focus in open space.'
                  : 'Awaiting anchor conditions.'
                : `Fly the Jumper to ${fmtSector(project.destination_sector_id, project.destination_name)} to anchor the focus.`
              : 'A Warp Jumper must carry the focus to the destination.'}
          </p>
        )}

        {anchorReady && (
          <div className="gw-anchor-block">
            <div className="gw-anchor-title">PHASE 3 — ANCHOR FOCUS</div>
            {renderManifest(PHASE3_MANIFEST, project.beacon_id)}
            <p className="gw-anchor-warning">
              THE JUMPER WILL BE CONSUMED when harmonization completes — cancellable until then. No
              insurance. The pilot ejects at the destination.
            </p>
            {!anchorArmed ? (
              <button
                type="button"
                className="gw-btn anchor"
                disabled={busy}
                onClick={() => {
                  setArmedAnchorId(project.beacon_id);
                  setArmedCancelId(null);
                }}
              >
                ANCHOR FOCUS
              </button>
            ) : (
              <div className="gw-confirm-row">
                <button
                  type="button"
                  className="gw-btn commit"
                  disabled={busy}
                  onClick={() => handleAnchor(project.beacon_id)}
                >
                  {anchorBusyId === project.beacon_id ? 'ANCHORING…' : 'COMMIT — BEGIN HARMONIZATION'}
                </button>
                <button
                  type="button"
                  className="gw-btn ghost"
                  disabled={busy}
                  onClick={() => setArmedAnchorId(null)}
                >
                  STAND DOWN
                </button>
              </div>
            )}
          </div>
        )}

        {project.phase === 'HARMONIZING' && (
          <div className="gw-harmonizing-block">
            <div className="gw-fusion-label">HULL FUSION IN PROGRESS — the Jumper becomes the gate</div>
            <div className={`gw-fusion-countdown ${fusion?.expired ? 'complete' : ''}`}>
              {fusion ? (fusion.expired ? 'FUSION COMPLETE — SYNCHRONIZING…' : fusion.text) : '—'}
            </div>
            {!cancelArmed ? (
              <button
                type="button"
                className="gw-btn ghost"
                disabled={busy}
                onClick={() => {
                  setArmedCancelId(project.beacon_id);
                  setArmedAnchorId(null);
                }}
              >
                CANCEL ANCHOR
              </button>
            ) : (
              <div className="gw-confirm-block">
                <p className="gw-cancel-note">
                  Cancel returns the Jumper intact and refunds the Phase 3 materials. The Phase 1
                  Quantum Crystal is already fused and does not refund.
                </p>
                <div className="gw-confirm-row">
                  <button
                    type="button"
                    className="gw-btn danger"
                    disabled={busy}
                    onClick={() => handleCancel(project)}
                  >
                    {cancelBusyId === project.beacon_id ? 'CANCELLING…' : 'CONFIRM CANCEL'}
                  </button>
                  <button
                    type="button"
                    className="gw-btn ghost"
                    disabled={busy}
                    onClick={() => setArmedCancelId(null)}
                  >
                    KEEP HARMONIZING
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {project.phase === 'ACTIVE' && (
          <div className="gw-ceremony">
            <div className="gw-ceremony-title">GATE ACTIVE — 0-TURN CORRIDOR OPEN</div>
            <div className="gw-ceremony-route">
              {fmtSector(project.source_sector_id, project.source_name)}
              <span aria-hidden="true"> ⟶ </span>
              {fmtSector(project.destination_sector_id, project.destination_name)}
            </div>
          </div>
        )}

        {project.phase === 'EXPIRED' && (
          <p className="gw-project-hint">
            The beacon window closed before the focus was anchored. The gate-in-progress is
            abandoned; Phase 1 materials are sunk cost.
          </p>
        )}

        {actionError && <div className="gw-validation-strip">{actionError}</div>}
      </article>
    );
  };

  return (
    <div className="gatewright-panel">
      <header className="gw-hud-header">
        <span className="gw-hud-title">GATEWRIGHT GUILD CONSOLE</span>
        <span className="gw-hud-sub">ARTIFICIAL CORRIDOR AUTHORITY</span>
        {onClose && (
          <button type="button" className="gw-close" onClick={onClose} aria-label="Close Gatewright console">
            ✕
          </button>
        )}
      </header>

      <div className="gw-body">
        <p className="gw-intro">
          Three rites build a gate: deploy the beacon, carry the focus, anchor it with the
          Jumper's own hull. The Guild keeps the ledger.
        </p>

        {/* ============ D2 — DEPLOY BEACON ============ */}
        <section className="gw-section">
          <h3 className="gw-section-title">PHASE 1 — DEPLOY BEACON</h3>
          {!isWarpJumper ? (
            <p className="gw-requirement">
              A WARP JUMPER must be under your command to deploy a gate beacon.
            </p>
          ) : isGrounded ? (
            <p className="gw-requirement">Undock or lift off to deploy a beacon in open space.</p>
          ) : (
            <div className="gw-deploy">
              <div className="gw-dest-row">
                <span className="gw-dest-label">DESTINATION SECTOR</span>
                <div className="gw-dest-controls">
                  <button type="button" className="gw-step-btn" onClick={() => adjustDest(-10)}>
                    −10
                  </button>
                  <button type="button" className="gw-step-btn" onClick={() => adjustDest(-1)}>
                    −1
                  </button>
                  <input
                    type="number"
                    min={1}
                    className="gw-dest-input"
                    value={destInput}
                    onChange={(e) => {
                      setDestTouched(true);
                      setDeployArmed(false);
                      // Keep the raw string; clamp/parse happens on blur/commit.
                      setDestInput(e.target.value);
                    }}
                    onBlur={commitDestBlur}
                  />
                  <button type="button" className="gw-step-btn" onClick={() => adjustDest(1)}>
                    +1
                  </button>
                  <button type="button" className="gw-step-btn" onClick={() => adjustDest(10)}>
                    +10
                  </button>
                </div>
              </div>

              <div className="gw-anchor-title">PHASE 1 MANIFEST</div>
              {renderManifest(PHASE1_MANIFEST, 'deploy')}

              {deployError && <div className="gw-validation-strip">{deployError}</div>}
              {deployNotice && <div className="gw-notice-strip">{deployNotice}</div>}

              {!deployArmed ? (
                <button
                  type="button"
                  className="gw-btn deploy"
                  disabled={deployBusy || destSector === null}
                  onClick={() => {
                    setDeployError(null);
                    setDeployNotice(null);
                    setDeployArmed(true);
                  }}
                >
                  DEPLOY BEACON
                </button>
              ) : (
                <div className="gw-confirm-block">
                  <p className="gw-cancel-note">
                    Deploy toward sector {destSector ?? '—'}? The manifest above is spent on commit; the
                    Quantum Crystal fuses into the beacon and cannot be recovered.
                  </p>
                  <div className="gw-confirm-row">
                    <button
                      type="button"
                      className="gw-btn commit"
                      disabled={deployBusy || destSector === null}
                      onClick={handleDeploy}
                    >
                      {deployBusy ? 'DEPLOYING…' : 'CONFIRM DEPLOY'}
                    </button>
                    <button
                      type="button"
                      className="gw-btn ghost"
                      disabled={deployBusy}
                      onClick={() => setDeployArmed(false)}
                    >
                      STAND DOWN
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </section>

        {/* ============ D1 — MY PROJECTS ============ */}
        <section className="gw-section">
          <h3 className="gw-section-title">GUILD LEDGER — MY PROJECTS</h3>
          {projectsLoading && projects === null ? (
            <p className="gw-state">Consulting the Guild registry…</p>
          ) : projects === null ? (
            /* First load failed outright — nothing to show but the error */
            <div className="gw-validation-strip">{projectsError}</div>
          ) : (
            <>
              {/* A failed background poll keeps the last good list visible */}
              {projectsError && <div className="gw-validation-strip">{projectsError}</div>}
              {liveProjects.length === 0 ? (
                <p className="gw-state">No gate projects on the ledger. Deploy a beacon to begin.</p>
              ) : (
                <div className="gw-project-list">{liveProjects.map(renderProjectCard)}</div>
              )}
            </>
          )}
        </section>

        {/* ============ D4 — GATES IN THIS SECTOR ============ */}
        <section className="gw-section">
          <h3 className="gw-section-title">
            CORRIDORS IN {currentSectorId !== null ? `SECTOR ${currentSectorId}` : 'THIS SECTOR'}
          </h3>
          {sectorLoading && sectorGates === null ? (
            <p className="gw-state">Scanning local space…</p>
          ) : sectorError ? (
            <div className="gw-validation-strip">{sectorError}</div>
          ) : (
            <>
              {(sectorGates ?? []).length === 0 && (sectorBeacons ?? []).length === 0 && (
                <p className="gw-state">No artificial corridors or beacons in this sector.</p>
              )}
              {(sectorGates ?? []).length > 0 && (
                <ul className="gw-gate-list">
                  {(sectorGates ?? []).map((gate) => (
                    <li key={gate.gate_id} className="gw-gate-row">
                      <span className="gw-gate-route">
                        ⟶ {fmtSector(gate.destination_sector_id, gate.destination_name)}
                      </span>
                      <span className="gw-gate-owner">
                        {gate.owner_name ? `GATEWRIGHT: ${gate.owner_name}` : 'GATEWRIGHT UNKNOWN'}
                      </span>
                      <span className="gw-badge turns">0 TURNS</span>
                      {gate.is_public && <span className="gw-badge public">PUBLIC</span>}
                    </li>
                  ))}
                </ul>
              )}
              {(sectorBeacons ?? []).length > 0 && (
                <ul className="gw-gate-list beacons">
                  {(sectorBeacons ?? []).map((beacon, idx) => {
                    const shield = beacon.invulnerable_until
                      ? fmtCountdown(beacon.invulnerable_until, nowMs)
                      : null;
                    return (
                      <li key={beacon.beacon_id ?? `beacon-${idx}`} className="gw-gate-row beacon">
                        <span className="gw-gate-route">
                          ◈ BEACON
                          {typeof beacon.destination_sector_id === 'number'
                            ? ` ⟶ ${fmtSector(beacon.destination_sector_id, beacon.destination_name)}`
                            : ''}
                        </span>
                        <span className="gw-gate-owner">
                          {beacon.owner_name ? `GATEWRIGHT: ${beacon.owner_name}` : 'GATE UNDER CONSTRUCTION'}
                        </span>
                        {shield && !shield.expired && (
                          <span className="gw-badge shield">SHIELDED {shield.text}</span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
};

export default GatewrightPanel;

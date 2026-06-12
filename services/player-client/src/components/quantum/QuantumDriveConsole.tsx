import React, { useEffect, useRef, useState } from 'react';
import { useGame, type QuantumBearing, type QuantumScanResult, type QuantumJumpResult } from '../../contexts/GameContext';
import apiClient from '../../services/apiClient';
import QuantumBearingViewport, { type MinimapSector } from './QuantumBearingViewport';
import './quantum-drive.css';

/**
 * QuantumDriveConsole — the QUANTUM DRIVE mode of the NAV monitor, shown only
 * while piloting a Warp Jumper. Lets the pilot aim a bearing (yaw/pitch),
 * pick a range band, fire a hyperspace echo scan, and commit a blind quantum
 * jump. All countdowns tick client-side from the *_until ISO timestamps the
 * quantum API returns — no network polling.
 */

interface QuantumDriveConsoleProps {
  /** Opens the Gatewright project panel (overlay owned by GameDashboard) */
  onOpenGatewright?: () => void;
}

type RangeBandId = QuantumBearing['range_band'];

/** GET /api/v1/quantum/minimap — astrogation chart (ADR-0030 Phase 1).
 *  Anonymous positions only per ADR-0031 (no ids/type/activity/presence).
 *  complete_radius_spacings = how far (in spacings) the chart is complete
 *  (25.0 unless the server's 400-sector cap truncated it). */
interface QuantumMinimap {
  origin_sector_id: number;
  spacing: number;
  complete_radius_spacings: number;
  sectors: MinimapSector[];
}

const RANGE_BANDS: { id: RangeBandId; label: string; range: string; tag?: string }[] = [
  { id: 'near', label: 'NEAR', range: '5–6' },
  { id: 'mid', label: 'MID', range: '7–8' },
  { id: 'far', label: 'FAR', range: '9–10', tag: '+1 SHARD' },
  { id: 'extended', label: 'EXTENDED', range: '12–15' },
];

// Resonance → how many of the 4 meter segments light up
const RESONANCE_LEVELS: Record<QuantumScanResult['resonance'], number> = {
  silent: 1,
  faint: 2,
  steady: 3,
  bright: 4,
};

const SCAN_TURN_COST = 5;
const JUMP_TURN_COST = 50;

// mm:ss under an hour, h:mm:ss above (jump cooldown is 24 scaled hours)
const formatCountdown = (ms: number): string => {
  const totalSeconds = Math.ceil(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const mm = String(minutes).padStart(2, '0');
  const ss = String(seconds).padStart(2, '0');
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
};

const QuantumDriveConsole: React.FC<QuantumDriveConsoleProps> = ({ onOpenGatewright }) => {
  const {
    playerState,
    quantumStatus,
    quantumScan,
    quantumJump,
    refineQuantumCharge,
    quantumScanResult,
    setQuantumScanResult,
  } = useGame();

  // --- Bearing controls ---
  const [yaw, setYaw] = useState(0);
  const [pitch, setPitch] = useState(0);
  const [rangeBand, setRangeBand] = useState<RangeBandId>('near');

  // --- Scan telemetry (lifted to context so a NAV mode flip can't destroy a
  // paid scan; we only read the slice tagged with our current sector) ---
  const [isScanning, setIsScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const originSectorId = playerState?.current_sector_id ?? null;
  const scanResult: QuantumScanResult | null =
    quantumScanResult && quantumScanResult.origin_sector_id === originSectorId
      ? quantumScanResult.result
      : null;

  // --- Jump ceremony: idle → armed → charging → outcome ---
  const [jumpPhase, setJumpPhase] = useState<'idle' | 'armed' | 'charging' | 'outcome'>('idle');
  const [jumpResult, setJumpResult] = useState<QuantumJumpResult | null>(null);
  const [jumpError, setJumpError] = useState<string | null>(null);

  // --- Charge refinement (docked at Class-3+/SpaceDock) ---
  const [isRefining, setIsRefining] = useState(false);
  const [refineError, setRefineError] = useState<string | null>(null);

  // --- Astrogation chart (minimap) — fetched once per sector while piloting
  // a Warp Jumper. On fetch failure the viewport renders WITHOUT dots and
  // shows an honest CHART UNAVAILABLE notice; we never fabricate sectors. ---
  const [minimap, setMinimap] = useState<QuantumMinimap | null>(null);
  const [chartFailed, setChartFailed] = useState(false);
  const [chartLoading, setChartLoading] = useState(false);
  const isWarpJumper = !!quantumStatus?.is_warp_jumper;
  useEffect(() => {
    if (!isWarpJumper || originSectorId === null) return;
    let cancelled = false;
    setChartFailed(false);
    setChartLoading(true);
    // Drop the previous sector's chart immediately — relative coordinates
    // from the old origin would plot dots in the wrong places.
    setMinimap((current) =>
      current && current.origin_sector_id !== originSectorId ? null : current
    );
    apiClient
      .get('/api/v1/quantum/minimap')
      .then((response) => {
        if (cancelled) return;
        const chart = response.data as QuantumMinimap;
        // Stale-response guard: a jump can land between request and reply;
        // a chart whose origin no longer matches our sector is garbage.
        if (chart.origin_sector_id === originSectorId) setMinimap(chart);
        setChartLoading(false);
      })
      .catch(() => {
        if (!cancelled) {
          setMinimap(null);
          setChartFailed(true);
          setChartLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [isWarpJumper, originSectorId]);

  // Render-time origin gate: never let a previous sector's chart (or one
  // that raced in around a jump) reach the viewport.
  const chart =
    minimap && minimap.origin_sector_id === originSectorId ? minimap : null;

  // 1s local tick drives every countdown (cooldowns + scan expiry)
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // ARM auto-disarms after 5s if the pilot doesn't confirm
  useEffect(() => {
    if (jumpPhase !== 'armed') return;
    const timer = setTimeout(() => setJumpPhase(phase => (phase === 'armed' ? 'idle' : phase)), 5000);
    return () => clearTimeout(timer);
  }, [jumpPhase]);

  const isMounted = useRef(true);
  useEffect(() => {
    isMounted.current = true;
    return () => { isMounted.current = false; };
  }, []);

  const msLeft = (iso?: string | null): number =>
    iso ? Math.max(0, new Date(iso).getTime() - now) : 0;

  const scanCooldownLeft = msLeft(quantumStatus?.scan_cooldown_until);
  const jumpCooldownLeft = msLeft(quantumStatus?.jump_cooldown_until);
  const scanExpiryLeft = msLeft(scanResult?.expires_at);
  const liveScan = scanResult && scanExpiryLeft > 0 ? scanResult : null;

  // Without a real status payload the drive isn't linked — render an explicit
  // loading state rather than fabricating zero-filled inventory.
  const statusReady = !!quantumStatus;
  const turns = playerState?.turns ?? 0;
  const charges = quantumStatus?.quantum_charges ?? 0;
  const shards = quantumStatus?.quantum_shards ?? 0;
  const crystals = quantumStatus?.quantum_crystals ?? 0;
  const sensorLevel = quantumStatus?.sensor_level ?? 0;
  const isDocked = !!(playerState?.is_docked || playerState?.is_landed);
  const extendedLocked = sensorLevel < 3;

  const bearing: QuantumBearing = { yaw_deg: yaw, pitch_deg: pitch, range_band: rangeBand };

  // --- Disable reasons (first match wins; null = ready) ---
  const scanBlockReason: string | null =
    !statusReady ? 'LINKING DRIVE…'
    : isScanning ? 'SCANNING…'
    : scanCooldownLeft > 0 ? `RECHARGE ${formatCountdown(scanCooldownLeft)}`
    : turns < SCAN_TURN_COST ? 'INSUFFICIENT TURNS'
    : rangeBand === 'far' && shards < 1 ? 'NO QUANTUM SHARD (FAR BAND)'
    : null;

  const jumpBlockReason: string | null =
    !statusReady ? 'LINKING DRIVE…'
    : jumpPhase === 'charging' ? 'TRANSLATING…'
    : isDocked ? 'DRIVE OFFLINE WHILE DOCKED'
    : jumpCooldownLeft > 0 ? `COOLDOWN ${formatCountdown(jumpCooldownLeft)}`
    : charges < 1 ? 'NO QUANTUM CHARGE'
    : turns < JUMP_TURN_COST ? 'INSUFFICIENT TURNS'
    : quantumStatus && !quantumStatus.can_jump ? 'DRIVE NOT READY'
    : null;

  const handleScan = async () => {
    if (scanBlockReason || (extendedLocked && rangeBand === 'extended')) return;
    const firedFromSector = originSectorId;
    setIsScanning(true);
    setScanError(null);
    try {
      const result = await quantumScan(bearing);
      // Persist into context FIRST — the context outlives this console, which
      // can unmount mid-await when the dashboard flashes its loading branch.
      // A paid scan must never be discarded because of a transient remount.
      if (firedFromSector !== null) {
        setQuantumScanResult({ origin_sector_id: firedFromSector, result });
      }
      if (!isMounted.current) return;
    } catch (error: any) {
      if (!isMounted.current) return;
      setScanError(error?.response?.data?.detail || 'Echo scan failed — drive sensors unresponsive');
    } finally {
      if (isMounted.current) setIsScanning(false);
    }
  };

  const handleJumpCommit = async () => {
    if (jumpBlockReason) return;
    setJumpPhase('charging');
    setJumpError(null);
    const startedAt = Date.now();
    try {
      const result = await quantumJump(bearing);
      // Charge-up ceremony: hold the animation for at least ~2s
      const remaining = Math.max(0, 2000 - (Date.now() - startedAt));
      if (remaining > 0) await new Promise(resolve => setTimeout(resolve, remaining));
      if (!isMounted.current) return;
      setJumpResult(result);
      setQuantumScanResult(null); // old echo telemetry is meaningless from a new sector
      setJumpPhase('outcome');
    } catch (error: any) {
      if (!isMounted.current) return;
      setJumpError(error?.response?.data?.detail || 'Quantum jump failed — drive aborted the translation');
      setJumpPhase('idle');
    }
  };

  const handleRefine = async () => {
    if (isRefining || shards < 1) return;
    setIsRefining(true);
    setRefineError(null);
    try {
      await refineQuantumCharge();
    } catch (error: any) {
      if (!isMounted.current) return;
      setRefineError(error?.response?.data?.detail || 'Charge refinement failed');
    } finally {
      if (isMounted.current) setIsRefining(false);
    }
  };

  const dismissOutcome = () => {
    setJumpResult(null);
    setJumpPhase('idle');
  };

  // Astrogation viewport phase mirrors the existing scan/jump state machine
  const viewportPhase: 'idle' | 'scanning' | 'charging' =
    jumpPhase === 'charging' ? 'charging' : isScanning ? 'scanning' : 'idle';

  return (
    <div className="qd-console">
      {/* Scroll container — overlays below stay pinned to the visible screen */}
      <div className="qd-console-scroll">
      {/* Inventory strip — explicit loading state until the drive links */}
      {!statusReady ? (
        <div className="qd-inventory qd-inventory-linking" role="status" aria-live="polite">
          <span className="qd-linking-text">LINKING DRIVE…</span>
          <span className="qd-linking-spinner" aria-hidden="true">⟳</span>
        </div>
      ) : (
      <div className="qd-inventory">
        <div className="qd-inv-item" title="Quantum shards (raw)">
          <span className="qd-inv-icon">💠</span>
          <span className="qd-inv-count">{shards}</span>
          <span className="qd-inv-label">SHARDS</span>
        </div>
        <div className="qd-inv-item" title="Quantum crystals (pristine)">
          <span className="qd-inv-icon">🔮</span>
          <span className="qd-inv-count">{crystals}</span>
          <span className="qd-inv-label">CRYSTALS</span>
        </div>
        <div className="qd-inv-item" title="Refined charges loaded in the drive">
          <span className="qd-inv-icon">⚡</span>
          <span className="qd-inv-count">{charges}</span>
          <span className="qd-inv-label">CHARGES</span>
        </div>
        <button
          className="qd-refine-btn"
          onClick={handleRefine}
          disabled={isRefining || shards < 1 || !isDocked}
          title={!isDocked
            ? 'Dock at a Class-3+ station or SpaceDock to refine shards into charges'
            : shards < 1 ? 'No shards to refine' : 'Refine 1 shard into 1 drive charge'}
        >
          {isRefining ? 'REFINING…' : 'REFINE 1⟶1'}
        </button>
      </div>
      )}
      {refineError && <div className="qd-inline-error">{refineError}</div>}

      {/* Bearing block — astrogation viewport (drag = yaw), pitch slider
          beside it, yaw fine-tune slider beneath. The viewport is a pure
          instrument: this console owns all bearing state. */}
      <div className="qd-section qd-bearing">
        <div className="qd-section-label">BEARING</div>
        <div className="qd-viewport-row">
          <QuantumBearingViewport
            yawDeg={yaw}
            pitchDeg={pitch}
            rangeBand={rangeBand}
            onBearingChange={(newYaw, newPitch) => {
              setYaw(Math.min(360, Math.max(0, Math.round(newYaw))));
              setPitch(Math.min(90, Math.max(-90, Math.round(newPitch))));
            }}
            phase={viewportPhase}
            spacing={chart?.spacing ?? null}
            sectors={chartFailed ? null : (chart?.sectors ?? [])}
            chartLoading={chartLoading}
            completeRadiusSpacings={chart?.complete_radius_spacings ?? null}
            scanResult={liveScan}
          />
          <div className="qd-pitch-block">
            <div className="qd-readout-row">
              <span className="qd-readout-label">PITCH</span>
              <span className="qd-readout-value">{pitch > 0 ? '+' : ''}{pitch}°</span>
            </div>
            <div className="qd-pitch-slider-rail">
              <span className="qd-pitch-tick" aria-hidden="true">+90</span>
              <input
                type="range"
                className="qd-pitch-slider"
                min={-90}
                max={90}
                step={1}
                value={pitch}
                onChange={(e) => setPitch(Math.min(90, Math.max(-90, parseInt(e.target.value, 10) || 0)))}
                aria-label="Pitch bearing in degrees"
              />
              <span className="qd-pitch-tick" aria-hidden="true">−90</span>
            </div>
          </div>
        </div>
        <div className="qd-yaw-block qd-yaw-finetune">
          <div className="qd-readout-row">
            <span className="qd-readout-label">YAW · FINE</span>
            <span className="qd-readout-value">{String(yaw).padStart(3, '0')}°</span>
          </div>
          <input
            type="range"
            className="qd-yaw-slider"
            min={0}
            max={360}
            step={1}
            value={yaw}
            onChange={(e) => setYaw(Math.min(360, Math.max(0, parseInt(e.target.value, 10) || 0)))}
            aria-label="Yaw bearing in degrees"
          />
          <div className="qd-yaw-ticks" aria-hidden="true">
            <span>N·000</span>
            <span>E·090</span>
            <span>S·180</span>
            <span>W·270</span>
            <span>N·360</span>
          </div>
        </div>
      </div>

      {/* Range band selector */}
      <div className="qd-section">
        <div className="qd-section-label">RANGE BAND</div>
        <div className="qd-band-row">
          {RANGE_BANDS.map((band) => {
            const locked = band.id === 'extended' && extendedLocked;
            return (
              <button
                key={band.id}
                className={`qd-band-btn ${rangeBand === band.id ? 'active' : ''} ${locked ? 'locked' : ''}`}
                onClick={() => { if (!locked) setRangeBand(band.id); }}
                disabled={locked}
                title={locked ? 'Extended-band targeting requires sensor level 3' : `${band.label}: ${band.range} sectors`}
              >
                <span className="qd-band-name">{band.label}</span>
                <span className="qd-band-range">{band.range}</span>
                {band.tag && <span className="qd-band-tag">{band.tag}</span>}
                {locked && <span className="qd-band-tag locked-tag">SENSOR L3 REQUIRED</span>}
              </button>
            );
          })}
        </div>
      </div>

      {/* Echo scan */}
      <div className="qd-section">
        <div className="qd-action-row">
          <button
            className="qd-scan-btn"
            onClick={handleScan}
            disabled={!!scanBlockReason || (rangeBand === 'extended' && extendedLocked)}
          >
            {scanBlockReason || 'ECHO SCAN'}
            {!scanBlockReason && (
              <span className="qd-cost-tag">
                {rangeBand === 'far' ? `${SCAN_TURN_COST} TURNS + 1 SHARD` : `${SCAN_TURN_COST} TURNS`}
              </span>
            )}
          </button>
        </div>
        {scanError && <div className="qd-inline-error">{scanError}</div>}

        {liveScan && (
          <div className="qd-telemetry">
            <div className="qd-telemetry-header">
              <span>ECHO TELEMETRY</span>
              <span className="qd-telemetry-expiry">FADES {formatCountdown(scanExpiryLeft)}</span>
            </div>
            <div className="qd-telemetry-row">
              <span className="qd-tele-label">RESONANCE</span>
              <span className="qd-resonance-meter" aria-label={`Resonance ${liveScan.resonance}`}>
                {[1, 2, 3, 4].map((level) => (
                  <span
                    key={level}
                    className={`qd-res-seg ${level <= RESONANCE_LEVELS[liveScan.resonance] ? 'lit' : ''} ${liveScan.resonance === 'silent' ? 'dim' : ''}`}
                  />
                ))}
              </span>
              <span className="qd-tele-value">{liveScan.resonance.toUpperCase()}</span>
            </div>
            <div className="qd-telemetry-row">
              <span className="qd-tele-label">TEXTURE</span>
              <span className="qd-tele-value">{liveScan.texture.toUpperCase()}</span>
            </div>
            <div className="qd-telemetry-row">
              <span className="qd-tele-label">ECHO</span>
              <span className="qd-tele-value">{liveScan.echo.toUpperCase()}</span>
            </div>
          </div>
        )}
      </div>

      {/* Jump commit */}
      <div className="qd-section qd-jump-section">
        {jumpPhase !== 'armed' ? (
          <button
            className="qd-jump-btn"
            onClick={() => { if (!jumpBlockReason) setJumpPhase('armed'); }}
            disabled={!!jumpBlockReason || jumpPhase === 'outcome'}
          >
            {jumpBlockReason || 'JUMP COMMIT'}
            {!jumpBlockReason && <span className="qd-cost-tag">1 CHARGE + {JUMP_TURN_COST} TURNS</span>}
          </button>
        ) : (
          <div className="qd-confirm-row">
            <button className="qd-jump-btn confirm" onClick={handleJumpCommit}>
              CONFIRM TRANSLATION
            </button>
            <button className="qd-abort-btn" onClick={() => setJumpPhase('idle')}>
              ABORT
            </button>
          </div>
        )}
        {jumpError && <div className="qd-inline-error">{jumpError}</div>}
      </div>

      {/* Footer: Gatewright access */}
      <div className="qd-footer">
        <button className="qd-gatewright-btn" onClick={onOpenGatewright}>
          ⛩ GATEWRIGHT
        </button>
      </div>
      </div>{/* /.qd-console-scroll */}

      {/* Charge-up ceremony overlay */}
      {jumpPhase === 'charging' && (
        <div className="qd-charging-overlay" role="status" aria-live="polite">
          <div className="qd-charging-rings" aria-hidden="true">
            <span /><span /><span />
          </div>
          <div className="qd-charging-text">QUANTUM TRANSLATION IN PROGRESS</div>
        </div>
      )}

      {/* Outcome card */}
      {jumpPhase === 'outcome' && jumpResult && (
        <div className={`qd-outcome-overlay ${jumpResult.outcome === 'misfire' ? 'misfire' : 'success'}`}>
          <div className="qd-outcome-card">
            <div className="qd-outcome-title">
              {jumpResult.outcome === 'misfire' ? '⚠ MISFIRE' : '◈ QUANTUM TRANSLATION COMPLETE'}
            </div>
            <div className="qd-outcome-body">
              {jumpResult.outcome === 'misfire'
                ? <>Emergency reversion at <strong>{jumpResult.destination_name}</strong>, hull −{jumpResult.hull_damage_pct}%</>
                : <>Arrived <strong>{jumpResult.destination_name}</strong></>}
            </div>
            <div className="qd-outcome-meta">
              DISTANCE {jumpResult.distance_jumped} SECTORS · TURNS REMAINING {jumpResult.turns_remaining.toLocaleString()}
            </div>
            <button className="qd-outcome-dismiss" onClick={dismissOutcome}>
              ACKNOWLEDGE
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default QuantumDriveConsole;

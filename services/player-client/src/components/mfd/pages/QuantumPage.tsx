/**
 * QUANTUM DRIVE — MFD-A page (NEON15, zone B2). Status: partial.
 *
 * Registry hides this page unless the active ship is a Warp Jumper; the
 * null-status guard below covers the race before quantumStatus hydrates.
 * Field provenance (verified — QuantumStatus interface, GameContext.tsx):
 *   quantum_charges, quantum_shards, quantum_crystals, sensor_level,
 *   can_jump, jump_cooldown_until, scan_cooldown_until
 * Plus quantumScanTelemetry (context `quantumScanResult`): last paid echo
 * scan {resonance, texture}, already cleared by context on sector change.
 */
import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDInsufficient } from '../atoms';
import './pages-ship.css';

const ACCENT = '#7B2FFF';

/** ISO timestamp → local clock time, '—' for null/garbage. */
const fmtTime = (iso: string | null): string => {
  if (!iso) return '—';
  const t = new Date(iso);
  return Number.isNaN(t.getTime())
    ? '—'
    : t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
};

const QuantumPage: React.FC = () => {
  const { quantumStatus, quantumScanResult } = useGame();

  if (!quantumStatus) {
    return (
      <>
        <MFDPageHeader title="QUANTUM DRIVE" accent={ACCENT} status="partial" showTitle={false} />
        <MFDPageBody scrollKey="quantum-drive">
          <MFDInsufficient text="QUANTUM TELEMETRY OFFLINE" />
        </MFDPageBody>
      </>
    );
  }

  const lastEcho = quantumScanResult
    ? `${quantumScanResult.result.resonance.toUpperCase()} · ${quantumScanResult.result.texture.toUpperCase()}`
    : '—';

  return (
    <>
      <MFDPageHeader title="QUANTUM DRIVE" accent={ACCENT} status="partial" showTitle={false} />
      <MFDPageBody scrollKey="quantum-drive">
        <div className="mfd-page-fields">
          <MFDField
            label="JUMP"
            value={quantumStatus.can_jump ? 'READY' : 'NOT READY'}
            accent={quantumStatus.can_jump}
          />
          <MFDField label="CHARGES" value={quantumStatus.quantum_charges} />
          <MFDField label="SHARDS" value={quantumStatus.quantum_shards} />
          <MFDField label="CRYSTALS" value={quantumStatus.quantum_crystals} />
          <MFDField label="SENSOR LVL" value={quantumStatus.sensor_level} />
          <MFDField label="JUMP CD" value={fmtTime(quantumStatus.jump_cooldown_until)} />
          <MFDField label="SCAN CD" value={fmtTime(quantumStatus.scan_cooldown_until)} />
          <MFDField label="LAST ECHO" value={lastEcho} />
        </div>
      </MFDPageBody>
    </>
  );
};

export default React.memo(QuantumPage);

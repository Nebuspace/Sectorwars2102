// @vitest-environment jsdom
/**
 * QuantumPage — MFD-A quantum drive page. Mirrors CargoPage.test.tsx /
 * ReputationPage.test.tsx / CommsCrewPage.test.tsx's seam: jsdom +
 * react-dom/client createRoot + act(), no RTL, no new deps.
 *
 * Pins: the null-quantumStatus telemetry-offline guard, every MFDField
 * reading live off quantumStatus (not hardcoded), the JUMP field's
 * accent-on-can_jump wiring, the cooldown-timestamp formatter's null/
 * garbage/valid-ISO handling, and the LAST ECHO field's presence/absence
 * off quantumScanResult.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const baseStatus = (overrides: Record<string, unknown> = {}) => ({
  quantum_shards: 3,
  quantum_crystals: 1,
  quantum_charges: 2,
  jump_cooldown_until: null,
  scan_cooldown_until: null,
  can_jump: true,
  is_warp_jumper: true,
  sensor_level: 4,
  ...overrides,
});

let mockQuantumStatus: ReturnType<typeof baseStatus> | null = baseStatus();
let mockQuantumScanResult: {
  origin_sector_id: number;
  result: { resonance: string; texture: string };
} | null = null;

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    quantumStatus: mockQuantumStatus,
    quantumScanResult: mockQuantumScanResult,
  }),
}));

import QuantumPage from './QuantumPage';

describe('QuantumPage', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockQuantumStatus = baseStatus();
    mockQuantumScanResult = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<QuantumPage />);
    });
  };

  const fieldValue = (label: string): string | undefined => {
    const fields = Array.from(container.querySelectorAll('.mfd-field'));
    const field = fields.find((f) => f.querySelector('.mfd-field-label')?.textContent === label);
    return field?.querySelector('.mfd-field-value')?.textContent ?? undefined;
  };

  it('shows QUANTUM TELEMETRY OFFLINE and renders no fields when quantumStatus is null', async () => {
    mockQuantumStatus = null;
    await mount();

    expect(container.querySelector('.mfd-insufficient')?.textContent).toBe(
      'QUANTUM TELEMETRY OFFLINE',
    );
    expect(container.querySelectorAll('.mfd-field').length).toBe(0);
  });

  it('renders every field bound to the real quantumStatus values', async () => {
    mockQuantumStatus = baseStatus({
      quantum_charges: 7,
      quantum_shards: 5,
      quantum_crystals: 2,
      sensor_level: 9,
    });
    await mount();

    expect(fieldValue('CHARGES')).toBe('7');
    expect(fieldValue('SHARDS')).toBe('5');
    expect(fieldValue('CRYSTALS')).toBe('2');
    expect(fieldValue('SENSOR LVL')).toBe('9');
  });

  it('shows JUMP READY with the accent class when can_jump is true', async () => {
    mockQuantumStatus = baseStatus({ can_jump: true });
    await mount();

    expect(fieldValue('JUMP')).toBe('READY');
    const fields = Array.from(container.querySelectorAll('.mfd-field'));
    const jumpField = fields.find((f) => f.querySelector('.mfd-field-label')?.textContent === 'JUMP');
    expect(jumpField?.className).toContain('mfd-field-accent');
  });

  it('shows JUMP NOT READY without the accent class when can_jump is false', async () => {
    mockQuantumStatus = baseStatus({ can_jump: false });
    await mount();

    expect(fieldValue('JUMP')).toBe('NOT READY');
    const fields = Array.from(container.querySelectorAll('.mfd-field'));
    const jumpField = fields.find((f) => f.querySelector('.mfd-field-label')?.textContent === 'JUMP');
    expect(jumpField?.className).not.toContain('mfd-field-accent');
  });

  it('renders an em-dash for null cooldown timestamps', async () => {
    mockQuantumStatus = baseStatus({ jump_cooldown_until: null, scan_cooldown_until: null });
    await mount();

    expect(fieldValue('JUMP CD')).toBe('—');
    expect(fieldValue('SCAN CD')).toBe('—');
  });

  it('renders an em-dash for a garbage (unparseable) cooldown timestamp', async () => {
    mockQuantumStatus = baseStatus({ jump_cooldown_until: 'not-a-date' });
    await mount();

    expect(fieldValue('JUMP CD')).toBe('—');
  });

  it('formats a valid ISO cooldown timestamp as a local clock time, not the raw string', async () => {
    mockQuantumStatus = baseStatus({ jump_cooldown_until: '2026-08-09T12:00:00.000Z' });
    await mount();

    const value = fieldValue('JUMP CD');
    expect(value).toBeDefined();
    expect(value).not.toBe('—');
    expect(value).not.toContain('2026-08-09T12:00:00.000Z');
  });

  it('shows an em-dash LAST ECHO when there is no scan result', async () => {
    mockQuantumScanResult = null;
    await mount();

    expect(fieldValue('LAST ECHO')).toBe('—');
  });

  it('shows the resonance/texture pair, uppercased, when a scan result exists', async () => {
    mockQuantumScanResult = {
      origin_sector_id: 5,
      result: { resonance: 'bright', texture: 'mineral' },
    };
    await mount();

    expect(fieldValue('LAST ECHO')).toBe('BRIGHT · MINERAL');
  });

  it('suppresses the page title but keeps the PARTIAL status chip (showTitle=false)', async () => {
    await mount();

    expect(container.querySelector('.mfd-page-title')).toBeNull();
    expect(container.querySelector('.mfd-chip-partial')?.textContent).toBe('PARTIAL');
  });
});

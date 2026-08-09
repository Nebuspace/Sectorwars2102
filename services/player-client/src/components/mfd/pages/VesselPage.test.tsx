// @vitest-environment jsdom
/**
 * VesselPage — MFD-A vessel status page. Mirrors the CargoPage/QuantumPage/
 * NavPositionPage MFD-page test seam: jsdom + react-dom/client createRoot +
 * act(), no RTL, no new deps. VesselPage is React.memo'd (like NavPositionPage)
 * -- one mount per test, never two root.render() calls in the same test with
 * unchanged props (memo silently skips the second).
 *
 * Pins: the no-active-vessel guard, every field's live data-binding incl. the
 * hull/shields gauge's three-way current/max/absent formatting, the
 * condition-vs-current_rating fallback, the failure-status warnline's
 * text/empty/"NONE" filtering, and the GENESIS BAY section's presence gate
 * + slot lit/unlit rendering.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseShip = (overrides: Record<string, unknown> = {}) => ({
  id: 'ship-1',
  name: 'Test Freighter',
  type: 'LIGHT_FREIGHTER',
  sector_id: 5,
  cargo: { used: 0, capacity: 50, contents: {} },
  cargo_capacity: 50,
  current_speed: 1,
  base_speed: 1,
  combat: { hull: 80, max_hull: 100, shields: 20, max_shields: 40 },
  maintenance: { condition: 90, failure_status: 'NONE' },
  is_flagship: false,
  purchase_value: 0,
  current_value: 0,
  genesis_devices: 0,
  max_genesis_devices: 0,
  ...overrides,
});

const basePlayerState = (overrides: Record<string, unknown> = {}) => ({
  defense_drones: 3,
  attack_drones: 5,
  ...overrides,
});

let mockCurrentShip: ReturnType<typeof baseShip> | null = baseShip();
let mockPlayerState: ReturnType<typeof basePlayerState> | null = basePlayerState();

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ currentShip: mockCurrentShip, playerState: mockPlayerState }),
}));

import VesselPage from './VesselPage';

describe('VesselPage', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockCurrentShip = baseShip();
    mockPlayerState = basePlayerState();
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
      root.render(<VesselPage />);
    });
  };

  const fieldValue = (label: string): string | undefined => {
    const fields = Array.from(container.querySelectorAll('.mfd-field'));
    const field = fields.find((f) => f.querySelector('.mfd-field-label')?.textContent === label);
    return field?.querySelector('.mfd-field-value')?.textContent ?? undefined;
  };

  it('shows NO ACTIVE VESSEL and no fields when there is no current ship', async () => {
    mockCurrentShip = null;
    await mount();

    expect(container.querySelector('.mfd-empty')?.textContent).toBe('NO ACTIVE VESSEL');
    expect(container.querySelectorAll('.mfd-field').length).toBe(0);
  });

  it('renders VESSEL name and CLASS with underscores replaced by spaces', async () => {
    mockCurrentShip = baseShip({ name: 'Star Runner', type: 'HEAVY_FREIGHTER' });
    await mount();

    expect(fieldValue('VESSEL')).toBe('Star Runner');
    expect(fieldValue('CLASS')).toBe('HEAVY FREIGHTER');
  });

  it('falls back VESSEL to an em-dash when name is empty', async () => {
    mockCurrentShip = baseShip({ name: '' });
    await mount();
    expect(fieldValue('VESSEL')).toBe('—');
  });

  it('renders HULL as "current / max" when both legs exist', async () => {
    mockCurrentShip = baseShip({ combat: { hull: 412, max_hull: 500 } });
    await mount();
    expect(fieldValue('HULL')).toBe('412 / 500');
  });

  it('renders HULL as the lone current value when max is missing', async () => {
    mockCurrentShip = baseShip({ combat: { hull: 412 } });
    await mount();
    expect(fieldValue('HULL')).toBe('412');
  });

  it('renders HULL as an em-dash when neither leg is a finite number', async () => {
    mockCurrentShip = baseShip({ combat: {} });
    await mount();
    expect(fieldValue('HULL')).toBe('—');
    expect(fieldValue('SHIELDS')).toBe('—');
  });

  it('treats a non-finite combat value as absent, not a fake number', async () => {
    mockCurrentShip = baseShip({ combat: { hull: NaN, max_hull: Infinity } });
    await mount();
    expect(fieldValue('HULL')).toBe('—');
  });

  it('renders CONDITION from the condition field', async () => {
    mockCurrentShip = baseShip({ maintenance: { condition: 77 } });
    await mount();
    expect(fieldValue('CONDITION')).toBe('77%');
  });

  it('falls back CONDITION to current_rating when condition is absent', async () => {
    mockCurrentShip = baseShip({ maintenance: { current_rating: 42 } });
    await mount();
    expect(fieldValue('CONDITION')).toBe('42%');
  });

  it('renders CONDITION as an em-dash when neither field is present', async () => {
    mockCurrentShip = baseShip({ maintenance: {} });
    await mount();
    expect(fieldValue('CONDITION')).toBe('—');
  });

  it('renders DEF/ATK DRONES from playerState when present', async () => {
    mockPlayerState = basePlayerState({ defense_drones: 6, attack_drones: 9 });
    await mount();
    expect(fieldValue('DEF DRONES')).toBe('6');
    expect(fieldValue('ATK DRONES')).toBe('9');
  });

  it('renders DEF/ATK DRONES as em-dashes when playerState is null', async () => {
    mockPlayerState = null;
    await mount();
    expect(fieldValue('DEF DRONES')).toBe('—');
    expect(fieldValue('ATK DRONES')).toBe('—');
  });

  it('hides the GENESIS BAY section when max_genesis_devices is 0', async () => {
    mockCurrentShip = baseShip({ max_genesis_devices: 0, genesis_devices: 0 });
    await mount();
    expect(container.querySelector('.mfd-page-genesis-row')).toBeNull();
  });

  it('renders lit vs unlit genesis slots matching the real loaded/max counts', async () => {
    mockCurrentShip = baseShip({ max_genesis_devices: 3, genesis_devices: 2 });
    await mount();

    const slots = container.querySelectorAll('.mfd-page-genesis-slot');
    expect(slots.length).toBe(3);
    expect(container.querySelectorAll('.mfd-page-genesis-slot.loaded').length).toBe(2);
    expect(container.querySelectorAll('.mfd-page-genesis-slot.empty').length).toBe(1);
    expect(container.querySelector('.mfd-page-genesis-count')?.textContent).toBe('2 / 3');
    expect(container.querySelector('.mfd-page-genesis-count')?.className).toContain('active');
  });

  it('does not mark the genesis count active when nothing is loaded', async () => {
    mockCurrentShip = baseShip({ max_genesis_devices: 2, genesis_devices: 0 });
    await mount();
    expect(container.querySelector('.mfd-page-genesis-count')?.className).not.toContain('active');
  });

  it('shows the failure warnline when failure_status is a real, non-NONE string', async () => {
    mockCurrentShip = baseShip({ maintenance: { failure_status: 'ENGINE' } });
    await mount();

    const warnline = container.querySelector('.mfd-page-warnline');
    expect(warnline?.textContent).toBe('ENGINE FAILURE DETECTED');
    expect(warnline?.getAttribute('role')).toBe('alert');
  });

  it('hides the failure warnline when failure_status is "NONE"', async () => {
    mockCurrentShip = baseShip({ maintenance: { failure_status: 'NONE' } });
    await mount();
    expect(container.querySelector('.mfd-page-warnline')).toBeNull();
  });

  it('hides the failure warnline when failure_status is empty or absent', async () => {
    mockCurrentShip = baseShip({ maintenance: {} });
    await mount();
    expect(container.querySelector('.mfd-page-warnline')).toBeNull();
  });
});

// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

const mockInvestigateAnomaly = vi.fn();
const mockInvestigateFormation = vi.fn();

vi.mock('../../../services/api', () => ({
  playerAPI: {
    investigateAnomaly: (...a: unknown[]) => mockInvestigateAnomaly(...a),
    investigateFormation: (...a: unknown[]) => mockInvestigateFormation(...a),
  },
}));

import AnomalyInvestigateCta, {
  formatAnomalyInvestigateError,
} from '../AnomalyInvestigateCta';

describe('formatAnomalyInvestigateError TypeError densify (LEG-3095)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatAnomalyInvestigateError(new TypeError('Failed to fetch'));
    expect(text).toBe('Investigation failed. Please try again.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves server detail for non-TypeError errors', () => {
    const err = Object.assign(new Error('sector_locked'), {
      response: { data: { detail: 'Sector is locked by another captain.' } },
    });
    expect(formatAnomalyInvestigateError(err)).toBe('Sector is locked by another captain.');
  });
});

describe('AnomalyInvestigateCta', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.clearAllMocks();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('does not render for non-ANOMALY sectors', async () => {
    await act(async () => {
      root.render(
        <AnomalyInvestigateCta sectorId={42} sectorType="ASTEROID_FIELD" />,
      );
    });
    expect(container.querySelector('[data-testid="anomaly-investigate-cta"]')).toBeNull();
  });

  it('visitor/occupant INVESTIGATE posts the sector route, not formation investigate', async () => {
    mockInvestigateAnomaly.mockResolvedValue({
      sector: { id: 's', name: 'Rift', type: 'ANOMALY', is_investigated: true },
      reward: { credits: 250 },
      credits_remaining: 1250,
      reward_is_no_canon: false,
    });
    await act(async () => {
      root.render(<AnomalyInvestigateCta sectorId={42} sectorType="ANOMALY" />);
    });
    const btn = container.querySelector(
      '[data-testid="anomaly-investigate-btn"]',
    ) as HTMLButtonElement;
    expect(btn).toBeTruthy();
    await act(async () => {
      btn.click();
    });
    expect(mockInvestigateAnomaly).toHaveBeenCalledWith(42);
    expect(mockInvestigateFormation).not.toHaveBeenCalled();
    expect(container.textContent).toContain('+250 cr');
  });

  it('409 already-done marks investigated without calling formation API', async () => {
    mockInvestigateAnomaly.mockRejectedValue(
      Object.assign(new Error('Anomaly has already been investigated.'), { status: 409 }),
    );
    await act(async () => {
      root.render(<AnomalyInvestigateCta sectorId={7} sectorType="ANOMALY" />);
    });
    const btn = container.querySelector(
      '[data-testid="anomaly-investigate-btn"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    expect(mockInvestigateAnomaly).toHaveBeenCalledWith(7);
    expect(mockInvestigateFormation).not.toHaveBeenCalled();
    expect(btn.disabled).toBe(true);
    expect(container.textContent).toMatch(/already been investigated/i);
  });

  it('investigate TypeError surfaces fallback without Failed to fetch / TypeError (LEG-3095)', async () => {
    mockInvestigateAnomaly.mockRejectedValue(new TypeError('Failed to fetch'));
    await act(async () => {
      root.render(<AnomalyInvestigateCta sectorId={42} sectorType="ANOMALY" />);
    });
    const btn = container.querySelector(
      '[data-testid="anomaly-investigate-btn"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    const status = container.querySelector('[data-testid="anomaly-investigate-status"]');
    expect(status).toBeTruthy();
    expect(status?.textContent).toBe('Investigation failed. Please try again.');
    expect(status?.textContent).not.toMatch(/Failed to fetch/i);
    expect(status?.textContent).not.toMatch(/TypeError/i);
    expect(btn.disabled).toBe(false);
  });
});

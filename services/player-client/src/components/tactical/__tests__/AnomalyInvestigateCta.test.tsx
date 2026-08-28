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

import AnomalyInvestigateCta from '../AnomalyInvestigateCta';

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
});

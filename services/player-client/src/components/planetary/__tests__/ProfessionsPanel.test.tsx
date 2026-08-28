// @vitest-environment jsdom
/**
 * ProfessionsPanel — owner-only profession training UI (LEG-2583 / LEG-INI-21).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getPlanetProfessions, trainPlanetProfession, OWNER_STATE } = vi.hoisted(() => {
  const state = {
    planet_id: 'planet-1',
    generic_colonists: 5000,
    cost_blocked: true,
    cost_block_reason: 'DECISION-NEEDED: profession training costs/caps not yet ruled',
    professions: {
      SPACE_ENGINEERS: 0,
      STRUCTURAL_ENGINEERS: 0,
      MINING_ENGINEERS: 120,
      RESEARCH_SCIENTISTS: 0,
      AGRICULTURAL_SCIENTISTS: 0,
      MEDICAL_PROFESSIONALS: 0,
      TERRAFORM_ENGINEERS: 0,
      COMBAT_PILOTS: 0,
      DEFENSE_COORDINATORS: 0,
      STRATEGIC_ANALYSTS: 0,
      TRADE_SPECIALISTS: 0,
      INDUSTRIAL_MANAGERS: 0,
    },
    training_queue: [
      {
        id: 'q-1',
        profession: 'MINING_ENGINEERS',
        trainee_count: 50,
        completes_at: '2026-09-01T12:00:00Z',
        status: 'QUEUED',
        training_days: 20,
      },
    ],
    training_durations_days: { MINING_ENGINEERS: 20 },
  };
  return {
    OWNER_STATE: state,
    getPlanetProfessions: vi.fn(async () => state),
    trainPlanetProfession: vi.fn(async () => ({
      success: true,
      cost_blocked: true,
      message: 'Training queued without charge — profession cost magnitudes remain DECISION-NEEDED.',
    })),
  };
});

vi.mock('../../../services/api', () => ({
  planetaryAPI: {
    getPlanetProfessions,
    trainPlanetProfession,
  },
}));

import ProfessionsPanel from '../ProfessionsPanel';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('ProfessionsPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getPlanetProfessions.mockClear();
    trainPlanetProfession.mockClear();
    getPlanetProfessions.mockResolvedValue(OWNER_STATE);
    trainPlanetProfession.mockResolvedValue({
      success: true,
      cost_blocked: true,
      message: 'Training queued without charge — profession cost magnitudes remain DECISION-NEEDED.',
    });
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

  it('shows profession state for owner at citadel L3+ with DECISION-NEEDED cost notice', async () => {
    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={3} />);
    });
    await act(async () => {
      await flush();
    });

    expect(getPlanetProfessions).toHaveBeenCalledWith('planet-1');
    expect(container.textContent).toContain('Colonist Professions');
    expect(container.textContent).toContain('Mining Engineers');
    expect(container.textContent).toContain('120');
    expect(container.querySelector('[data-testid="professions-cost-blocked"]')?.textContent).toContain(
      'DECISION-NEEDED',
    );
    expect(container.textContent).not.toMatch(/\d+\s*cr/i);
  });

  it('hides the panel when the server rejects non-owner access', async () => {
    getPlanetProfessions.mockRejectedValueOnce(
      new Error('Only the planet owner can view professions'),
    );

    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={4} />);
    });
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('.professions-panel')).toBeNull();
  });

  it('posts train request when Queue training is clicked', async () => {
    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={3} />);
    });
    await act(async () => {
      await flush();
    });

    const btn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Queue training'),
    );
    expect(btn).toBeTruthy();

    await act(async () => {
      btn!.click();
      await flush();
    });

    expect(trainPlanetProfession).toHaveBeenCalledWith('planet-1', 'SPACE_ENGINEERS', 100);
  });

  it('blocks train UI below citadel L3 without inventing prices', async () => {
    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={2} />);
    });
    await act(async () => {
      await flush();
    });

    expect(container.textContent).toContain('Citadel level 3+');
    const btn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Queue training'),
    );
    expect(btn?.disabled).toBe(true);
    expect(container.textContent).not.toMatch(/price|credits per/i);
  });
});

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
    training_eligibility: {
      SPACE_ENGINEERS: true,
      STRUCTURAL_ENGINEERS: true,
      MINING_ENGINEERS: true,
      RESEARCH_SCIENTISTS: true,
      AGRICULTURAL_SCIENTISTS: true,
      MEDICAL_PROFESSIONALS: true,
      TERRAFORM_ENGINEERS: true,
      COMBAT_PILOTS: true,
      DEFENSE_COORDINATORS: true,
      STRATEGIC_ANALYSTS: true,
      TRADE_SPECIALISTS: true,
      INDUSTRIAL_MANAGERS: true,
    },
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

import ProfessionsPanel, { formatProfessionsLoadError } from '../ProfessionsPanel';

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

  it('hides Research Scientists from train select when training_eligibility is false', async () => {
    getPlanetProfessions.mockResolvedValueOnce({
      ...OWNER_STATE,
      training_eligibility: {
        ...OWNER_STATE.training_eligibility,
        RESEARCH_SCIENTISTS: false,
      },
    });

    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={3} />);
    });
    await act(async () => {
      await flush();
    });

    const options = Array.from(container.querySelectorAll('select option')).map((o) =>
      o.textContent?.trim(),
    );
    expect(options.some((t) => t?.includes('Research Scientists'))).toBe(false);
    expect(container.querySelector('[data-testid="professions-research-lab-gate"]')?.textContent).toContain(
      'Research Lab level 3',
    );
  });

  it('shows Research Scientists in train select when training_eligibility is true', async () => {
    getPlanetProfessions.mockResolvedValueOnce({
      ...OWNER_STATE,
      training_eligibility: {
        ...OWNER_STATE.training_eligibility,
        RESEARCH_SCIENTISTS: true,
      },
    });

    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={3} />);
    });
    await act(async () => {
      await flush();
    });

    const options = Array.from(container.querySelectorAll('select option')).map((o) =>
      o.textContent?.trim(),
    );
    expect(options.some((t) => t?.includes('Research Scientists'))).toBe(true);
    expect(container.querySelector('[data-testid="professions-research-lab-gate"]')).toBeNull();
  });

  it('shows generic gate notice when an unknown profession is ineligible', async () => {
    getPlanetProfessions.mockResolvedValueOnce({
      ...OWNER_STATE,
      training_eligibility: {
        ...OWNER_STATE.training_eligibility,
        COMBAT_PILOTS: false,
      },
    });

    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={3} />);
    });
    await act(async () => {
      await flush();
    });

    const gate = container.querySelector('[data-testid="professions-training-gate-combat_pilots"]');
    expect(gate?.textContent).toContain('Combat Pilots');
    expect(gate?.textContent).toContain('not available yet');
    const options = Array.from(container.querySelectorAll('select option')).map((o) =>
      o.textContent?.trim(),
    );
    expect(options.some((t) => t?.includes('Combat Pilots'))).toBe(false);
  });

  it('shows no gate notice when a profession is eligible', async () => {
    getPlanetProfessions.mockResolvedValueOnce({
      ...OWNER_STATE,
      training_eligibility: {
        ...OWNER_STATE.training_eligibility,
        COMBAT_PILOTS: true,
      },
    });

    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={3} />);
    });
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('[data-testid="professions-training-gate-combat_pilots"]')).toBeNull();
    const options = Array.from(container.querySelectorAll('select option')).map((o) =>
      o.textContent?.trim(),
    );
    expect(options.some((t) => t?.includes('Combat Pilots'))).toBe(true);
  });

  it('formatProfessionsLoadError falls back on bare 403 without server detail', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatProfessionsLoadError(err)).toBe('You do not own this planet.');
  });

  it('formatProfessionsLoadError falls back on bare 404 without server detail', () => {
    const err = Object.assign(new Error('API Error: 404'), { status: 404 });
    expect(formatProfessionsLoadError(err)).toBe('Planet not found.');
  });

  it('surfaces load 404 planet-not-found server detail', async () => {
    getPlanetProfessions.mockRejectedValueOnce(
      Object.assign(new Error('Planet not found'), { status: 404 }),
    );

    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={3} />);
    });
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('.professions-panel__error')?.textContent).toBe('Planet not found');
  });

  it('shows honest fallback for bare 403 load failure', async () => {
    getPlanetProfessions.mockRejectedValueOnce(
      Object.assign(new Error('API Error: 403'), { status: 403 }),
    );

    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={3} />);
    });
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('.professions-panel__error')?.textContent).toBe(
      'You do not own this planet.',
    );
  });

  it('hides panel on not-owner load failure without showing error', async () => {
    getPlanetProfessions.mockRejectedValueOnce(
      Object.assign(new Error('Only the planet owner can view professions'), { status: 403 }),
    );

    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={3} />);
    });
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('.professions-panel__error')).toBeNull();
    expect(container.querySelector('.professions-panel')).toBeNull();
  });
});

  it('falls back on TypeError network collapse (LEG-3055)', () => {
    const text = formatProfessionsLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load professions/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError (LEG-3545)', () => {
    expect(formatProfessionsLoadError(new Error('Network Error'))).toBe('Failed to load professions');
    expect(formatProfessionsLoadError(new Error('Failed to fetch'))).toBe('Failed to load professions');
    expect(formatProfessionsLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
    expect(formatProfessionsLoadError(new Error('Failed to fetch'))).not.toMatch(/Failed to fetch/i);
  });

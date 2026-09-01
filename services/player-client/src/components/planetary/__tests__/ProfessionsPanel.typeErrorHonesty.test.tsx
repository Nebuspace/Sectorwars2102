// @vitest-environment jsdom
/**
 * LEG-3671 Soft-ORDER — ProfessionsPanel TypeError/Network Error densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getPlanetProfessions, trainPlanetProfession, OWNER_STATE } = vi.hoisted(() => {
  const state = {
    planet_id: 'planet-1',
    generic_colonists: 5000,
    cost_blocked: false,
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
    training_queue: [],
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
      message: 'Training queued.',
    })),
  };
});

vi.mock('../../../services/api', () => ({
  planetaryAPI: {
    getPlanetProfessions,
    trainPlanetProfession,
  },
}));

import ProfessionsPanel, {
  formatProfessionsLoadError,
  formatProfessionsTrainError,
} from '../ProfessionsPanel';

const LOAD_FALLBACK = 'Failed to load professions';
const TRAIN_FALLBACK = 'Training failed';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('ProfessionsPanel load TypeError densify (LEG-3671)', () => {
  it('formatProfessionsLoadError falls back on TypeError network collapse', () => {
    const text = formatProfessionsLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe(LOAD_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatProfessionsLoadError(new Error('Network Error'))).toBe(LOAD_FALLBACK);
    expect(formatProfessionsLoadError(new Error('Failed to fetch'))).toBe(LOAD_FALLBACK);
    expect(formatProfessionsLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatProfessionsLoadError(new Error('professions_offline'))).toBe('professions_offline');
  });
});

describe('ProfessionsPanel train TypeError densify (LEG-3671)', () => {
  it('formatProfessionsTrainError falls back on TypeError network collapse', () => {
    const text = formatProfessionsTrainError(new TypeError('Failed to fetch'));
    expect(text).toBe(TRAIN_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatProfessionsTrainError(new Error('Network Error'))).toBe(TRAIN_FALLBACK);
    expect(formatProfessionsTrainError(new Error('Failed to fetch'))).toBe(TRAIN_FALLBACK);
    expect(formatProfessionsTrainError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatProfessionsTrainError(new Error('queue_full'))).toBe('queue_full');
  });
});

describe('ProfessionsPanel transport collapse densify (LEG-3671)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getPlanetProfessions.mockReset();
    trainPlanetProfession.mockReset();
    getPlanetProfessions.mockResolvedValue(OWNER_STATE);
    trainPlanetProfession.mockResolvedValue({ success: true, message: 'Training queued.' });
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

  it('load Network Error surfaces honest fallback without raw transport text', async () => {
    getPlanetProfessions.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={3} />);
    });
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('.professions-panel__error')?.textContent).toBe(LOAD_FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('train Network Error surfaces honest fallback without raw transport text', async () => {
    trainPlanetProfession.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<ProfessionsPanel planetId="planet-1" citadelLevel={3} />);
    });
    await act(async () => {
      await flush();
    });

    const trainBtn = Array.from(container.querySelectorAll('button')).find((btn) =>
      btn.textContent?.includes('Queue training'),
    );
    expect(trainBtn).toBeTruthy();

    await act(async () => {
      trainBtn!.click();
      await flush();
    });

    expect(container.textContent).toContain(TRAIN_FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});

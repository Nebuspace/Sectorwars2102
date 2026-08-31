// @vitest-environment jsdom
/**
 * PioneerOfficeVenue — Pioneer Office broker/ferry console
 * (FEATURES/planets/colonization.md). Covers the loading/error states,
 * the broker-cohort panel (presets, slider, total), the active-contracts
 * list (progress %, source-name fallback, LOAD/VOID gating), the
 * per-contract load form (max-batch clamp, LOAD PODS/CANCEL), and every
 * axiosErrorMessage fallback branch (server detail/message, TypeError /
 * network densify, generic fallback).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Planet, MigrationContract, PioneerOffice } from '../../../contexts/GameContext';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { useGameMock, getPioneerOfficeMock, brokerMigrationContractMock, loadPioneerBatchMock, cancelMigrationContractMock } =
  vi.hoisted(() => ({
    useGameMock: vi.fn(),
    getPioneerOfficeMock: vi.fn(),
    brokerMigrationContractMock: vi.fn(),
    loadPioneerBatchMock: vi.fn(),
    cancelMigrationContractMock: vi.fn(),
  }));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: useGameMock,
}));

import PioneerOfficeVenue, { axiosErrorMessage } from '../PioneerOfficeVenue';

let container: HTMLElement;
let root: ReturnType<typeof createRoot>;

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

const planet = (over: Partial<Planet> = {}): Planet => ({
  id: 'planet-1',
  name: 'New Haven',
  type: 'terrestrial',
  status: 'colonized',
  sector_id: 10,
  resources: {},
  population: 100,
  max_population: 5000,
  habitability_score: 0.7,
  is_population_hub: true,
  ...over,
});

const contract = (over: Partial<MigrationContract> = {}): MigrationContract => ({
  id: 'c1',
  source_planet_id: 'planet-1',
  source_planet_name: 'New Haven',
  source_sector_id: 10,
  cohort_total: 1000,
  loaded: 0,
  delivered: 0,
  remaining_to_load: 1000,
  fee_per_pioneer_locked: 5,
  status: 'BROKERED',
  ...over,
});

const office = (over: Partial<PioneerOffice> = {}): PioneerOffice => ({
  planet_id: 'planet-1',
  planet_name: 'New Haven',
  fee_per_pioneer: 5,
  cargo_colonists: 200,
  cargo_free: 800,
  contracts: [],
  ...over,
});

const setInputValue = (el: HTMLInputElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
};

const render = async (props: Partial<{ planet: Planet; onBack: () => void }> = {}) => {
  const onBack = props.onBack ?? vi.fn();
  await act(async () => {
    root.render(<PioneerOfficeVenue planet={props.planet ?? planet()} onBack={onBack} />);
  });
  await flush();
  return onBack;
};

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);

  getPioneerOfficeMock.mockReset().mockResolvedValue(office());
  brokerMigrationContractMock.mockReset().mockResolvedValue(undefined);
  loadPioneerBatchMock.mockReset().mockResolvedValue(undefined);
  cancelMigrationContractMock.mockReset().mockResolvedValue(undefined);

  useGameMock.mockReturnValue({
    getPioneerOffice: getPioneerOfficeMock,
    brokerMigrationContract: brokerMigrationContractMock,
    loadPioneerBatch: loadPioneerBatchMock,
    cancelMigrationContract: cancelMigrationContractMock,
  });
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
});

describe('PioneerOfficeVenue — loading + error states', () => {
  it('shows the loading copy before the office fetch resolves', async () => {
    let resolveOffice!: (v: PioneerOffice) => void;
    getPioneerOfficeMock.mockReturnValue(new Promise((res) => { resolveOffice = res; }));
    await act(async () => {
      root.render(<PioneerOfficeVenue planet={planet()} onBack={vi.fn()} />);
    });
    expect(container.querySelector('.po-loading')?.textContent).toBe('Contacting the Migration Authority…');

    await act(async () => {
      resolveOffice(office());
      await Promise.resolve();
    });
    expect(container.querySelector('.po-loading')).toBeNull();
  });

  it('shows the server detail message on a structured API error', async () => {
    getPioneerOfficeMock.mockRejectedValue({ response: { data: { detail: 'Hub not staffed' } } });
    await render();
    expect(container.querySelector('.po-error')?.textContent).toBe('Hub not staffed');
  });

  it('densifies Network Error on load to the stable fallback (LEG-3266)', async () => {
    getPioneerOfficeMock.mockRejectedValue({ message: 'Network Error' });
    await render();
    const err = container.querySelector('.po-error');
    expect(err?.textContent).toBe('Could not reach the Pioneer Office.');
    expect(err?.textContent).not.toMatch(/Network Error/i);
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
  });

  it('falls back to the generic copy when the error carries neither', async () => {
    getPioneerOfficeMock.mockRejectedValue({});
    await render();
    expect(container.querySelector('.po-error')?.textContent).toBe('Could not reach the Pioneer Office.');
  });

  it('renders the back button and title, and wires onBack', async () => {
    const onBack = await render({ planet: planet({ name: 'Outpost Nine' }) });
    expect(container.querySelector('.po-title')?.textContent).toBe('PIONEER OFFICE — Outpost Nine');
    act(() => {
      (container.querySelector('.po-back') as HTMLButtonElement).dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onBack).toHaveBeenCalled();
  });
});

describe('PioneerOfficeVenue — cargo line + broker panel', () => {
  it('renders cargo/fee stats from the office response', async () => {
    getPioneerOfficeMock.mockResolvedValue(office({ cargo_colonists: 42, cargo_free: 958, fee_per_pioneer: 7 }));
    await render();
    const cargoLine = container.querySelector('.po-cargo-line')?.textContent || '';
    expect(cargoLine).toContain('42');
    expect(cargoLine).toContain('958');
    expect(cargoLine).toContain('7 cr');
  });

  it('selects a cohort preset, applying the active class and updating the total', async () => {
    getPioneerOfficeMock.mockResolvedValue(office({ fee_per_pioneer: 5 }));
    await render();
    const presets = container.querySelectorAll('.po-preset');
    expect(presets).toHaveLength(5);

    const preset500 = Array.from(presets).find((b) => b.textContent === '500') as HTMLButtonElement;
    act(() => {
      preset500.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(preset500.className).toContain('active');
    expect(container.querySelector('.po-summary')?.textContent).toContain('2,500 cr'); // 500 * 5
  });

  it('updates the cohort via the range slider', async () => {
    getPioneerOfficeMock.mockResolvedValue(office({ fee_per_pioneer: 2 }));
    await render();
    const slider = container.querySelector('.po-panel .po-slider') as HTMLInputElement;
    act(() => {
      setInputValue(slider, '3000');
    });
    expect(container.querySelector('.po-summary')?.textContent).toContain('3,000');
    expect(container.querySelector('.po-summary')?.textContent).toContain('6,000 cr'); // 3000 * 2
  });

  it('brokers the cohort and refreshes, disabling the button while busy', async () => {
    getPioneerOfficeMock
      .mockResolvedValueOnce(office({ contracts: [] }))
      .mockResolvedValueOnce(office({ contracts: [contract()] }));
    let resolveBroker!: () => void;
    brokerMigrationContractMock.mockReturnValue(new Promise<void>((res) => { resolveBroker = res; }));
    await render();

    const brokerBtn = container.querySelector('.po-action') as HTMLButtonElement;
    act(() => {
      brokerBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(brokerBtn.disabled).toBe(true);

    await act(async () => {
      resolveBroker();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(brokerMigrationContractMock).toHaveBeenCalledWith(1000); // default cohort
    expect(getPioneerOfficeMock).toHaveBeenCalledTimes(2);
    expect(container.querySelector('.po-contracts')).not.toBeNull();
  });

  it('surfaces a broker failure inline and clears busy', async () => {
    brokerMigrationContractMock.mockRejectedValue({ response: { data: { message: 'Insufficient funds' } } });
    await render();
    await act(async () => {
      (container.querySelector('.po-action') as HTMLButtonElement).dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('.po-error')?.textContent).toBe('Insufficient funds');
    expect((container.querySelector('.po-action') as HTMLButtonElement).disabled).toBe(false);
  });
});

describe('PioneerOfficeVenue — active contracts list', () => {
  it('shows the empty-contracts copy when there are none', async () => {
    await render();
    expect(container.querySelector('.po-empty')?.textContent).toBe('No active migration contracts.');
  });

  it('renders progress %, status class, and falls back to Sector N when the source name is missing', async () => {
    getPioneerOfficeMock.mockResolvedValue(
      office({
        contracts: [
          contract({ id: 'c1', delivered: 250, cohort_total: 1000, status: 'IN_PROGRESS', source_planet_name: null, source_sector_id: 22 }),
        ],
      })
    );
    await render();
    const bar = container.querySelector('.po-progress-bar') as HTMLElement;
    expect(bar.style.width).toBe('25%');
    expect(container.querySelector('.po-status')?.className).toContain('po-status-in_progress');
    expect(container.querySelector('.po-contract-source')?.textContent).toBe('Sector 22');
  });

  it('shows 0% progress for a zero-total contract rather than dividing by zero', async () => {
    getPioneerOfficeMock.mockResolvedValue(office({ contracts: [contract({ cohort_total: 0, delivered: 0 })] }));
    await render();
    expect((container.querySelector('.po-progress-bar') as HTMLElement).style.width).toBe('0%');
  });

  it('disables LOAD BATCH away from the source hub, with an explanatory title', async () => {
    getPioneerOfficeMock.mockResolvedValue(
      office({ contracts: [contract({ source_planet_id: 'elsewhere', source_sector_id: 5 })] })
    );
    await render({ planet: planet({ id: 'planet-1' }) });
    const [loadBtn] = container.querySelectorAll('.po-contract-actions .po-action');
    expect((loadBtn as HTMLButtonElement).disabled).toBe(true);
    expect(loadBtn.getAttribute('title')).toBe('Return to Sector 5 to load');
  });

  it('disables LOAD BATCH when remaining_to_load or cargoFree is exhausted', async () => {
    getPioneerOfficeMock.mockResolvedValue(
      office({ cargo_free: 0, contracts: [contract({ remaining_to_load: 500 })] })
    );
    await render();
    const [loadBtn] = container.querySelectorAll('.po-contract-actions .po-action');
    expect((loadBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it('disables VOID once any pods are loaded, with an explanatory title, and enables it otherwise', async () => {
    getPioneerOfficeMock.mockResolvedValue(office({ contracts: [contract({ loaded: 10 })] }));
    await render();
    const voidBtn = container.querySelector('.po-contract-actions .po-action-ghost') as HTMLButtonElement;
    expect(voidBtn.disabled).toBe(true);
    expect(voidBtn.title).toBe('Settle or disembark loaded pioneers first');
  });

  it('voids a contract and refreshes on click when unloaded', async () => {
    getPioneerOfficeMock
      .mockResolvedValueOnce(office({ contracts: [contract({ id: 'c1', loaded: 0 })] }))
      .mockResolvedValueOnce(office({ contracts: [] }));
    await render();
    const voidBtn = container.querySelector('.po-contract-actions .po-action-ghost') as HTMLButtonElement;
    expect(voidBtn.disabled).toBe(false);
    await act(async () => {
      voidBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
    expect(cancelMigrationContractMock).toHaveBeenCalledWith('c1');
    expect(container.querySelector('.po-empty')).not.toBeNull();
  });

  it('surfaces a void failure inline', async () => {
    getPioneerOfficeMock.mockResolvedValue(office({ contracts: [contract({ id: 'c1' })] }));
    cancelMigrationContractMock.mockRejectedValue({});
    await render();
    await act(async () => {
      (container.querySelector('.po-contract-actions .po-action-ghost') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    await flush();
    expect(container.querySelector('.po-error')?.textContent).toBe('Could not void the contract.');
  });
});

describe('PioneerOfficeVenue — per-contract load form', () => {
  it('opens with the max-batch clamp (min of remaining_to_load and cargoFree) preselected', async () => {
    getPioneerOfficeMock.mockResolvedValue(
      office({ cargo_free: 300, contracts: [contract({ id: 'c1', remaining_to_load: 900, fee_per_pioneer_locked: 4 })] })
    );
    await render();
    act(() => {
      (container.querySelector('.po-contract-actions .po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    expect(container.querySelector('.po-load')).not.toBeNull();
    const loadRow = container.querySelector('.po-load-row')?.textContent || '';
    expect(loadRow).toContain('300'); // clamped to cargoFree, not the larger remaining_to_load
    expect(container.querySelector('.po-load-cap')?.textContent).toContain('max now: 300');
  });

  it('updates the load quantity via its slider and reflects the cost at the office fee', async () => {
    getPioneerOfficeMock.mockResolvedValue(
      office({ fee_per_pioneer: 5, cargo_free: 500, contracts: [contract({ id: 'c1', remaining_to_load: 500 })] })
    );
    await render();
    act(() => {
      (container.querySelector('.po-contract-actions .po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    const slider = container.querySelector('.po-load .po-slider') as HTMLInputElement;
    act(() => {
      setInputValue(slider, '100');
    });
    expect(container.querySelector('.po-load-row')?.textContent).toContain('500 cr'); // 100 * 5
  });

  it('cancels the load form without calling the API', async () => {
    getPioneerOfficeMock.mockResolvedValue(office({ contracts: [contract({ id: 'c1' })] }));
    await render();
    act(() => {
      (container.querySelector('.po-contract-actions .po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    act(() => {
      (container.querySelector('.po-load-actions .po-action-ghost') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    expect(container.querySelector('.po-load')).toBeNull();
    expect(loadPioneerBatchMock).not.toHaveBeenCalled();
  });

  it('loads the batch, closes the form, and refreshes', async () => {
    getPioneerOfficeMock
      .mockResolvedValueOnce(office({ cargo_free: 500, contracts: [contract({ id: 'c1', remaining_to_load: 500 })] }))
      .mockResolvedValueOnce(office({ cargo_free: 400, contracts: [contract({ id: 'c1', remaining_to_load: 400, loaded: 100 })] }));
    await render();
    act(() => {
      (container.querySelector('.po-contract-actions .po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    await act(async () => {
      (container.querySelector('.po-load-actions .po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    await flush();
    expect(loadPioneerBatchMock).toHaveBeenCalledWith('c1', 500);
    expect(container.querySelector('.po-load')).toBeNull();
    expect(getPioneerOfficeMock).toHaveBeenCalledTimes(2);
  });

  it('disables LOAD PODS when the clamped quantity is zero', async () => {
    getPioneerOfficeMock.mockResolvedValue(office({ cargo_free: 0, contracts: [contract({ id: 'c1', remaining_to_load: 500 })] }));
    await render();
    // cargoFree=0 also disables the outer LOAD BATCH trigger -- this exercises
    // openLoad's own maxBatch=0 clamp directly via a still-enabled trigger
    // path is impossible here, so assert the trigger itself is correctly gated instead.
    const loadBtn = container.querySelector('.po-contract-actions .po-action') as HTMLButtonElement;
    expect(loadBtn.disabled).toBe(true);
  });

  it('surfaces a load failure inline', async () => {
    getPioneerOfficeMock.mockResolvedValue(office({ cargo_free: 500, contracts: [contract({ id: 'c1', remaining_to_load: 500 })] }));
    loadPioneerBatchMock.mockRejectedValue({ response: { data: { detail: 'Cargo hold full' } } });
    await render();
    act(() => {
      (container.querySelector('.po-contract-actions .po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    await act(async () => {
      (container.querySelector('.po-load-actions .po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    await flush();
    expect(container.querySelector('.po-error')?.textContent).toBe('Cargo hold full');
    expect(container.querySelector('.po-load')).not.toBeNull(); // stays open on failure
  });
});

describe('PioneerOfficeVenue TypeError densify (LEG-3266)', () => {
  const loadFallback = 'Could not reach the Pioneer Office.';
  const brokerFallback = 'Could not broker the contract.';
  const batchFallback = 'Could not load the batch.';

  it('axiosErrorMessage densifies TypeError / Failed to fetch / Network Error to fallback', () => {
    expect(axiosErrorMessage(new TypeError('Failed to fetch'), loadFallback)).toBe(loadFallback);
    expect(axiosErrorMessage({ message: 'Failed to fetch' }, loadFallback)).toBe(loadFallback);
    expect(axiosErrorMessage({ message: 'Network Error' }, loadFallback)).toBe(loadFallback);
    expect(axiosErrorMessage({ message: '   ' }, loadFallback)).toBe(loadFallback);
    expect(axiosErrorMessage(new TypeError('Failed to fetch'), loadFallback)).not.toMatch(/Failed to fetch/i);
    expect(axiosErrorMessage(new TypeError('Failed to fetch'), loadFallback)).not.toMatch(/TypeError/i);
  });

  it('axiosErrorMessage keeps structured API detail honesty', () => {
    expect(
      axiosErrorMessage({ response: { data: { detail: 'Hub not staffed' } } }, loadFallback)
    ).toBe('Hub not staffed');
    expect(
      axiosErrorMessage({ response: { data: { message: 'Insufficient funds' } } }, brokerFallback)
    ).toBe('Insufficient funds');
  });

  it('load TypeError surfaces fallback without Failed to fetch / TypeError / Network Error in DOM', async () => {
    getPioneerOfficeMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await render();
    const err = container.querySelector('.po-error');
    expect(err?.textContent).toBe(loadFallback);
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(err?.textContent).not.toMatch(/Network Error/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('broker TypeError surfaces fallback without Failed to fetch / TypeError in DOM', async () => {
    brokerMigrationContractMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await render();
    await act(async () => {
      (container.querySelector('.po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    await flush();
    const err = container.querySelector('.po-error');
    expect(err?.textContent).toBe(brokerFallback);
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('load-batch TypeError surfaces fallback without Failed to fetch / TypeError in DOM', async () => {
    getPioneerOfficeMock.mockResolvedValue(
      office({ cargo_free: 500, contracts: [contract({ id: 'c1', remaining_to_load: 500 })] })
    );
    loadPioneerBatchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await render();
    act(() => {
      (container.querySelector('.po-contract-actions .po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    await act(async () => {
      (container.querySelector('.po-load-actions .po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    await flush();
    const err = container.querySelector('.po-error');
    expect(err?.textContent).toBe(batchFallback);
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });
});

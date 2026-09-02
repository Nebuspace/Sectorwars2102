// @vitest-environment jsdom
/**
 * LEG-3760 Soft-ORDER — PioneerOfficeVenue TypeError/network densify.
 * LEG-4060 Soft-ORDER — HTTP 403/429 densify (invent=0).
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

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

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

const render = async () => {
  await act(async () => {
    root.render(<PioneerOfficeVenue planet={planet()} onBack={vi.fn()} />);
  });
  await flush();
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

describe('PioneerOfficeVenue TypeError densify (LEG-3760)', () => {
  const loadFallback = 'Could not reach the Pioneer Office.';
  const brokerFallback = 'Could not broker the contract.';
  const batchFallback = 'Could not load the batch.';
  const cancelFallback = 'Could not void the contract.';

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
      axiosErrorMessage({ response: { data: { detail: 'Hub not staffed' } } }, loadFallback),
    ).toBe('Hub not staffed');
    expect(
      axiosErrorMessage({ response: { data: { message: 'Insufficient funds' } } }, brokerFallback),
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
        new MouseEvent('click', { bubbles: true }),
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
      office({ cargo_free: 500, contracts: [contract({ id: 'c1', remaining_to_load: 500 })] }),
    );
    loadPioneerBatchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await render();
    act(() => {
      (container.querySelector('.po-contract-actions .po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
    });
    await act(async () => {
      (container.querySelector('.po-load-actions .po-action') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
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

  it('cancel TypeError surfaces fallback without Failed to fetch / TypeError in DOM', async () => {
    getPioneerOfficeMock.mockResolvedValue(office({ contracts: [contract()] }));
    cancelMigrationContractMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await render();
    await act(async () => {
      (container.querySelector('.po-contract-actions .po-action-ghost') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
    });
    await flush();
    const err = container.querySelector('.po-error');
    expect(err?.textContent).toBe(cancelFallback);
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });
});

describe('axiosErrorMessage 403/429 densify (LEG-4060)', () => {
  const loadFallback = 'Could not reach the Pioneer Office.';
  it('maps 403/429 without raw transport leakage', () => {
    expect(axiosErrorMessage(apiRequestError(403), loadFallback)).toBe(
      'Access denied — you cannot use the Pioneer Office right now.',
    );
    expect(axiosErrorMessage(apiRequestError(403, 'office_denied'), loadFallback)).toBe('office_denied');
    expect(axiosErrorMessage(apiRequestError(429), loadFallback)).toMatch(/rate limit/i);
    expect(axiosErrorMessage(apiRequestError(429), loadFallback)).not.toMatch(/\b429\b/);
    expect(axiosErrorMessage(apiRequestError(403), loadFallback)).not.toMatch(/API Error/i);
    expect(axiosErrorMessage(apiRequestError(403), loadFallback)).not.toMatch(/TypeError/i);
  });
});

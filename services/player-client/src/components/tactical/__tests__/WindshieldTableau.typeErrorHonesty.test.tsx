// @vitest-environment jsdom
/**
 * LEG-3769 Soft-ORDER — WindshieldTableau sector load typeErrorHonesty.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetContents = vi.fn();
const mockGetPose = vi.fn();
const mockBurn = vi.fn();
const mockHalt = vi.fn();

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    sectorAPI: {
      ...actual.sectorAPI,
      getContents: (...args: unknown[]) => mockGetContents(...args),
    },
    helmAPI: {
      getPose: (...args: unknown[]) => mockGetPose(...args),
      burn: (...args: unknown[]) => mockBurn(...args),
      halt: (...args: unknown[]) => mockHalt(...args),
    },
  };
});

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({ status: 'idle', abort: vi.fn() }),
}));

import WindshieldTableau from '../WindshieldTableau';
import { WindshieldFlightProvider } from '../../../contexts/WindshieldFlightContext';

const SECTOR_ID = 77;
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const assertNoTransportLeak = (text: string) => {
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/Network Error/i);
  expect(text).not.toMatch(/\b403\b/);
  expect(text).not.toMatch(/\b429\b/);
  expect(text).not.toMatch(/HTTP 429/i);
};

describe('WindshieldTableau load transport collapse densify (LEG-3769)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetPose.mockRejectedValue(new Error('no pose mock in this suite'));
    mockBurn.mockResolvedValue(undefined);
    mockHalt.mockResolvedValue(undefined);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const mountWithContentsError = async (err: unknown) => {
    mockGetContents.mockRejectedValue(err);
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    await act(async () => {
      root.render(
        <WindshieldFlightProvider>
          <WindshieldTableau sectorId={SECTOR_ID} />
        </WindshieldFlightProvider>,
      );
    });
    await act(async () => {
      await flush();
    });
    errSpy.mockRestore();
  };

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('sector contents %s surfaces acquisition-failed fallback without raw transport text', async (_label, err) => {
    await mountWithContentsError(err);
    expect(container.textContent).toContain('SCAN ACQUISITION FAILED');
    assertNoTransportLeak(container.textContent ?? '');
  });

  it.each([
    ['HTTP 403', apiRequestError(403)],
    ['HTTP 429', apiRequestError(429)],
  ])('sector contents %s surfaces acquisition-failed fallback without raw status text (LEG-3962)', async (_label, err) => {
    await mountWithContentsError(err);
    expect(container.textContent).toContain('SCAN ACQUISITION FAILED');
    assertNoTransportLeak(container.textContent ?? '');
  });

  it.each([
    ['HTTP 403', apiRequestError(403)],
    ['HTTP 429', apiRequestError(429)],
  ])('helm pose %s stays silent without raw status text when sector contents load (LEG-3962)', async (_label, err) => {
    mockGetContents.mockResolvedValue({
      bodies: [],
      stations: [],
      engage_range_em: 50,
    });
    mockGetPose.mockRejectedValue(err);
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    await act(async () => {
      root.render(
        <WindshieldFlightProvider>
          <WindshieldTableau sectorId={SECTOR_ID} />
        </WindshieldFlightProvider>,
      );
    });
    await act(async () => {
      await flush();
    });
    errSpy.mockRestore();
    assertNoTransportLeak(container.textContent ?? '');
    expect(container.textContent).not.toContain('SCAN ACQUISITION FAILED');
  });
});

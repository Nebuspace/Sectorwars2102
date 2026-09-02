// @vitest-environment jsdom
/**
 * LEG-3748 Soft-ORDER — SolarSystemViewscreen rename + sector load TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SolarSystemViewscreen, { formatPlanetRenameError } from '../SolarSystemViewscreen';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetSystem = vi.fn();
const mockSetName = vi.fn();

vi.mock('../../../contexts/SettingsContext', () => ({
  useSettings: () => ({ uiScale: 1 }),
}));

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    sectorAPI: {
      ...actual.sectorAPI,
      getSystem: (...args: unknown[]) => mockGetSystem(...args),
    },
    planetaryAPI: {
      ...actual.planetaryAPI,
      setName: (...args: unknown[]) => mockSetName(...args),
    },
  };
});

const SECTOR_ID = 42;
const W = 800;
const H = 400;
const TEST_SHIP = { ship_id: 'ship-alpha', ship_name: 'Alpha Runner', ship_type: 'SCOUT', is_npc: false };

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function makeNoopCtx(): CanvasRenderingContext2D {
  const store: Record<string, unknown> = {};
  return new Proxy(store, {
    get(target, prop) {
      if (prop === 'measureText') return () => ({ width: 10 });
      if (prop === 'createRadialGradient' || prop === 'createLinearGradient') {
        return () => ({ addColorStop: () => {} });
      }
      if (prop in target) return target[prop as string];
      return () => {};
    },
    set(target, prop, value) {
      target[prop as string] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
}

describe('formatPlanetRenameError TypeError densify (LEG-3748)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatPlanetRenameError(new TypeError('Failed to fetch'));
    expect(text).toBe('Rename failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(formatPlanetRenameError(new Error('Network Error'))).toBe('Rename failed');
    expect(formatPlanetRenameError(new Error('Failed to fetch'))).toBe('Rename failed');
    expect(formatPlanetRenameError(new Error('   '))).toBe('Rename failed');
  });

  it('keeps axios response detail honesty', () => {
    expect(
      formatPlanetRenameError({ response: { data: { detail: 'Name already taken' } } }),
    ).toBe('Name already taken');
  });
});

describe('SolarSystemViewscreen sector load TypeError densify (LEG-3748)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mockGetSystem.mockReset();
    mockSetName.mockReset();

    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: true,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia;

    class MockResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = MockResizeObserver;

    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      width: W,
      height: H,
      top: 0,
      left: 0,
      right: W,
      bottom: H,
      x: 0,
      y: 0,
      toJSON() {
        return {};
      },
    } as DOMRect);

    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
      (() => makeNoopCtx()) as unknown as typeof HTMLCanvasElement.prototype.getContext,
    );

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    errorSpy.mockRestore();
    vi.restoreAllMocks();
  });

  it('TypeError on getSystem falls back to SectorViewport without raw transport strings', async () => {
    mockGetSystem.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(
        <SolarSystemViewscreen sectorId={SECTOR_ID} scene="flight" ships={[TEST_SHIP]} />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    expect(container.querySelector('canvas.sector-viewport-canvas')).toBeTruthy();
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('axios-style Network Error on getSystem falls back without leaking transport copy', async () => {
    mockGetSystem.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(
        <SolarSystemViewscreen sectorId={SECTOR_ID} scene="flight" ships={[TEST_SHIP]} />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    expect(container.querySelector('canvas.sector-viewport-canvas')).toBeTruthy();
    expect(container.textContent).not.toMatch(/Network Error/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('landed scene keeps canvas on getSystem TypeError (graceful degrade, no raw error in DOM)', async () => {
    mockGetSystem.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(
        <SolarSystemViewscreen
          sectorId={SECTOR_ID}
          scene="landed"
          ships={[TEST_SHIP]}
          planetType="terrestrial"
        />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    expect(container.querySelector('canvas.solar-viewscreen-canvas')).toBeTruthy();
    expect(container.querySelector('canvas.sector-viewport-canvas')).toBeFalsy();
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });
});


describe('formatPlanetRenameError 403/429 densify (LEG-4089)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatPlanetRenameError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatPlanetRenameError(apiRequestError(403, 'rename_denied'))).toBe('rename_denied');
    expect(formatPlanetRenameError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatPlanetRenameError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatPlanetRenameError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});

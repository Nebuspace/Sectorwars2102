// @vitest-environment jsdom
/**
 * TerraformingPanel — start money path (WO-TESTCOV-PLAYER-TERRAFORMING-START).
 * Level card Start → POST /api/v1/planets/:id/terraforming/start { target_level }.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../../services/api', () => ({
  terraformAPI: {
    confirmBiome: vi.fn(),
  },
}));

import TerraformingPanel from '../TerraformingPanel';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const LEVEL = {
  level: 2,
  name: 'Atmosphere Seed',
  creditCost: 5000,
  durationHours: 12,
  habitabilityBoost: 15,
  organicsCost: 100,
  equipmentCost: 50,
};

describe('TerraformingPanel — start money path', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'tok-test');
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes('/terraforming/status')) {
        return {
          ok: true,
          json: async () => ({
            active: false,
            currentHabitability: 40,
            availableLevels: { '2': LEVEL },
          }),
        };
      }
      if (u.includes('/terraforming/start') && init?.method === 'POST') {
        return { ok: true, json: async () => ({ active: true }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('posts terraforming/start when Start is clicked on a level card', async () => {
    await act(async () => {
      root.render(
        <TerraformingPanel
          planetId="planet-1"
          playerCredits={100_000}
          habitabilityScore={40}
          planetType="DESERT"
        />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.querySelector('.terraforming-btn.start-btn')).toBeTruthy();
    });

    const startBtn = container.querySelector('.terraforming-btn.start-btn') as HTMLButtonElement;
    expect(startBtn.disabled).toBe(false);

    await act(async () => {
      startBtn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      const post = fetchMock.mock.calls.find(
        (c) => String(c[0]).includes('/terraforming/start') && c[1]?.method === 'POST',
      );
      expect(post).toBeTruthy();
    });

    const call = fetchMock.mock.calls.find(
      (c) => String(c[0]).includes('/terraforming/start') && c[1]?.method === 'POST',
    )!;
    expect(String(call[0])).toContain('/api/v1/planets/planet-1/terraforming/start');
    const [, init] = call;
    expect(JSON.parse(init?.body as string)).toEqual({ target_level: 2 });
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok-test');
  });
});

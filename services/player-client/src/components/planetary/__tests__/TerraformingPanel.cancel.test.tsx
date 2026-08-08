// @vitest-environment jsdom
/**
 * TerraformingPanel — cancel refund money path (WO-TESTCOV-PLAYER-TERRAFORMING-CANCEL).
 * Cancel Project → POST /api/v1/planets/:id/terraforming/cancel (refundAmount surfaced).
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

describe('TerraformingPanel — cancel money path', () => {
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
            active: true,
            currentHabitability: 45,
            terraformingTarget: 60,
            progress: 20,
            estimatedCompletion: new Date(Date.now() + 3_600_000).toISOString(),
          }),
        };
      }
      if (u.includes('/terraforming/cancel') && init?.method === 'POST') {
        return { ok: true, json: async () => ({ refundAmount: 2500 }) };
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

  it('posts terraforming/cancel when Cancel Project is clicked', async () => {
    await act(async () => {
      root.render(
        <TerraformingPanel
          planetId="planet-1"
          playerCredits={100_000}
          habitabilityScore={45}
          planetType="DESERT"
        />,
      );
    });
    await act(async () => {
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.querySelector('.terraforming-btn.cancel-btn')).toBeTruthy();
    });

    const cancelBtn = container.querySelector('.terraforming-btn.cancel-btn') as HTMLButtonElement;
    expect(cancelBtn.textContent).toContain('Cancel Project');

    await act(async () => {
      cancelBtn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      const post = fetchMock.mock.calls.find(
        (c) => String(c[0]).includes('/terraforming/cancel') && c[1]?.method === 'POST',
      );
      expect(post).toBeTruthy();
    });

    const call = fetchMock.mock.calls.find(
      (c) => String(c[0]).includes('/terraforming/cancel') && c[1]?.method === 'POST',
    )!;
    expect(String(call[0])).toContain('/api/v1/planets/planet-1/terraforming/cancel');
    expect((call[1]?.headers as Record<string, string>).Authorization).toBe('Bearer tok-test');
    expect(container.textContent).toMatch(/2,?500 credits refunded/);
  });
});

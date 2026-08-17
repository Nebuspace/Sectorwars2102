// @vitest-environment jsdom
/**
 * LandingRightsControl — LEG-155 owner ACL UI.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const setLandingRights = vi.fn();

vi.mock('../../../services/api', () => ({
  planetaryAPI: {
    setLandingRights: (...args: unknown[]) => setLandingRights(...args),
  },
}));

import LandingRightsControl from '../LandingRightsControl';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('LandingRightsControl', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    setLandingRights.mockReset();
    setLandingRights.mockResolvedValue({
      success: true,
      message: "Landing rights for Test set to 'team_only'.",
      planet_id: 'planet-1',
      mode: 'team_only',
      whitelist: [],
      denylist: [],
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('shows the control for an owner', async () => {
    await act(async () => {
      root.render(
        <LandingRightsControl planetId="planet-1" isOwned initialMode="public" />,
      );
    });

    expect(container.querySelector('[data-testid="landing-rights-control"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="landing-rights-select"]')).toBeTruthy();
  });

  it('omits the control for a non-owner', async () => {
    await act(async () => {
      root.render(
        <LandingRightsControl planetId="planet-1" isOwned={false} initialMode="public" />,
      );
    });

    expect(container.querySelector('[data-testid="landing-rights-control"]')).toBeNull();
  });

  it('PUTs the selected simple mode with empty list arrays', async () => {
    await act(async () => {
      root.render(
        <LandingRightsControl planetId="planet-1" isOwned initialMode="public" />,
      );
    });

    const select = container.querySelector(
      '[data-testid="landing-rights-select"]',
    ) as HTMLSelectElement;
    expect(select).toBeTruthy();

    await act(async () => {
      select.value = 'team_only';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await flush();
    });

    expect(setLandingRights).toHaveBeenCalledTimes(1);
    expect(setLandingRights).toHaveBeenCalledWith('planet-1', {
      mode: 'team_only',
      whitelist: [],
      denylist: [],
    });
  });

  it('PUTs public → private (Accept minimum mode flip)', async () => {
    setLandingRights.mockResolvedValue({
      success: true,
      message: "Landing rights for Test set to 'private'.",
      planet_id: 'planet-1',
      mode: 'private',
      whitelist: [],
      denylist: [],
    });

    await act(async () => {
      root.render(
        <LandingRightsControl planetId="planet-1" isOwned initialMode="public" />,
      );
    });

    const select = container.querySelector(
      '[data-testid="landing-rights-select"]',
    ) as HTMLSelectElement;

    await act(async () => {
      select.value = 'private';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await flush();
    });

    expect(setLandingRights).toHaveBeenCalledWith('planet-1', {
      mode: 'private',
      whitelist: [],
      denylist: [],
    });
  });

  it('disables whitelist and denylist options with honest residual copy', async () => {
    await act(async () => {
      root.render(
        <LandingRightsControl planetId="planet-1" isOwned initialMode="private" />,
      );
    });

    const select = container.querySelector(
      '[data-testid="landing-rights-select"]',
    ) as HTMLSelectElement;
    const whitelist = Array.from(select.options).find((o) => o.value === 'whitelist');
    const denylist = Array.from(select.options).find((o) => o.value === 'denylist');
    expect(whitelist?.disabled).toBe(true);
    expect(denylist?.disabled).toBe(true);
    expect(container.querySelector('[data-testid="landing-rights-list-residual"]')?.textContent)
      .toMatch(/UUID list editor/i);
  });
});

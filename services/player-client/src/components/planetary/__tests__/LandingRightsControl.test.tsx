// @vitest-environment jsdom
/**
 * LandingRightsControl — LEG-155 + LEG-INI-31 owner ACL UI.
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

import LandingRightsControl, {
  formatLandingRightsError,
  parseUuidList,
} from '../LandingRightsControl';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const UUID_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const UUID_B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

describe('formatLandingRightsError (LEG-2952)', () => {
  it('preserves gameserver landing-rights refusal detail', () => {
    const err = Object.assign(new Error('Only the planet owner may change landing rights.'), {
      status: 403,
    });
    expect(formatLandingRightsError(err)).toBe(
      'Only the planet owner may change landing rights.',
    );
  });

  it('falls back when message is bare API Error: 403', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatLandingRightsError(err)).toBe(
      'You do not have permission to change landing rights.',
    );
  });

  it('falls back on bare API Error: 429', () => {
    const err = Object.assign(new Error('API Error: 429'), { status: 429 });
    expect(formatLandingRightsError(err)).toMatch(/rate limit exceeded/i);
  });

  it('falls back on TypeError network collapse (LEG-3035)', () => {
    const text = formatLandingRightsError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to update landing rights/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});

describe('parseUuidList', () => {
  it('accepts line- and comma-separated UUIDs and drops empties', () => {
    const { ok, bad } = parseUuidList(`${UUID_A}\n${UUID_B}, ${UUID_A}`);
    expect(ok).toEqual([UUID_A, UUID_B]);
    expect(bad).toEqual([]);
  });

  it('flags invalid tokens', () => {
    const { ok, bad } = parseUuidList(`${UUID_A}\nnot-a-uuid`);
    expect(ok).toEqual([UUID_A]);
    expect(bad).toEqual(['not-a-uuid']);
  });
});

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

  it('shows UUID list editor when whitelist is selected (LEG-INI-31)', async () => {
    await act(async () => {
      root.render(
        <LandingRightsControl planetId="planet-1" isOwned initialMode="public" />,
      );
    });

    const select = container.querySelector(
      '[data-testid="landing-rights-select"]',
    ) as HTMLSelectElement;

    await act(async () => {
      select.value = 'whitelist';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await flush();
    });

    expect(setLandingRights).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="landing-rights-list-editor"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="landing-rights-list-input"]')).toBeTruthy();
  });

  it('enables whitelist mode and PUTs UUID array (LEG-INI-31)', async () => {
    setLandingRights.mockResolvedValue({
      success: true,
      message: "Landing rights set to 'whitelist'.",
      planet_id: 'planet-1',
      mode: 'whitelist',
      whitelist: [UUID_A, UUID_B],
      denylist: [],
    });

    await act(async () => {
      root.render(
        <LandingRightsControl
          planetId="planet-1"
          isOwned
          initialMode="whitelist"
          initialWhitelist={[UUID_A, UUID_B]}
        />,
      );
    });

    expect(container.querySelector('[data-testid="landing-rights-list-editor"]')).toBeTruthy();

    const apply = container.querySelector(
      '[data-testid="landing-rights-apply-list"]',
    ) as HTMLButtonElement;
    await act(async () => {
      apply.click();
      await flush();
    });

    expect(setLandingRights).toHaveBeenCalledWith('planet-1', {
      mode: 'whitelist',
      whitelist: [UUID_A, UUID_B],
      denylist: [],
    });
  });

  it('enables denylist mode and PUTs UUID array (LEG-INI-31)', async () => {
    setLandingRights.mockResolvedValue({
      success: true,
      message: "Landing rights set to 'denylist'.",
      planet_id: 'planet-1',
      mode: 'denylist',
      whitelist: [],
      denylist: [UUID_A],
    });

    await act(async () => {
      root.render(
        <LandingRightsControl
          planetId="planet-1"
          isOwned
          initialMode="denylist"
          initialDenylist={[UUID_A]}
        />,
      );
    });

    const apply = container.querySelector(
      '[data-testid="landing-rights-apply-list"]',
    ) as HTMLButtonElement;
    await act(async () => {
      apply.click();
      await flush();
    });

    expect(setLandingRights).toHaveBeenCalledWith('planet-1', {
      mode: 'denylist',
      whitelist: [],
      denylist: [UUID_A],
    });
  });

  it('surfaces PUT 403 owner-denial detail in landing-rights-error', async () => {
    setLandingRights.mockRejectedValue(
      apiRequestError(403, 'Only the planet owner may change landing rights.'),
    );

    await act(async () => {
      root.render(
        <LandingRightsControl planetId="planet-1" isOwned initialMode="public" />,
      );
    });

    const select = container.querySelector(
      '[data-testid="landing-rights-select"]',
    ) as HTMLSelectElement;

    await act(async () => {
      select.value = 'team_only';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await flush();
    });

    const alert = container.querySelector('[data-testid="landing-rights-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toContain('Only the planet owner may change landing rights.');
  });

  it('surfaces PUT 429 rate-limit detail in landing-rights-error', async () => {
    setLandingRights.mockRejectedValue(
      apiRequestError(429, 'Rate limit exceeded — try again shortly.'),
    );

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

    const alert = container.querySelector('[data-testid="landing-rights-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toMatch(/rate limit exceeded/i);
  });

  it('rejects empty whitelist without calling the API', async () => {
    await act(async () => {
      root.render(
        <LandingRightsControl planetId="planet-1" isOwned initialMode="whitelist" />,
      );
    });

    const apply = container.querySelector(
      '[data-testid="landing-rights-apply-list"]',
    ) as HTMLButtonElement;
    await act(async () => {
      apply.click();
      await flush();
    });

    expect(setLandingRights).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="landing-rights-error"]')?.textContent).toMatch(
      /at least one/i,
    );
  });
});

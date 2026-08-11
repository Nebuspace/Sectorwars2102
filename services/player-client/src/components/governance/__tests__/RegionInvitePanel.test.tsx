// @vitest-environment jsdom
/**
 * RegionInvitePanel — the region owner's invite-mint/list/revoke console
 * (WO-IL4). jsdom + react-dom/client createRoot() + act(), no RTL. Fake
 * timers (vi.useFakeTimers) pin Date.now() for the create-flow's computed
 * expires_at and drive the 1s countdown tick + the 1.8s copy-flash timeout
 * deterministically. jsdom has neither navigator.clipboard nor
 * document.execCommand -- both stubbed (the latter only for the dedicated
 * fallback-path test, to keep the primary-path tests on the real branch).
 *
 * Pins: friendlyError's ERR_* → owner-readable copy mapping, effectiveStatus
 * client-side TTL (an 'active' invite whose expires_at has already passed
 * reads as expired without a server round-trip), the ticking relative-expiry
 * countdown, the max-uses stepper's clamp at [1,10], the create flow's
 * optimistic prepend + minted spotlight + refetch, revoke's 2-click arm/
 * confirm/keep with a row-scoped error on failure, and the clipboard
 * copy-then-fallback paths.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockListInvites, mockCreateInvite, mockRevokeInvite } = vi.hoisted(() => ({
  mockListInvites: vi.fn(),
  mockCreateInvite: vi.fn(),
  mockRevokeInvite: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  regionOwnerAPI: { listInvites: mockListInvites, createInvite: mockCreateInvite, revokeInvite: mockRevokeInvite },
}));

import RegionInvitePanel from '../RegionInvitePanel';
import type { ComponentProps } from 'react';

const NOW = Date.parse('2026-08-09T12:00:00Z');

const invite = (overrides: Record<string, unknown> = {}) => ({
  id: 'inv-1',
  code: 'ABCD1234',
  region_id: 'region-1',
  max_uses: 1,
  uses: 0,
  status: 'active',
  expires_at: new Date(NOW + 3600_000).toISOString(), // 1h from now
  created_at: new Date(NOW).toISOString(),
  ...overrides,
});

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('RegionInvitePanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let writeTextMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);

    mockListInvites.mockReset();
    mockListInvites.mockResolvedValue({ invites: [] });
    mockCreateInvite.mockReset();
    mockRevokeInvite.mockReset();
    mockRevokeInvite.mockResolvedValue({});

    writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, 'clipboard', {
      value: { writeText: writeTextMock },
      configurable: true,
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
    vi.useRealTimers();
  });

  const mount = async (props: Partial<ComponentProps<typeof RegionInvitePanel>> = {}) => {
    await act(async () => {
      root.render(<RegionInvitePanel regionId="region-1" {...props} />);
    });
    await flush();
  };

  it('renders the header title and region name, and the intro copy', async () => {
    await mount({ regionName: 'The Fringe' });
    expect(container.querySelector('.ri-hud-title')?.textContent).toBe('REGION INVITE CONTROL');
    expect(container.querySelector('.ri-hud-sub')?.textContent).toBe('The Fringe');
    expect(container.querySelector('.ri-intro')).not.toBeNull();
  });

  it('falls back to "YOUR REGION" when regionName is omitted, and hides close when onClose is omitted', async () => {
    await mount();
    expect(container.querySelector('.ri-hud-sub')?.textContent).toBe('YOUR REGION');
    expect(container.querySelector('.ri-close')).toBeNull();
  });

  it('renders a close button that calls onClose when provided', async () => {
    const onClose = vi.fn();
    await mount({ onClose });
    await act(async () => {
      (container.querySelector('.ri-close') as HTMLButtonElement).click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows the loading message before the first list resolves', async () => {
    let resolveList: (v: unknown) => void = () => {};
    mockListInvites.mockReturnValue(new Promise((resolve) => { resolveList = resolve; }));
    await act(async () => {
      root.render(<RegionInvitePanel regionId="region-1" />);
    });
    expect(container.querySelector('.ri-state')?.textContent).toBe('Consulting the invite registry…');
    await act(async () => {
      resolveList({ invites: [] });
    });
    await flush();
    expect(container.querySelector('.ri-state')?.textContent).toContain('No invites yet');
  });

  it('maps a list-fetch error through friendlyError (ERR_NOT_REGION_OWNER)', async () => {
    mockListInvites.mockRejectedValue(new Error('ERR_NOT_REGION_OWNER'));
    await mount();
    expect(container.querySelector('.ri-validation-strip')?.textContent).toBe('You are not the owner of this region.');
  });

  it('shows the empty-list message when the region has no invites', async () => {
    await mount();
    expect(container.querySelector('.ri-state')?.textContent).toBe('No invites yet. Mint one above to seat a new citizen.');
  });

  describe('invite row rendering', () => {
    it('renders code, uses/max, status badge, and a live relative countdown for an active invite', async () => {
      mockListInvites.mockResolvedValue({ invites: [invite()] });
      await mount();
      const row = container.querySelector('.ri-invite-row') as HTMLElement;
      expect(row.querySelector('.ri-invite-code')?.textContent).toBe('ABCD1234');
      expect(row.querySelector('.ri-meta-value')?.textContent).toBe('0 / 1');
      expect(row.querySelector('.ri-badge')?.textContent).toBe('ACTIVE');
      expect(row.className).toContain('status-active');
      const expiryValues = row.querySelectorAll('.ri-meta-value');
      expect(expiryValues[1].textContent).toBe('1h 0m');
    });

    it('reads an active invite whose expiry has already passed as EXPIRED (client-side TTL), with no REVOKE button', async () => {
      mockListInvites.mockResolvedValue({
        invites: [invite({ expires_at: new Date(NOW - 1000).toISOString() })],
      });
      await mount();
      const row = container.querySelector('.ri-invite-row') as HTMLElement;
      expect(row.className).toContain('status-expired');
      expect(row.querySelector('.ri-badge')?.textContent).toBe('EXPIRED');
      expect(row.querySelector('.ri-terminal-note')?.textContent).toBe('EXPIRED');
      expect(Array.from(row.querySelectorAll('button')).some((b) => b.textContent === 'REVOKE')).toBe(false);
    });

    it('shows FULLY REDEEMED for an exhausted invite and a revoked-at note for a revoked one', async () => {
      mockListInvites.mockResolvedValue({
        invites: [
          invite({ id: 'ex-1', status: 'exhausted', uses: 1 }),
          invite({ id: 'rv-1', status: 'revoked', revoked_at: new Date(NOW).toISOString() }),
        ],
      });
      await mount();
      const rows = container.querySelectorAll('.ri-invite-row');
      expect(rows[0].querySelector('.ri-terminal-note')?.textContent).toBe('FULLY REDEEMED');
      expect(rows[1].querySelector('.ri-terminal-note')?.textContent).toContain('REVOKED');
    });

    it('updates the relative countdown as time advances', async () => {
      mockListInvites.mockResolvedValue({ invites: [invite()] });
      await mount();
      await act(async () => {
        vi.advanceTimersByTime(61_000);
      });
      const row = container.querySelector('.ri-invite-row') as HTMLElement;
      expect(row.querySelectorAll('.ri-meta-value')[1].textContent).toBe('58m');
    });

    it('reflects only effectively-active invites in the ACTIVE tally', async () => {
      mockListInvites.mockResolvedValue({
        invites: [invite({ id: 'a' }), invite({ id: 'b', status: 'revoked' })],
      });
      await mount();
      expect(container.querySelector('.ri-active-tally')?.textContent).toBe('1 / 10 ACTIVE');
    });
  });

  describe('create flow', () => {
    it('clamps the max-uses stepper to [1, 10]', async () => {
      await mount();
      const minus = container.querySelector('.ri-step-btn') as HTMLButtonElement;
      expect(minus.disabled).toBe(true);
      const plus = container.querySelectorAll('.ri-step-btn')[1] as HTMLButtonElement;
      for (let i = 0; i < 10; i++) {
        await act(async () => { plus.click(); });
      }
      expect(container.querySelector('.ri-stepper-value')?.textContent).toBe('10');
      expect(plus.disabled).toBe(true);
    });

    it('selects an expiry preset and highlights it', async () => {
      await mount();
      const buttons = Array.from(container.querySelectorAll('.ri-seg-btn'));
      const thirtyDay = buttons.find((b) => b.textContent === '30 DAYS') as HTMLButtonElement;
      const sevenDay = buttons.find((b) => b.textContent === '7 DAYS') as HTMLButtonElement;
      expect(sevenDay.className).toContain('selected');
      await act(async () => { thirtyDay.click(); });
      expect(thirtyDay.className).toContain('selected');
      expect(sevenDay.className).not.toContain('selected');
    });

    it('mints with the clamped max_uses and the preset-derived expires_at, and shows the spotlight once the refetch reconciles the authoritative list', async () => {
      const existing = invite({ id: 'existing' });
      const minted = invite({ id: 'new-1', code: 'FRESH123' });
      mockCreateInvite.mockResolvedValue({ invite: minted });
      mockListInvites.mockResolvedValueOnce({ invites: [existing] }).mockResolvedValue({ invites: [minted, existing] });
      await mount();

      await act(async () => {
        (container.querySelector('.ri-btn.primary.mint') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockCreateInvite).toHaveBeenCalledWith('region-1', {
        max_uses: 1,
        expires_at: new Date(NOW + 7 * 86400 * 1000).toISOString(),
      });
      expect(container.querySelector('.ri-minted-code')?.textContent).toBe('FRESH123');
      expect(container.querySelectorAll('.ri-invite-row')).toHaveLength(2);
    });

    it('shows createError mapped through friendlyError on mint failure, without a spotlight', async () => {
      mockCreateInvite.mockRejectedValue(new Error('ERR_ACTIVE_INVITE_CAP'));
      await mount();
      await act(async () => {
        (container.querySelector('.ri-btn.primary.mint') as HTMLButtonElement).click();
      });
      await flush();
      expect(container.querySelector('.ri-section .ri-validation-strip')?.textContent).toBe(
        'You have reached the maximum active invites for this region. Revoke or let one expire before minting another.'
      );
      expect(container.querySelector('.ri-minted-card')).toBeNull();
    });
  });

  describe('revoke flow', () => {
    it('requires arm-then-confirm; KEEP disarms without calling the API', async () => {
      mockListInvites.mockResolvedValue({ invites: [invite()] });
      await mount();
      await act(async () => {
        (container.querySelector('.ri-btn.danger') as HTMLButtonElement).click();
      });
      expect(container.querySelector('.ri-confirm-row')).not.toBeNull();

      await act(async () => {
        (container.querySelector('.ri-btn.ghost') as HTMLButtonElement).click();
      });
      expect(container.querySelector('.ri-confirm-row')).toBeNull();
      expect(mockRevokeInvite).not.toHaveBeenCalled();
    });

    it('confirms revoke, clears the minted spotlight if it was the revoked invite, and refetches', async () => {
      const target = invite();
      mockCreateInvite.mockResolvedValue({ invite: target });
      mockListInvites.mockResolvedValueOnce({ invites: [] }).mockResolvedValue({ invites: [target] });
      await mount();
      await act(async () => {
        (container.querySelector('.ri-btn.primary.mint') as HTMLButtonElement).click();
      });
      await flush();
      expect(container.querySelector('.ri-minted-card')).not.toBeNull();

      await act(async () => {
        (container.querySelector('.ri-btn.danger') as HTMLButtonElement).click();
      });
      await act(async () => {
        (container.querySelector('.ri-btn.danger.commit') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockRevokeInvite).toHaveBeenCalledWith('region-1', 'inv-1');
      expect(container.querySelector('.ri-minted-card')).toBeNull();
    });

    it('shows a row-scoped error on revoke failure and stays armed', async () => {
      mockListInvites.mockResolvedValue({ invites: [invite()] });
      mockRevokeInvite.mockRejectedValue(new Error('ERR_NOT_INVITE_OWNER'));
      await mount();
      await act(async () => {
        (container.querySelector('.ri-btn.danger') as HTMLButtonElement).click();
      });
      await act(async () => {
        (container.querySelector('.ri-btn.danger.commit') as HTMLButtonElement).click();
      });
      await flush();

      expect(container.querySelector('.ri-invite-row .ri-validation-strip')?.textContent).toBe('You did not mint this invite.');
      expect(container.querySelector('.ri-confirm-row')).not.toBeNull();
    });
  });

  describe('copy to clipboard', () => {
    it('copies the code via navigator.clipboard and flashes COPIED, reverting after the timeout', async () => {
      mockListInvites.mockResolvedValue({ invites: [invite()] });
      await mount();
      const copyBtn = container.querySelector('.ri-copy-btn') as HTMLButtonElement;
      await act(async () => { copyBtn.click(); });
      await flush();

      expect(writeTextMock).toHaveBeenCalledWith('ABCD1234');
      expect(copyBtn.textContent).toBe('COPIED');

      await act(async () => {
        vi.advanceTimersByTime(1801);
      });
      expect(copyBtn.textContent).toBe('COPY CODE');
    });

    it('falls back to a hidden textarea + execCommand when navigator.clipboard is unavailable', async () => {
      Object.defineProperty(window.navigator, 'clipboard', { value: undefined, configurable: true });
      const execCommandMock = vi.fn().mockReturnValue(true);
      (document as unknown as { execCommand: typeof execCommandMock }).execCommand = execCommandMock;

      mockListInvites.mockResolvedValue({ invites: [invite()] });
      await mount();
      const copyBtn = container.querySelector('.ri-copy-btn') as HTMLButtonElement;
      await act(async () => { copyBtn.click(); });
      await flush();

      expect(execCommandMock).toHaveBeenCalledWith('copy');
      expect(copyBtn.textContent).toBe('COPIED');
    });
  });
});

// @vitest-environment jsdom
/**
 * LEG-3164 Soft-ORDER — RegionInvitePanel TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockListInvites, mockCreateInvite, mockRevokeInvite } = vi.hoisted(() => ({
  mockListInvites: vi.fn(),
  mockCreateInvite: vi.fn(),
  mockRevokeInvite: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  regionOwnerAPI: {
    listInvites: mockListInvites,
    createInvite: mockCreateInvite,
    revokeInvite: mockRevokeInvite,
  },
}));

import RegionInvitePanel, { formatRegionInviteError } from '../RegionInvitePanel';

const invite = {
  id: 'inv-1',
  code: 'ABCD1234',
  region_id: 'region-1',
  max_uses: 1,
  uses: 0,
  status: 'active',
  expires_at: new Date(Date.now() + 3600_000).toISOString(),
  created_at: new Date().toISOString(),
};

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('formatRegionInviteError TypeError densify (LEG-3164)', () => {
  it('falls back on TypeError network collapse for list', () => {
    const text = formatRegionInviteError(
      new TypeError('Failed to fetch'),
      'Invite registry unreachable. Try again.',
    );
    expect(text).toBe('Invite registry unreachable. Try again.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on TypeError network collapse for create', () => {
    const text = formatRegionInviteError(new TypeError('Failed to fetch'), 'Invite mint rejected.');
    expect(text).toBe('Invite mint rejected.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves ERR_* server detail when not TypeError', () => {
    expect(
      formatRegionInviteError(new Error('ERR_NOT_REGION_OWNER'), 'Invite registry unreachable. Try again.'),
    ).toBe('You are not the owner of this region.');
  });
});

describe('RegionInvitePanel TypeError densify (LEG-3164)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockListInvites.mockReset();
    mockCreateInvite.mockReset();
    mockRevokeInvite.mockReset();
    mockRevokeInvite.mockResolvedValue({});

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

  const mount = async () => {
    await act(async () => {
      root.render(<RegionInvitePanel regionId="region-1" />);
    });
    await flush();
  };

  it('list TypeError surfaces honest fallback without Failed to fetch / TypeError', async () => {
    mockListInvites.mockRejectedValue(new TypeError('Failed to fetch'));
    await mount();
    const strip = container.querySelector('.ri-validation-strip');
    expect(strip?.textContent).toBe('Invite registry unreachable. Try again.');
    expect(strip?.textContent).not.toMatch(/Failed to fetch/i);
    expect(strip?.textContent).not.toMatch(/TypeError/i);
  });

  it('create TypeError surfaces honest fallback without Failed to fetch / TypeError', async () => {
    mockListInvites.mockResolvedValue({ invites: [] });
    mockCreateInvite.mockRejectedValue(new TypeError('Failed to fetch'));
    await mount();
    await act(async () => {
      (container.querySelector('.ri-btn.primary.mint') as HTMLButtonElement).click();
    });
    await flush();
    const strip = container.querySelector('.ri-section .ri-validation-strip');
    expect(strip?.textContent).toBe('Invite mint rejected.');
    expect(strip?.textContent).not.toMatch(/Failed to fetch/i);
    expect(strip?.textContent).not.toMatch(/TypeError/i);
  });

  it('revoke TypeError surfaces honest fallback without Failed to fetch / TypeError', async () => {
    mockListInvites.mockResolvedValue({ invites: [invite] });
    mockRevokeInvite.mockRejectedValue(new TypeError('Failed to fetch'));
    await mount();
    await act(async () => {
      (container.querySelector('.ri-btn.danger') as HTMLButtonElement).click();
    });
    await act(async () => {
      (container.querySelector('.ri-btn.danger.commit') as HTMLButtonElement).click();
    });
    await flush();
    const strip = container.querySelector('.ri-invite-row .ri-validation-strip');
    expect(strip?.textContent).toBe('Revoke rejected.');
    expect(strip?.textContent).not.toMatch(/Failed to fetch/i);
    expect(strip?.textContent).not.toMatch(/TypeError/i);
  });
});

// @vitest-environment jsdom
/**
 * ShipRegistryPanel — WO-WIRE-SHIP-REGISTRY-UI.
 * Pins report-stolen / abandon / claim call paths.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const {
  reportStolen,
  retractStolenReport,
  abandon,
  claim,
  fileTransferClaim,
  approveTransferClaim,
} = vi.hoisted(() => ({
  reportStolen: vi.fn(),
  retractStolenReport: vi.fn(),
  abandon: vi.fn(),
  claim: vi.fn(),
  fileTransferClaim: vi.fn(),
  approveTransferClaim: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  shipRegistryAPI: {
    reportStolen: (...a: unknown[]) => reportStolen(...a),
    retractStolenReport: (...a: unknown[]) => retractStolenReport(...a),
    abandon: (...a: unknown[]) => abandon(...a),
    claim: (...a: unknown[]) => claim(...a),
    fileTransferClaim: (...a: unknown[]) => fileTransferClaim(...a),
    approveTransferClaim: (...a: unknown[]) => approveTransferClaim(...a),
  },
}));

import ShipRegistryPanel, { formatShipRegistryActionError } from '../ShipRegistryPanel';

describe('formatShipRegistryActionError', () => {
  it('preserves 404 Ship not found detail', () => {
    const err = new Error('Ship not found.');
    (err as { status?: number }).status = 404;
    expect(formatShipRegistryActionError(err)).toBe('Ship not found.');
  });

  it('preserves structured code/message detail', () => {
    const err = new Error('Not the registered owner.');
    (err as { status?: number; code?: string; data?: unknown }).status = 403;
    (err as { code?: string }).code = 'ERR_NOT_REGISTERED_OWNER';
    (err as { data?: unknown }).data = {
      detail: { code: 'ERR_NOT_REGISTERED_OWNER', message: 'Not the registered owner.' },
    };
    expect(formatShipRegistryActionError(err)).toBe(
      'Not the registered owner. [ERR_NOT_REGISTERED_OWNER]'
    );
  });

  it('falls back when only bare API Error status is present', () => {
    expect(formatShipRegistryActionError(new Error('API Error: 500'))).toBe(
      'Registry action failed'
    );
  });

  it('falls back on TypeError network collapse without leaking Failed to fetch', () => {
    const text = formatShipRegistryActionError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Registry action failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});

describe('ShipRegistryPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    reportStolen.mockReset().mockResolvedValue({});
    retractStolenReport.mockReset().mockResolvedValue({});
    abandon.mockReset().mockResolvedValue({});
    claim.mockReset().mockResolvedValue({});
    fileTransferClaim.mockReset().mockResolvedValue({});
    approveTransferClaim.mockReset().mockResolvedValue({});
    vi.stubGlobal('confirm', vi.fn(() => true));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
  });

  it('report stolen POSTs for the selected ship', async () => {
    await act(async () => {
      root.render(<ShipRegistryPanel shipId="ship-9" shipName="Rusty" portId="port-1" />);
    });

    const btn = container.querySelector(
      '[data-testid="ship-registry-report-stolen"]'
    ) as HTMLButtonElement;
    expect(btn).not.toBeNull();
    await act(async () => {
      btn.click();
    });

    expect(reportStolen).toHaveBeenCalledWith('ship-9');
  });

  it('abandon / claim require port and call with port_id', async () => {
    await act(async () => {
      root.render(<ShipRegistryPanel shipId="ship-9" portId="port-1" />);
    });

    await act(async () => {
      (container.querySelector('[data-testid="ship-registry-abandon"]') as HTMLButtonElement).click();
    });
    expect(abandon).toHaveBeenCalledWith('ship-9', 'port-1');

    await act(async () => {
      (container.querySelector('[data-testid="ship-registry-claim"]') as HTMLButtonElement).click();
    });
    expect(claim).toHaveBeenCalledWith('ship-9', 'port-1');

    await act(async () => {
      (
        container.querySelector('[data-testid="ship-registry-transfer-claim"]') as HTMLButtonElement
      ).click();
    });
    expect(fileTransferClaim).toHaveBeenCalledWith('ship-9', 'port-1');
  });

  it('surfaces GS 404 Ship not found on report failure', async () => {
    const err = new Error('Ship not found.');
    (err as { status?: number }).status = 404;
    reportStolen.mockRejectedValueOnce(err);

    await act(async () => {
      root.render(<ShipRegistryPanel shipId="ship-9" shipName="Rusty" portId="port-1" />);
    });

    await act(async () => {
      (container.querySelector('[data-testid="ship-registry-report-stolen"]') as HTMLButtonElement).click();
    });

    const feedback = container.querySelector('[data-testid="ship-registry-feedback"]');
    expect(feedback?.textContent).toBe('Ship not found.');
  });

  it('surfaces structured code/message on claim failure', async () => {
    const err = new Error('Hull already claimed.');
    (err as { status?: number; code?: string }).status = 409;
    (err as { code?: string }).code = 'ERR_ALREADY_CLAIMED';
    claim.mockRejectedValueOnce(err);

    await act(async () => {
      root.render(<ShipRegistryPanel shipId="ship-9" portId="port-1" />);
    });

    await act(async () => {
      (container.querySelector('[data-testid="ship-registry-claim"]') as HTMLButtonElement).click();
    });

    const feedback = container.querySelector('[data-testid="ship-registry-feedback"]');
    expect(feedback?.textContent).toBe('Hull already claimed. [ERR_ALREADY_CLAIMED]');
  });

  it('report TypeError surfaces Registry action failed without Failed to fetch / TypeError', async () => {
    reportStolen.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<ShipRegistryPanel shipId="ship-9" shipName="Rusty" portId="port-1" />);
    });

    await act(async () => {
      (container.querySelector('[data-testid="ship-registry-report-stolen"]') as HTMLButtonElement).click();
    });

    const feedback = container.querySelector('[data-testid="ship-registry-feedback"]');
    const text = feedback?.textContent ?? '';
    expect(text).toMatch(/Registry action failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});

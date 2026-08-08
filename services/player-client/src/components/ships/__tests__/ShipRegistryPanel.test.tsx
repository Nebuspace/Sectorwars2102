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

import ShipRegistryPanel from '../ShipRegistryPanel';

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
});

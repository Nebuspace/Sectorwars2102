// @vitest-environment jsdom
/**
 * ShipRegistryPanel — WO-WIRE-SHIP-REGISTRY-UI + LEG-329/330.
 * Pins report-stolen / abandon / claim / eject / board / salvage-break call paths.
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
  eject,
  board,
  salvageBreak,
} = vi.hoisted(() => ({
  reportStolen: vi.fn(),
  retractStolenReport: vi.fn(),
  abandon: vi.fn(),
  claim: vi.fn(),
  fileTransferClaim: vi.fn(),
  approveTransferClaim: vi.fn(),
  eject: vi.fn(),
  board: vi.fn(),
  salvageBreak: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  shipRegistryAPI: {
    reportStolen: (...a: unknown[]) => reportStolen(...a),
    retractStolenReport: (...a: unknown[]) => retractStolenReport(...a),
    abandon: (...a: unknown[]) => abandon(...a),
    claim: (...a: unknown[]) => claim(...a),
    fileTransferClaim: (...a: unknown[]) => fileTransferClaim(...a),
    approveTransferClaim: (...a: unknown[]) => approveTransferClaim(...a),
    eject: (...a: unknown[]) => eject(...a),
    board: (...a: unknown[]) => board(...a),
    salvageBreak: (...a: unknown[]) => salvageBreak(...a),
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
    eject.mockReset().mockResolvedValue({ ejected_ship_id: 'ship-1', turns_spent: 0 });
    board.mockReset().mockResolvedValue({ boarded: true, state: 'owner_aboard' });
    salvageBreak.mockReset().mockResolvedValue({
      ship_id: 'ship-9',
      started_at: '2026-08-17T12:00:00Z',
      duration_seconds: 300,
      completes_at: '2026-08-17T12:05:00Z',
    });
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

  it('eject calls shipRegistryAPI.eject with no ship id', async () => {
    await act(async () => {
      root.render(<ShipRegistryPanel shipId="ship-9" />);
    });

    await act(async () => {
      (container.querySelector('[data-testid="ship-registry-eject"]') as HTMLButtonElement).click();
    });

    expect(eject).toHaveBeenCalledTimes(1);
    expect(eject).toHaveBeenCalledWith();
    expect(
      (container.querySelector('[data-testid="ship-registry-feedback"]') as HTMLElement).textContent
    ).toMatch(/Drifting/i);
  });

  it('board POSTs target ship id and optional pin', async () => {
    await act(async () => {
      root.render(<ShipRegistryPanel shipId="ship-9" />);
    });

    const pinInput = container.querySelector(
      '[data-testid="ship-registry-board-pin"]'
    ) as HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    await act(async () => {
      setter.call(pinInput, 'ABC123');
      pinInput.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await act(async () => {
      (container.querySelector('[data-testid="ship-registry-board"]') as HTMLButtonElement).click();
    });

    expect(board).toHaveBeenCalledWith('ship-9', 'ABC123');
  });

  it('board omits pin when the pin field is empty', async () => {
    await act(async () => {
      root.render(<ShipRegistryPanel shipId="ship-owned" />);
    });

    await act(async () => {
      (container.querySelector('[data-testid="ship-registry-board"]') as HTMLButtonElement).click();
    });

    expect(board).toHaveBeenCalledWith('ship-owned', null);
  });

  it('salvage-break shows ETA on success', async () => {
    await act(async () => {
      root.render(<ShipRegistryPanel shipId="ship-drift" />);
    });

    await act(async () => {
      (
        container.querySelector('[data-testid="ship-registry-salvage-break"]') as HTMLButtonElement
      ).click();
    });

    expect(salvageBreak).toHaveBeenCalledWith('ship-drift');
    const progress = container.querySelector(
      '[data-testid="ship-registry-salvage-progress"]'
    ) as HTMLElement;
    expect(progress).not.toBeNull();
    expect(progress.textContent).toMatch(/300s/);
    expect(progress.textContent).toMatch(/2026-08-17T12:05:00Z/);
    expect(
      (container.querySelector('[data-testid="ship-registry-feedback"]') as HTMLElement).textContent
    ).toMatch(/Salvage break started/i);
  });

  it('salvage-break surfaces honest server error text', async () => {
    salvageBreak.mockRejectedValueOnce(
      new Error('This ship is in sector 7; travel there to salvage-break it.')
    );
    await act(async () => {
      root.render(<ShipRegistryPanel shipId="ship-far" />);
    });

    await act(async () => {
      (
        container.querySelector('[data-testid="ship-registry-salvage-break"]') as HTMLButtonElement
      ).click();
    });

    expect(
      (container.querySelector('[data-testid="ship-registry-feedback"]') as HTMLElement).textContent
    ).toMatch(/sector 7/);
  });
});

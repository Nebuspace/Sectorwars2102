// @vitest-environment jsdom
/**
 * renderTableauPopupContent — star / procedural chrome (no land/dock gates).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockRead = vi.fn();
const mockSalvage = vi.fn();

vi.mock('../../../services/api', () => ({
  beaconAPI: {
    read: (...a: unknown[]) => mockRead(...a),
    salvage: (...a: unknown[]) => mockSalvage(...a),
  },
}));

import { renderTableauPopupContent } from '../windshieldTableauPopupContent';
import type { PopupState } from '../windshieldTableauHelpers';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseParams = {
  sectorId: 42,
  planets: [],
  system: null,
  star: { xPct: 20, yPct: 50, sizeEm: 3 },
  shipPos: null,
  localTraveling: false,
  glideTargetId: null,
  onHaltApproach: vi.fn(),
  onClosePopup: vi.fn(),
  onApproachPlanet: vi.fn(),
  onApproachStation: vi.fn(),
};

describe('renderTableauPopupContent', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.clearAllMocks();
    mockRead.mockReset();
    mockSalvage.mockReset();
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

  it('renders star class chrome', async () => {
    const popup = {
      xPct: 20,
      yPct: 50,
      meta: { kind: 'star', label: 'Sol', color: '#ff0', starClass: 'G2' },
    } as unknown as PopupState;

    await act(async () => {
      root.render(<>{renderTableauPopupContent({ ...baseParams, popup })}</>);
    });

    expect(container.textContent).toContain('SOL');
    expect(container.textContent).toContain('CLASS G2');
    expect(container.textContent).toContain('SECTOR 42');
  });

  it('renders procedural unsurveyed status', async () => {
    const popup = {
      xPct: 30,
      yPct: 40,
      meta: {
        kind: 'procedural',
        designation: 'P-9',
        typeName: 'Rock',
        sizeDesc: 'Small',
      },
    } as unknown as PopupState;

    await act(async () => {
      root.render(<>{renderTableauPopupContent({ ...baseParams, popup })}</>);
    });

    expect(container.textContent).toContain('P-9');
    expect(container.textContent).toContain('UNSURVEYED');
    expect(container.textContent).toContain('NO LANDING SITE');
  });

  const visitorBeaconPopup = {
    xPct: 40,
    yPct: 40,
    meta: {
      kind: 'beacon',
      beaconId: 'beacon-visitor-1',
      deployerNickname: 'Stranger',
      preview: 'just a teaser…',
      deployedAt: '2026-08-01T00:00:00Z',
    },
  } as unknown as PopupState;

  it('visitor Read calls beaconAPI.read and shows full message + author', async () => {
    mockRead.mockResolvedValue({
      id: 'beacon-visitor-1',
      message: 'The full secret from a stranger.',
      deployer_nickname: 'Stranger',
      read_once: false,
      read_count: 2,
    });
    const onBeaconRemoved = vi.fn();

    await act(async () => {
      root.render(
        <>{renderTableauPopupContent({ ...baseParams, popup: visitorBeaconPopup, onBeaconRemoved })}</>,
      );
    });

    expect(container.textContent).toContain('just a teaser');
    const readBtn = container.querySelector('[data-testid="beacon-popup-read"]') as HTMLButtonElement;
    expect(readBtn).toBeTruthy();

    await act(async () => {
      readBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockRead).toHaveBeenCalledWith('beacon-visitor-1');
    expect(container.querySelector('[data-testid="beacon-popup-full-message"]')?.textContent).toBe(
      'The full secret from a stranger.',
    );
    expect(container.querySelector('[data-testid="beacon-popup-author"]')?.textContent).toContain('Stranger');
    expect(onBeaconRemoved).not.toHaveBeenCalled();
  });

  it('visitor Salvage calls beaconAPI.salvage and notifies removal', async () => {
    mockSalvage.mockResolvedValue({ id: 'beacon-visitor-1', salvage_refund: 250 });
    const onBeaconRemoved = vi.fn();
    const onClosePopup = vi.fn();

    await act(async () => {
      root.render(
        <>
          {renderTableauPopupContent({
            ...baseParams,
            popup: visitorBeaconPopup,
            onClosePopup,
            onBeaconRemoved,
          })}
        </>,
      );
    });

    const salvageBtn = container.querySelector('[data-testid="beacon-popup-salvage"]') as HTMLButtonElement;
    expect(salvageBtn).toBeTruthy();

    await act(async () => {
      salvageBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockSalvage).toHaveBeenCalledWith('beacon-visitor-1');
    expect(onBeaconRemoved).toHaveBeenCalledWith('beacon-visitor-1');
    expect(onClosePopup).toHaveBeenCalled();
  });
});

// @vitest-environment jsdom
/**
 * LEG-3698 Soft-ORDER — Teleprinter nav-sequence TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

Element.prototype.scrollIntoView = vi.fn();

const mockSendARIAMessage = vi.fn();
let mockIsConnected = true;

const SEED_ARIA_MESSAGES = [
  {
    id: 'narr-1',
    type: 'ai' as const,
    content: 'Hazard field detected — shields answering.',
    timestamp: '2026-01-01T00:00:02.000Z',
    isNarration: true as const,
  },
];

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    ariaMessages: SEED_ARIA_MESSAGES,
    sendARIAMessage: (...args: unknown[]) => mockSendARIAMessage(...args),
    isConnected: mockIsConnected,
  }),
}));

function makePlayerState(overrides: Record<string, unknown> = {}) {
  return {
    id: 'player-1',
    credits: 5_000,
    turns: 120,
    current_sector_id: 7,
    is_docked: false,
    is_landed: false,
    ...overrides,
  };
}

let mockPlayerState: Record<string, unknown> = makePlayerState();
let mockStationsInSector: Array<{ id: string; name: string }> = [];
let mockPlanetsInSector: Array<{
  id: string;
  name: string;
  owner_id?: string | null;
  is_population_hub?: boolean;
}> = [];
const mockDockAtStation = vi.fn();
const mockUndockFromStation = vi.fn();
const mockLandOnPlanet = vi.fn();
const mockClaimPlanet = vi.fn();
const mockLeavePlanet = vi.fn();

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    currentSector: { name: 'Sol', sector_id: 7 },
    stationsInSector: mockStationsInSector,
    planetsInSector: mockPlanetsInSector,
    dockAtStation: (...args: unknown[]) => mockDockAtStation(...args),
    undockFromStation: (...args: unknown[]) => mockUndockFromStation(...args),
    landOnPlanet: (...args: unknown[]) => mockLandOnPlanet(...args),
    claimPlanet: (...args: unknown[]) => mockClaimPlanet(...args),
    leavePlanet: (...args: unknown[]) => mockLeavePlanet(...args),
  }),
}));

const mockPlotCourse = vi.fn();
const mockEngage = vi.fn();
const mockAutopilotAbort = vi.fn();

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({
    plotCourse: (...args: unknown[]) => mockPlotCourse(...args),
    engage: (...args: unknown[]) => mockEngage(...args),
    abort: (...args: unknown[]) => mockAutopilotAbort(...args),
  }),
}));

import { ariaFeed } from '../../mfd/ariaFeedStore';
import Teleprinter from '../Teleprinter';

const DOCK_FAILURE = 'Docking sequence failed.';
const PLOT_FAILURE = 'No such sector on any chart I can read.';

const ControlledTeleprinter: React.FC = () => {
  const [bodyPanel, setBodyPanel] = React.useState(true);
  const [transcriptOpen, setTranscriptOpen] = React.useState(false);
  return (
    <Teleprinter
      bodyPanel={bodyPanel}
      onBodyPanelChange={setBodyPanel}
      transcriptOpen={transcriptOpen}
      onTranscriptOpenChange={setTranscriptOpen}
    />
  );
};

describe('Teleprinter TypeError densify (LEG-3698)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const flush = async () => {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  const setInput = async (el: HTMLInputElement, text: string) => {
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(el, text);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await flush();
  };

  const pressEnter = async (el: HTMLInputElement) => {
    await act(async () => {
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    });
    await flush();
  };

  const clickTab = async (id: string) => {
    const tab = container.querySelector(`#tp-mode-tab-${id}`) as HTMLButtonElement;
    await act(async () => {
      tab.click();
    });
    await flush();
  };

  const submitViaBody = async (text: string) => {
    const input = container.querySelector('.tp-input') as HTMLInputElement;
    await setInput(input, text);
    await pressEnter(input);
  };

  const navLogText = () => container.querySelector('#tp-log')?.textContent ?? '';

  beforeEach(() => {
    mockSendARIAMessage.mockReset();
    mockIsConnected = true;
    mockPlayerState = makePlayerState();
    mockStationsInSector = [{ id: 'station-9', name: 'Vela Trade Hub' }];
    mockPlanetsInSector = [];
    mockDockAtStation.mockReset();
    mockPlotCourse.mockReset();
    mockEngage.mockReset();
    mockAutopilotAbort.mockReset();
    ariaFeed.clearNav();
    ariaFeed.setConversationId(null);

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

  it('dock TypeError rejection shows canned nav only — no raw transport text', async () => {
    mockDockAtStation.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<ControlledTeleprinter />);
    });
    await flush();
    await clickTab('command-echo');
    await submitViaBody('dock');
    await clickTab('narration');
    await flush();

    expect(navLogText()).toContain(DOCK_FAILURE);
    expect(navLogText()).not.toMatch(/Failed to fetch/i);
    expect(navLogText()).not.toMatch(/TypeError/i);
    expect(document.body.textContent).not.toMatch(/Failed to fetch/i);
  });

  it('plotCourse TypeError rejection shows canned nav only — no raw transport text', async () => {
    mockPlotCourse.mockRejectedValueOnce(new TypeError('network down'));

    await act(async () => {
      root.render(<ControlledTeleprinter />);
    });
    await flush();
    await clickTab('command-echo');
    await submitViaBody('set course to 12');
    await clickTab('narration');
    await flush();

    expect(mockPlotCourse).toHaveBeenCalledWith(12);
    expect(navLogText()).toContain(PLOT_FAILURE);
    expect(navLogText()).not.toMatch(/network down/i);
    expect(navLogText()).not.toMatch(/TypeError/i);
  });
});

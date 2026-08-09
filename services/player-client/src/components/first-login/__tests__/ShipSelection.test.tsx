// @vitest-environment jsdom
/**
 * ShipSelection — ship grid, loading/empty, claim submit gate.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const claimShip = vi.fn();
const firstLoginState = {
  session: null,
  availableShips: ['SCOUT_SHIP', 'CARGO_HAULER'] as string[],
  sessionLoaded: true,
  claimShip,
  currentPrompt: 'Guard: Which vessel?',
  isLoading: false,
};

vi.mock('../../../contexts/FirstLoginContext', () => ({
  useFirstLogin: () => firstLoginState,
}));

import ShipSelection from '../ShipSelection';

describe('ShipSelection', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.clearAllMocks();
    firstLoginState.availableShips = ['SCOUT_SHIP', 'CARGO_HAULER'];
    firstLoginState.sessionLoaded = true;
    firstLoginState.isLoading = false;
    firstLoginState.currentPrompt = 'Guard: Which vessel?';
    claimShip.mockResolvedValue(undefined);
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

  it('lists ship options and disables Submit until ship + response are set', async () => {
    await act(async () => {
      root.render(<ShipSelection />);
    });

    expect(container.textContent).toContain('Scout Ship');
    expect(container.textContent).toContain('Cargo Hauler');
    expect(container.textContent).toContain('Guard: Which vessel?');

    const submit = container.querySelector(
      'button.submit-response',
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    const scout = Array.from(container.querySelectorAll('.ship-option')).find((el) =>
      el.textContent?.includes('Scout Ship'),
    ) as HTMLElement;
    await act(async () => {
      scout.click();
    });
    expect(scout.getAttribute('aria-pressed')).toBe('true');
    expect(submit.disabled).toBe(true);

    const textarea = container.querySelector('textarea') as HTMLTextAreaElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype,
        'value',
      )?.set;
      setter?.call(textarea, 'That scout is mine.');
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(submit.disabled).toBe(false);

    await act(async () => {
      submit.click();
    });
    expect(claimShip).toHaveBeenCalledWith('SCOUT_SHIP', 'That scout is mine.');
  });

  it('shows loading and empty ship states', async () => {
    firstLoginState.availableShips = [];
    firstLoginState.sessionLoaded = false;
    await act(async () => {
      root.render(<ShipSelection />);
    });
    expect(container.textContent).toContain('Loading available ships');

    firstLoginState.sessionLoaded = true;
    await act(async () => {
      root.render(<ShipSelection />);
    });
    expect(container.textContent).toContain('No ships are available right now');
  });
});

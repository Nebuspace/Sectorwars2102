// @vitest-environment jsdom
/**
 * ShellContext — presence flag + slot defaults / provider values.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  ShellPresenceContext,
  ShellSlotsContext,
  useShellPresent,
  useShellSlots,
} from '../ShellContext';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const Probe: React.FC = () => {
  const present = useShellPresent();
  const slots = useShellSlots();
  return (
    <div
      data-testid="probe"
      data-present={String(present)}
      data-band={slots.bandEl ? 'yes' : 'no'}
      data-deck={slots.deckEl ? 'yes' : 'no'}
    />
  );
};

describe('ShellContext', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
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

  it('defaults to absent shell and null slots', async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    const probe = container.querySelector('[data-testid="probe"]') as HTMLElement;
    expect(probe.dataset.present).toBe('false');
    expect(probe.dataset.band).toBe('no');
    expect(probe.dataset.deck).toBe('no');
  });

  it('reads provider values when nested under shell contexts', async () => {
    const band = document.createElement('div');
    const deck = document.createElement('div');
    await act(async () => {
      root.render(
        <ShellPresenceContext.Provider value={true}>
          <ShellSlotsContext.Provider value={{ bandEl: band, deckEl: deck }}>
            <Probe />
          </ShellSlotsContext.Provider>
        </ShellPresenceContext.Provider>,
      );
    });
    const probe = container.querySelector('[data-testid="probe"]') as HTMLElement;
    expect(probe.dataset.present).toBe('true');
    expect(probe.dataset.band).toBe('yes');
    expect(probe.dataset.deck).toBe('yes');
  });
});

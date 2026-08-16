// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import PlayerNamePlate from './PlayerNamePlate';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('PlayerNamePlate', () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('renders name only when no pin', () => {
    act(() => {
      root.render(<PlayerNamePlate name="Ace" />);
    });
    expect(container.textContent).toContain('Ace');
    expect(container.querySelector('[data-testid="player-name-plate-medal"]')).toBeNull();
  });

  it('renders pinned medal icon and count badge', () => {
    act(() => {
      root.render(
        <PlayerNamePlate
          name="Ace"
          pinnedMedalId="star_bronze"
          pinnedMedalIcon="🥉"
          pinnedMedalName="Bronze Star"
          medalCount={4}
        />,
      );
    });
    expect(container.querySelector('[data-testid="player-name-plate-medal"]')?.textContent).toBe(
      '🥉',
    );
    expect(container.querySelector('[data-testid="player-name-plate-count"]')?.textContent).toBe(
      '4',
    );
    expect(
      container.querySelector('[data-testid="player-name-plate"]')?.getAttribute('data-pinned-medal'),
    ).toBe('star_bronze');
  });
});

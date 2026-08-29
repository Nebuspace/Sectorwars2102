// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, afterEach } from 'vitest';
import PlayerNamePlate from '../../common/PlayerNamePlate';
import { shipCaptainNamePlateProps } from '../SolarSystemViewscreen';

describe('SolarSystemViewscreen ship medal identity (LEG-2657)', () => {
  let container: HTMLDivElement | undefined;
  let root: ReturnType<typeof createRoot> | undefined;

  afterEach(() => {
    if (root) act(() => root.unmount());
    if (container) container.remove();
  });

  it('shipCaptainNamePlateProps passes pinned medal fields when present on presence row', () => {
    const props = shipCaptainNamePlateProps({
      username: 'ace',
      pinned_medal_id: 'bronze_cluster',
      medal_count: 2,
    });
    expect(props).toEqual({
      name: 'ACE',
      pinnedMedalId: 'bronze_cluster',
      medalCount: 2,
    });
  });

  it('renders PlayerNamePlate medal pin for sector ship captain props', async () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const props = shipCaptainNamePlateProps({
      username: 'ace',
      pinned_medal_id: 'silver_star',
      medal_count: 5,
    });

    await act(async () => {
      root.render(
        <PlayerNamePlate
          name={props.name}
          size="sm"
          pinnedMedalId={props.pinnedMedalId}
          medalCount={props.medalCount}
        />,
      );
    });

    expect(container.querySelector('[data-testid="player-name-plate-medal"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="player-name-plate-count"]')?.textContent).toBe('5');
  });

  it('absent medal fields yield name-only props', () => {
    const props = shipCaptainNamePlateProps({ username: 'drift' });
    expect(props.pinnedMedalId).toBeUndefined();
    expect(props.name).toBe('DRIFT');
  });
});

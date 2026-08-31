import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import GalaxyOverviewHeader from './GalaxyOverviewHeader';
import type { GalaxyOverviewSummary } from './GalaxyOverviewHeader';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (!opts) return key;
      const extra = Object.entries(opts)
        .filter(([k]) => k !== 'defaultValue')
        .map(([k, v]) => `${k}=${v}`)
        .join(' ');
      return extra ? `${key} ${extra}` : key;
    },
  }),
}));

const populated: GalaxyOverviewSummary = {
  name: 'Andromeda',
  id: 'g-1',
  bangVersion: '1.3.0',
  bangSeed: 42,
  diameter: 1200,
  islandPercent: 0.125,
  clusterCount: 7,
};

describe('GalaxyOverviewHeader empty/mismatch (LEG-3253)', () => {
  it('renders empty-galaxy copy when galaxy is null', () => {
    render(<GalaxyOverviewHeader galaxy={null} />);

    expect(screen.getByText('bang.overview.title')).toBeTruthy();
    expect(screen.getByText('bang.overview.noGalaxy')).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders populated summary fields including island percent', () => {
    render(<GalaxyOverviewHeader galaxy={populated} />);

    expect(screen.getByText('1.3.0')).toBeTruthy();
    expect(screen.getByText('42')).toBeTruthy();
    expect(screen.getByText('1200')).toBeTruthy();
    expect(screen.getByText('12.5%')).toBeTruthy();
    expect(screen.getByText('7')).toBeTruthy();
  });

  it('shows version-mismatch warning when galaxy and server bang versions differ', () => {
    render(
      <GalaxyOverviewHeader
        galaxy={populated}
        serverBangVersion="1.4.0"
      />,
    );

    expect(
      screen.getByText('bang.overview.versionMismatchWarning galaxyVersion=1.3.0 serverVersion=1.4.0'),
    ).toBeTruthy();
  });

  it('does not show mismatch when versions match or server version is absent', () => {
    const { rerender } = render(
      <GalaxyOverviewHeader galaxy={populated} serverBangVersion="1.3.0" />,
    );
    expect(screen.queryByText(/versionMismatchWarning/)).toBeNull();

    rerender(<GalaxyOverviewHeader galaxy={populated} />);
    expect(screen.queryByText(/versionMismatchWarning/)).toBeNull();
  });

  it('shows Wipe and Add Region only when callbacks are provided and invokes them', () => {
    const onWipe = vi.fn();
    const onAddRegion = vi.fn();

    const { rerender } = render(<GalaxyOverviewHeader galaxy={populated} />);
    expect(screen.queryByRole('button', { name: 'bang.wipe.title' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'bang.overview.addRegion' })).toBeNull();

    rerender(
      <GalaxyOverviewHeader
        galaxy={populated}
        onWipe={onWipe}
        onAddRegion={onAddRegion}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'bang.wipe.title' }));
    fireEvent.click(screen.getByRole('button', { name: 'bang.overview.addRegion' }));
    expect(onWipe).toHaveBeenCalledTimes(1);
    expect(onAddRegion).toHaveBeenCalledTimes(1);
  });
});

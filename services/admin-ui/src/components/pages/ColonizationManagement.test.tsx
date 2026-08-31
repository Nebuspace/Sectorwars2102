import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ColonizationManagement } from './ColonizationManagement';

vi.mock('../colonization/ColonyOverview', () => ({
  ColonyOverview: () => <div data-testid="colony-overview-stub">Colony Overview</div>,
}));

vi.mock('../colonization/ProductionMonitoring', () => ({
  ProductionMonitoring: () => <div data-testid="production-monitoring-stub">Production Monitoring</div>,
}));

vi.mock('../colonization/GenesisDeviceTracking', () => ({
  GenesisDeviceTracking: () => <div data-testid="genesis-device-stub">Genesis Devices</div>,
}));

vi.mock('../colonization/PlanetaryManagement', () => ({
  PlanetaryManagement: () => <div data-testid="planetary-management-stub">Planetary Management</div>,
}));

describe('ColonizationManagement tab shell (LEG-3124)', () => {
  it('renders page header and default Colony Overview tab', () => {
    render(<ColonizationManagement />);
    expect(screen.getByRole('heading', { name: 'Colonization Management' })).toBeInTheDocument();
    expect(screen.getByTestId('colony-overview-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('production-monitoring-stub')).not.toBeInTheDocument();
  });

  it('switches to Production Monitoring tab', () => {
    render(<ColonizationManagement />);
    fireEvent.click(screen.getByRole('button', { name: /Production Monitoring/i }));
    expect(screen.getByTestId('production-monitoring-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('colony-overview-stub')).not.toBeInTheDocument();
  });

  it('switches to Genesis Devices tab', () => {
    render(<ColonizationManagement />);
    fireEvent.click(screen.getByRole('button', { name: /Genesis Devices/i }));
    expect(screen.getByTestId('genesis-device-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('colony-overview-stub')).not.toBeInTheDocument();
  });

  it('switches to Planetary Management tab', () => {
    render(<ColonizationManagement />);
    fireEvent.click(screen.getByRole('button', { name: /Planetary Management/i }));
    expect(screen.getByTestId('planetary-management-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('colony-overview-stub')).not.toBeInTheDocument();
  });

  it('can return to Colony Overview tab', () => {
    render(<ColonizationManagement />);
    fireEvent.click(screen.getByRole('button', { name: /Production Monitoring/i }));
    fireEvent.click(screen.getByRole('button', { name: /Colony Overview/i }));
    expect(screen.getByTestId('colony-overview-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('production-monitoring-stub')).not.toBeInTheDocument();
  });
});

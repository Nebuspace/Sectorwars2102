import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import BangGalaxyPage from './BangGalaxyPage';

const wipeGalaxy = vi.fn();
const loadGalaxyInfo = vi.fn();

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { error?: string }) =>
      opts?.error ? `${key}:${opts.error}` : key,
  }),
}));

vi.mock('../../contexts/AdminContext', () => ({
  useAdmin: () => ({
    galaxyState: { id: 'g1', name: 'TestGalaxy' },
    loadGalaxyInfo,
    wipeGalaxy,
  }),
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ token: 'tok' }),
}));

vi.mock('../universe/bang/GalaxyGenerationForm', () => ({
  default: () => <div data-testid="form-stub" />,
}));

vi.mock('../universe/bang/GalaxyGenerationHistory', () => ({
  default: () => <div data-testid="history-stub" />,
}));

vi.mock('../universe/bang/GenerationLogPanel', () => ({
  default: () => null,
}));

vi.mock('../universe/bang/AddRegionDialog', () => ({
  default: () => null,
}));

vi.mock('../universe/bang/GalaxyOverviewHeader', () => ({
  default: ({ onWipe }: { onWipe?: () => void }) => (
    <button type="button" onClick={onWipe}>
      Open wipe
    </button>
  ),
}));

vi.mock('../universe/bang/WipeGalaxyConfirmDialog', () => ({
  default: ({
    onConfirm,
    error,
  }: {
    onConfirm: (name: string) => void;
    error: string | null;
  }) => (
    <div>
      <button type="button" onClick={() => onConfirm('TestGalaxy')}>
        Confirm wipe
      </button>
      {error ? <div role="alert">{error}</div> : null}
    </div>
  ),
}));

describe('BangGalaxyPage scope errors (LEG-1215)', () => {
  beforeEach(() => {
    wipeGalaxy.mockReset();
    loadGalaxyInfo.mockReset();
  });

  it('surfaces scope denial on wipe 403', async () => {
    wipeGalaxy.mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: { status: 403, data: { detail: 'Missing scope admin.universe.manage' } },
      }),
    );

    render(<BangGalaxyPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Open wipe' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm wipe' }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/admin\.universe\.manage/i);
    });
  });

  it('shows rate-limit copy on wipe 429', async () => {
    wipeGalaxy.mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429, data: {} },
      }),
    );

    render(<BangGalaxyPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Open wipe' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm wipe' }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
  });
});

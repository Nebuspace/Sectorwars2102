import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { CustomReportBuilder } from './CustomReportBuilder';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

/**
 * LEG-3633 Soft-ORDER — CustomReportBuilder TypeError/Network Error densify.
 */
describe('CustomReportBuilder typeErrorHonesty densify (LEG-3633)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on load-path without leaking raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<CustomReportBuilder onGenerate={() => {}} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(
      /Gameserver unreachable — network error fetching report builder data/i,
    );
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on load-path without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<CustomReportBuilder onGenerate={() => {}} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(
      /Gameserver unreachable — network error fetching report builder data/i,
    );
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with admin.audit.view scope hint', async () => {
    vi.mocked(api.get).mockRejectedValue({
      response: { status: 403, data: {} },
    });

    render(<CustomReportBuilder onGenerate={() => {}} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/admin\.audit\.view/i);
    expect(alert).toMatch(/Access denied/i);
  });
});

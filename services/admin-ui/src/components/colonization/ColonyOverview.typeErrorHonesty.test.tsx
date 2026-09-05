import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ColonyOverview } from './ColonyOverview';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: { get: vi.fn() },
}));

vi.mock('../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    catalog: [],
    loading: false,
    getLabel: (n: string) => n,
    getIcon: () => '📦',
  }),
}));


const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

/**
 * LEG-3430 Soft-ORDER — ColonyOverview TypeError/Network Error honesty densify.
 * LEG-3886 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('ColonyOverview typeErrorHonesty densify (LEG-3430)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error to colonies load fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<ColonyOverview />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load colonies/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch to colonies load fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<ColonyOverview />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load colonies/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with colonization.view scope copy when colonies GET is denied', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<ColonyOverview />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Access denied|colonization\.view/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on colonies GET', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<ColonyOverview />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

});

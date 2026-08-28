import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import GalaxyGenerationForm from './GalaxyGenerationForm';
import { previewBangConfig } from '../../../services/bangGalaxyApi';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { error?: string }) =>
      opts?.error ? `${key}:${opts.error}` : key,
  }),
}));

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ token: 'tok' }),
}));

const bangGalaxy = vi.fn();
vi.mock('../../../contexts/AdminContext', () => ({
  useAdmin: () => ({ bangGalaxy }),
}));

vi.mock('../../../services/bangGalaxyApi', () => ({
  previewBangConfig: vi.fn(),
}));

describe('GalaxyGenerationForm (LEG-1253)', () => {
  beforeEach(() => {
    vi.mocked(previewBangConfig).mockReset();
    bangGalaxy.mockReset();
  });

  it('surfaces preview 403 as BANG_REGENERATE denial', async () => {
    vi.mocked(previewBangConfig).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), { response: { status: 403 } }),
    );
    const user = userEvent.setup();
    render(<GalaxyGenerationForm />);

    await user.click(screen.getByRole('button', { name: /preview/i }));

    await waitFor(() => {
      expect(screen.getByText(/BANG_REGENERATE/i)).toBeTruthy();
    });
    expect(screen.getByText(/Access denied/i).textContent).not.toMatch(
      /previewFailed/i,
    );
  });

  it('surfaces preview 429 as admin rate-limit copy', async () => {
    vi.mocked(previewBangConfig).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), { response: { status: 429 } }),
    );
    const user = userEvent.setup();
    render(<GalaxyGenerationForm />);

    await user.click(screen.getByRole('button', { name: /preview/i }));

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });

  it('surfaces commit 403 as BANG_REGENERATE denial (LEG-2711)', async () => {
    bangGalaxy.mockRejectedValue(
      Object.assign(new Error('HTTP 403'), { response: { status: 403 } }),
    );
    const user = userEvent.setup();
    render(<GalaxyGenerationForm />);

    await user.click(screen.getByRole('button', { name: 'bang.form.actions.commit' }));

    await waitFor(() => {
      expect(bangGalaxy).toHaveBeenCalled();
    });
    expect(screen.getByText(/BANG_REGENERATE/i)).toBeTruthy();
    expect(screen.getByText(/Access denied/i).textContent).not.toMatch(
      /submitFailed/i,
    );
  });

  it('surfaces commit 429 as admin rate-limit copy (LEG-2711)', async () => {
    bangGalaxy.mockRejectedValue(
      Object.assign(new Error('HTTP 429'), { response: { status: 429 } }),
    );
    const user = userEvent.setup();
    render(<GalaxyGenerationForm />);

    await user.click(screen.getByRole('button', { name: 'bang.form.actions.commit' }));

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
    expect(screen.queryByText(/submitFailed/i)).toBeNull();
  });
});

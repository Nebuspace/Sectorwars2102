import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MFASetup } from './MFASetup';
import { api } from '../../utils/auth';

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'testuser' } }),
}));

vi.mock('../../utils/auth', () => ({
  api: {
    post: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api, true);

const generateOk = {
  secret: 'SECRET123',
  setup_url: 'otpauth://test',
  qr_code_data_url: 'data:image/png;base64,abc',
  message: 'ok',
};

describe('MFASetup API errors (LEG-3172)', () => {
  beforeEach(() => {
    mockedApi.post.mockReset();
  });

  it('shows API detail when mfa/generate fails', async () => {
    mockedApi.post.mockRejectedValue({
      response: { data: { detail: 'MFA already enabled' } },
    });

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('MFA already enabled')).toBeTruthy();
    });
    expect(mockedApi.post).toHaveBeenCalledWith('/api/v1/auth/mfa/generate');
  });

  it('shows fallback when mfa/generate fails without detail', async () => {
    mockedApi.post.mockRejectedValue(new Error('network down'));

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('Failed to generate MFA secret')).toBeTruthy();
    });
  });

  it('surfaces formatAdminApiError fallback on mfa/generate TypeError (LEG-3322)', async () => {
    mockedApi.post.mockRejectedValue(new TypeError('Failed to fetch'));

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('Failed to generate MFA secret')).toBeTruthy();
    });

    const errorEl = screen.getByText('Failed to generate MFA secret');
    expect(errorEl.textContent).not.toMatch(/Failed to fetch/i);
    expect(errorEl.textContent).not.toMatch(/TypeError/i);
  });

  it('shows API detail when mfa/verify fails', async () => {
    mockedApi.post.mockImplementation((url: string) => {
      if (url === '/api/v1/auth/mfa/generate') {
        return Promise.resolve({ data: generateOk });
      }
      if (url === '/api/v1/auth/mfa/verify') {
        return Promise.reject({
          response: { data: { detail: 'Invalid verification code' } },
        });
      }
      return Promise.reject(new Error('unexpected'));
    });

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('SECRET123')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));

    const input = screen.getByPlaceholderText('000000');
    fireEvent.change(input, { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }));

    await waitFor(() => {
      expect(screen.getByText('Invalid verification code')).toBeTruthy();
    });
    expect(mockedApi.post).toHaveBeenCalledWith('/api/v1/auth/mfa/verify', {
      code: '123456',
    });
  });

  it('shows fallback when mfa/verify fails without detail', async () => {
    mockedApi.post.mockImplementation((url: string) => {
      if (url === '/api/v1/auth/mfa/generate') {
        return Promise.resolve({ data: generateOk });
      }
      return Promise.reject(new Error('network down'));
    });

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('SECRET123')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.change(screen.getByPlaceholderText('000000'), {
      target: { value: '654321' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }));

    await waitFor(() => {
      expect(screen.getByText('Verification failed')).toBeTruthy();
    });
  });

  it('surfaces formatAdminApiError fallback on mfa/verify TypeError (LEG-3322)', async () => {
    mockedApi.post.mockImplementation((url: string) => {
      if (url === '/api/v1/auth/mfa/generate') {
        return Promise.resolve({ data: generateOk });
      }
      if (url === '/api/v1/auth/mfa/verify') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return Promise.reject(new Error('unexpected'));
    });

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('SECRET123')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.change(screen.getByPlaceholderText('000000'), {
      target: { value: '654321' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }));

    await waitFor(() => {
      expect(screen.getByText('Verification failed')).toBeTruthy();
    });

    const error = screen.getByText('Verification failed').textContent ?? '';
    expect(error).not.toMatch(/Failed to fetch/i);
    expect(error).not.toMatch(/TypeError/i);
  });
});

describe('MFASetup axios Network Error densify (LEG-3511)', () => {
  beforeEach(() => {
    mockedApi.post.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios-shaped Network Error on mfa/generate to honest fallback', async () => {
    mockedApi.post.mockRejectedValue(new Error('Network Error'));

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('Failed to generate MFA secret')).toBeTruthy();
    });

    const errorEl = screen.getByText('Failed to generate MFA secret');
    expect(errorEl.textContent).not.toMatch(/Network Error/i);
  });

  it('collapses axios-shaped Network Error on mfa/verify to honest fallback', async () => {
    mockedApi.post.mockImplementation((url: string) => {
      if (url === '/api/v1/auth/mfa/generate') {
        return Promise.resolve({ data: generateOk });
      }
      if (url === '/api/v1/auth/mfa/verify') {
        return Promise.reject(new Error('Network Error'));
      }
      return Promise.reject(new Error('unexpected'));
    });

    render(<MFASetup />);

    await waitFor(() => {
      expect(screen.getByText('SECRET123')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.change(screen.getByPlaceholderText('000000'), {
      target: { value: '654321' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }));

    await waitFor(() => {
      expect(screen.getByText('Verification failed')).toBeTruthy();
    });

    const error = screen.getByText('Verification failed').textContent ?? '';
    expect(error).not.toMatch(/Network Error/i);
  });
});

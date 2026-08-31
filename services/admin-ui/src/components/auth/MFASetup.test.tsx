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
});

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import UserProfile from './UserProfile';

const mockUser = vi.hoisted(() => ({
  current: null as { username: string; mfaEnabled: boolean } | null,
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: mockUser.current }),
}));

vi.mock('./LogoutButton', () => ({
  default: () => <button type="button">Logout</button>,
}));

vi.mock('./MFASetup', () => ({
  MFASetup: ({ onCancel }: { onCancel: () => void }) => (
    <div>
      <span>MFA Setup View</span>
      <button type="button" onClick={onCancel}>
        Cancel MFA
      </button>
    </div>
  ),
}));

describe('UserProfile MFA toggle (LEG-3196)', () => {
  it('returns null when user is absent', () => {
    mockUser.current = null;
    const { container } = render(<UserProfile />);
    expect(container.firstChild).toBeNull();
  });

  it('shows username and MFA Enabled badge when MFA is on', () => {
    mockUser.current = { username: 'admin-operator', mfaEnabled: true };

    render(<UserProfile />);

    expect(screen.getByText('admin-operator')).toBeTruthy();
    expect(screen.getByText(/MFA Enabled/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Enable MFA/i })).toBeNull();
  });

  it('shows Enable MFA button and opens MFASetup; cancel returns to profile', () => {
    mockUser.current = { username: 'new-admin', mfaEnabled: false };

    render(<UserProfile />);

    expect(screen.getByText('new-admin')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Enable MFA/i }));

    expect(screen.getByText('MFA Setup View')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel MFA' }));

    expect(screen.getByText('new-admin')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Enable MFA/i })).toBeTruthy();
    expect(screen.queryByText('MFA Setup View')).toBeNull();
  });
});

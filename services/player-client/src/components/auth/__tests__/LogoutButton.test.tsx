// @vitest-environment jsdom
/**
 * LogoutButton — clicks call AuthContext.logout and navigate to '/'.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockLogout = vi.fn();
const mockNavigate = vi.fn();

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ logout: mockLogout }),
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

import LogoutButton from '../LogoutButton';

describe('LogoutButton', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockLogout.mockReset();
    mockNavigate.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('renders a Logout control and applies an optional className', async () => {
    await act(async () => {
      root.render(<LogoutButton className="nav-logout" />);
    });
    const btn = container.querySelector('button.logout-button') as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(btn.textContent).toBe('Logout');
    expect(btn.className).toContain('nav-logout');
  });

  it('calls logout then navigates home on click', async () => {
    await act(async () => {
      root.render(<LogoutButton />);
    });
    const btn = container.querySelector('button.logout-button') as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    expect(mockLogout).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/');
  });
});

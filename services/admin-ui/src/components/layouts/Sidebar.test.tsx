import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Sidebar from './Sidebar';

vi.mock('../auth/LogoutButton', () => ({
  default: () => <button type="button">Logout</button>,
}));

vi.mock('../ui/SystemHealthStatus', () => ({
  default: () => <div data-testid="system-health" />,
}));

vi.mock('../common/LanguageSwitcher', () => ({
  default: () => <div data-testid="language-switcher" />,
}));

function renderSidebar() {
  return render(
    <MemoryRouter>
      <Sidebar />
    </MemoryRouter>,
  );
}

describe('Sidebar nav groups (LEG-3199)', () => {
  it('shows child links for default expanded groups and mounts footer widgets', () => {
    renderSidebar();

    expect(screen.getByRole('link', { name: /Dashboard/i })).toHaveAttribute('href', '/dashboard');
    expect(screen.getByRole('link', { name: /Users/i })).toHaveAttribute('href', '/users');
    expect(screen.getByRole('link', { name: /Universe Overview/i })).toHaveAttribute('href', '/universe');
    expect(screen.getByTestId('system-health')).toBeTruthy();
    expect(screen.getByTestId('language-switcher')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Logout' })).toBeTruthy();
  });

  it('toggles a group header to collapse and hide child links', () => {
    renderSidebar();

    const playersHeader = screen.getByRole('button', { name: /Player Management/i });
    const playersGroup = playersHeader.closest('.sidebar-nav-group');
    expect(playersGroup).toBeTruthy();
    expect(within(playersGroup as HTMLElement).getByRole('link', { name: /Users/i })).toBeTruthy();

    fireEvent.click(playersHeader);

    expect(within(playersGroup as HTMLElement).queryByRole('link', { name: /Users/i })).toBeNull();
  });

  it('includes key security and analytics routes when groups are expanded', () => {
    renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: /Security & Admin/i }));
    fireEvent.click(screen.getByRole('button', { name: /Analytics & AI/i }));

    expect(screen.getByRole('link', { name: /Security/i })).toHaveAttribute('href', '/security');
    expect(screen.getByRole('link', { name: /Analytics/i })).toHaveAttribute('href', '/analytics');
  });
});

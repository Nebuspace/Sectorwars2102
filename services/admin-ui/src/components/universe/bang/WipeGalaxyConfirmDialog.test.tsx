import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import WipeGalaxyConfirmDialog from './WipeGalaxyConfirmDialog';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (!opts) return key;
      const extra = Object.entries(opts)
        .filter(([k]) => k !== 'defaultValue')
        .map(([k, v]) => `${k}=${v}`)
        .join(' ');
      return extra ? `${key} ${extra}` : key;
    },
  }),
}));

describe('WipeGalaxyConfirmDialog typed-confirm (LEG-3252)', () => {
  const onCancel = vi.fn();
  const onConfirm = vi.fn();

  beforeEach(() => {
    onCancel.mockReset();
    onConfirm.mockReset();
  });

  it('keeps confirm disabled until the typed name matches exactly (case-sensitive, no trim)', () => {
    render(
      <WipeGalaxyConfirmDialog
        galaxyName="Andromeda"
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    const confirm = screen.getByRole('button', { name: 'bang.wipe.confirm' });
    expect(confirm).toHaveProperty('disabled', true);

    const input = screen.getByLabelText('bang.wipe.prompt');
    fireEvent.change(input, { target: { value: 'andromeda' } });
    expect(confirm).toHaveProperty('disabled', true);
    expect(screen.getByText('bang.wipe.mismatch')).toBeTruthy();

    fireEvent.change(input, { target: { value: 'Andromeda ' } });
    expect(confirm).toHaveProperty('disabled', true);

    fireEvent.change(input, { target: { value: 'Andromeda' } });
    expect(confirm).toHaveProperty('disabled', false);
    expect(screen.queryByText('bang.wipe.mismatch')).toBeNull();
  });

  it('submits the typed name to onConfirm', () => {
    render(
      <WipeGalaxyConfirmDialog
        galaxyName="Andromeda"
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    fireEvent.change(screen.getByLabelText('bang.wipe.prompt'), {
      target: { value: 'Andromeda' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'bang.wipe.confirm' }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith('Andromeda');
  });

  it('calls onCancel on overlay click when not busy, not on inner panel click', () => {
    render(
      <WipeGalaxyConfirmDialog
        galaxyName="Andromeda"
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    fireEvent.click(screen.getByRole('dialog'));
    expect(onCancel).toHaveBeenCalledTimes(1);

    onCancel.mockClear();
    fireEvent.click(document.querySelector('.wipe-galaxy-panel')!);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('does not close on overlay click while busy and disables confirm', () => {
    render(
      <WipeGalaxyConfirmDialog
        galaxyName="Andromeda"
        onCancel={onCancel}
        onConfirm={onConfirm}
        busy
      />,
    );

    expect(screen.getByRole('button', { name: 'bang.wipe.wiping' })).toHaveProperty(
      'disabled',
      true,
    );
    fireEvent.click(screen.getByRole('dialog'));
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('surfaces the parent error string', () => {
    render(
      <WipeGalaxyConfirmDialog
        galaxyName="Andromeda"
        onCancel={onCancel}
        onConfirm={onConfirm}
        error="wipe failed"
      />,
    );

    expect(screen.getByText('wipe failed')).toBeTruthy();
  });
});

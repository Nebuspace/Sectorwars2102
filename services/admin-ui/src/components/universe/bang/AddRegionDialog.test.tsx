import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import AddRegionDialog from './AddRegionDialog';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

describe('AddRegionDialog form and clamp (LEG-3181)', () => {
  const onCancel = vi.fn();
  const onConfirm = vi.fn();

  beforeEach(() => {
    onCancel.mockReset();
    onConfirm.mockReset();
    onConfirm.mockResolvedValue(undefined);
  });

  it('clamps sectors to [100, 1000] on submit', async () => {
    render(
      <AddRegionDialog onCancel={onCancel} onConfirm={onConfirm} />,
    );

    const sectorsInput = screen.getByLabelText('bang.addRegion.sectors');
    fireEvent.change(sectorsInput, { target: { value: '50' } });

    const form = sectorsInput.closest('form');
    expect(form).toBeTruthy();
    form!.noValidate = true;
    fireEvent.submit(form!);

    await waitFor(() => {
      expect(onConfirm).toHaveBeenCalled();
    });
    const [, clampedSectors] = onConfirm.mock.calls[0];
    expect(clampedSectors).toBe(100);
  });

  it('disables submit when busy', () => {
    render(
      <AddRegionDialog onCancel={onCancel} onConfirm={onConfirm} busy />,
    );

    expect(screen.getByRole('button', { name: 'bang.addRegion.busy' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'bang.addRegion.cancel' })).toBeDisabled();
  });

  it('surfaces parent error string', () => {
    render(
      <AddRegionDialog
        onCancel={onCancel}
        onConfirm={onConfirm}
        error="Region seed already in use"
      />,
    );

    expect(screen.getByText('Region seed already in use')).toBeTruthy();
  });

  it('calls onCancel from cancel button', () => {
    render(
      <AddRegionDialog onCancel={onCancel} onConfirm={onConfirm} />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'bang.addRegion.cancel' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('calls onCancel when overlay is clicked', () => {
    render(
      <AddRegionDialog onCancel={onCancel} onConfirm={onConfirm} />,
    );

    fireEvent.click(screen.getByRole('dialog'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

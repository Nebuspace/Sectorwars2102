import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MFAVerification } from './MFAVerification';

describe('MFAVerification', () => {
  it('disables Verify until 6 digits are entered', async () => {
    const user = userEvent.setup();
    render(<MFAVerification onVerify={vi.fn()} />);
    const submit = screen.getByRole('button', { name: /verify/i });
    expect(submit).toBeDisabled();

    const input = screen.getByPlaceholderText('000000');
    await user.type(input, '123');
    expect(submit).toBeDisabled();

    await user.type(input, '456');
    expect(submit).not.toBeDisabled();
  });

  it('strips non-digit characters and caps input at 6 digits', async () => {
    const user = userEvent.setup();
    render(<MFAVerification onVerify={vi.fn()} />);
    const input = screen.getByPlaceholderText('000000') as HTMLInputElement;
    await user.type(input, '12a3-4567890');
    expect(input.value).toBe('123456');
  });

  it('calls onVerify with the code on submit and shows external error on failure', async () => {
    const onVerify = vi.fn().mockRejectedValue(new Error('bad code'));
    const { rerender } = render(<MFAVerification onVerify={onVerify} />);
    const input = screen.getByPlaceholderText('000000');
    fireEvent.change(input, { target: { value: '654321' } });
    fireEvent.click(screen.getByRole('button', { name: /verify/i }));

    await waitFor(() => expect(onVerify).toHaveBeenCalledWith('654321'));
    await waitFor(() => expect(screen.getByText('bad code')).toBeInTheDocument());

    // External error prop takes precedence when provided.
    rerender(<MFAVerification onVerify={onVerify} error="server says no" />);
    expect(screen.getByText('server says no')).toBeInTheDocument();
  });

  it('renders Cancel only when onCancel is provided, and calls it on click', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    const { rerender } = render(<MFAVerification onVerify={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument();

    rerender(<MFAVerification onVerify={vi.fn()} onCancel={onCancel} />);
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalled();
  });

  it('renders the backup-code link only when onUseBackupCode is provided', () => {
    const { rerender } = render(<MFAVerification onVerify={vi.fn()} />);
    expect(screen.queryByText(/use a backup code/i)).not.toBeInTheDocument();

    rerender(<MFAVerification onVerify={vi.fn()} onUseBackupCode={vi.fn()} />);
    expect(screen.getByText(/use a backup code/i)).toBeInTheDocument();
  });
});

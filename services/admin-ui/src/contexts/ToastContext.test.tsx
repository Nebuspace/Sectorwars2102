import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { ToastProvider, useToast, useConfirm } from './ToastContext';

function ToastProbe() {
  const toast = useToast();
  return (
    <div>
      <button type="button" onClick={() => toast.success('Saved OK')}>
        fire-success
      </button>
      <button type="button" onClick={() => toast.error('Boom')}>
        fire-error
      </button>
    </div>
  );
}

function ConfirmProbe({
  onResult,
  options,
}: {
  onResult: (v: boolean) => void;
  options: Parameters<ReturnType<typeof useConfirm>>[0];
}) {
  const confirm = useConfirm();
  return (
    <button
      type="button"
      onClick={() => {
        void confirm(options).then(onResult);
      }}
    >
      open-confirm
    </button>
  );
}

describe('ToastContext / ToastProvider (LEG-3299)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders toast.success then auto-dismisses after 5s', () => {
    render(
      <ToastProvider>
        <ToastProbe />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'fire-success' }));
    expect(screen.getByText('Saved OK')).toBeTruthy();
    expect(document.querySelector('.toast-success')).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.queryByText('Saved OK')).toBeNull();
  });

  it('renders toast.error then removes on dismiss click', () => {
    render(
      <ToastProvider>
        <ToastProbe />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'fire-error' }));
    expect(screen.getByText('Boom')).toBeTruthy();
    expect(document.querySelector('.toast-error')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification' }));
    expect(screen.queryByText('Boom')).toBeNull();
  });

  it('useConfirm resolves true on Confirm and false on Cancel', async () => {
    vi.useRealTimers();
    const onResult = vi.fn();
    render(
      <ToastProvider>
        <ConfirmProbe
          onResult={onResult}
          options={{ message: 'Delete this?' }}
        />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'open-confirm' }));
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText('Delete this?')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));

    onResult.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'open-confirm' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
  });

  it('useConfirm resolves false on overlay click', async () => {
    vi.useRealTimers();
    const onResult = vi.fn();
    render(
      <ToastProvider>
        <ConfirmProbe
          onResult={onResult}
          options={{ message: 'Sure?' }}
        />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'open-confirm' }));
    fireEvent.click(document.querySelector('.confirm-overlay')!);
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
  });

  it('typeToConfirm keeps Confirm disabled until exact match (trim on typed)', () => {
    vi.useRealTimers();
    render(
      <ToastProvider>
        <ConfirmProbe
          onResult={() => undefined}
          options={{ message: 'Wipe?', typeToConfirm: 'WIPE' }}
        />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'open-confirm' }));
    const confirmBtn = screen.getByRole('button', { name: 'Confirm' });
    expect(confirmBtn).toHaveProperty('disabled', true);

    const input = screen.getByPlaceholderText('Type "WIPE" to confirm');
    fireEvent.change(input, { target: { value: 'wipe' } });
    expect(confirmBtn).toHaveProperty('disabled', true);

    fireEvent.change(input, { target: { value: 'WIPE' } });
    expect(confirmBtn).toHaveProperty('disabled', false);

    // trim on typed side: trailing spaces still match after trim
    fireEvent.change(input, { target: { value: 'WIPE  ' } });
    expect(confirmBtn).toHaveProperty('disabled', false);
  });

  it('honors danger styling on dialog and confirm button', () => {
    vi.useRealTimers();
    render(
      <ToastProvider>
        <ConfirmProbe
          onResult={() => undefined}
          options={{ message: 'Dangerous?', danger: true }}
        />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'open-confirm' }));
    expect(document.querySelector('.confirm-dialog.danger')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Confirm' }).className).toContain(
      'danger',
    );
  });
});

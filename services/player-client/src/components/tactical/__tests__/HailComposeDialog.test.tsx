// @vitest-environment jsdom
/**
 * HailComposeDialog — portal dialog: send/cancel/Escape, busy, error alert.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import HailComposeDialog from '../HailComposeDialog';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('HailComposeDialog', () => {
  let mountPoint: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mountPoint = document.createElement('div');
    document.body.appendChild(mountPoint);
    root = createRoot(mountPoint);
    vi.spyOn(performance, 'now').mockReturnValue(10_000);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    mountPoint.remove();
    document.querySelectorAll('.confirm-dialog-overlay').forEach((el) => el.remove());
    vi.restoreAllMocks();
  });

  it('renders hail chrome and sends when Send is clicked', async () => {
    const onSend = vi.fn();
    const onChange = vi.fn();
    await act(async () => {
      root.render(
        <HailComposeDialog
          contactName="Raven"
          value="Hello"
          onChange={onChange}
          onSend={onSend}
          onCancel={vi.fn()}
          busy={false}
        />,
      );
    });

    const dialog = document.querySelector('.confirm-dialog-panel');
    expect(dialog?.getAttribute('role')).toBe('dialog');
    expect(dialog?.getAttribute('aria-label')).toBe('Hail message to Raven');
    expect(document.body.textContent).toContain('HAIL — Raven');

    await act(async () => {
      (document.querySelector('.confirm-dialog-btn.confirm') as HTMLButtonElement).click();
    });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it('disables Send when empty or busy; shows error alert', async () => {
    await act(async () => {
      root.render(
        <HailComposeDialog
          contactName="Raven"
          value="   "
          onChange={vi.fn()}
          onSend={vi.fn()}
          onCancel={vi.fn()}
          busy={false}
          error="Channel jammed"
        />,
      );
    });
    expect(
      (document.querySelector('.confirm-dialog-btn.confirm') as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(document.querySelector('#hail-compose-error')?.getAttribute('role')).toBe('alert');
    expect(document.body.textContent).toContain('Channel jammed');

    await act(async () => {
      root.render(
        <HailComposeDialog
          contactName="Raven"
          value="Ping"
          onChange={vi.fn()}
          onSend={vi.fn()}
          onCancel={vi.fn()}
          busy
        />,
      );
    });
    expect(
      (document.querySelector('.confirm-dialog-btn.confirm') as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(document.body.textContent).toContain('…');
  });

  it('cancels on Cancel and Escape when not busy', async () => {
    const onCancel = vi.fn();
    await act(async () => {
      root.render(
        <HailComposeDialog
          contactName="Raven"
          value="x"
          onChange={vi.fn()}
          onSend={vi.fn()}
          onCancel={onCancel}
          busy={false}
        />,
      );
    });

    await act(async () => {
      (document.querySelector('.confirm-dialog-btn.cancel') as HTMLButtonElement).click();
    });
    expect(onCancel).toHaveBeenCalledTimes(1);

    onCancel.mockClear();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('does not cancel via Escape while busy', async () => {
    const onCancel = vi.fn();
    await act(async () => {
      root.render(
        <HailComposeDialog
          contactName="Raven"
          value="x"
          onChange={vi.fn()}
          onSend={vi.fn()}
          onCancel={onCancel}
          busy
        />,
      );
    });
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onCancel).not.toHaveBeenCalled();
  });
});

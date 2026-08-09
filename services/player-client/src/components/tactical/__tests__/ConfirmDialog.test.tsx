// @vitest-environment jsdom
/**
 * ConfirmDialog — confirm/cancel, notice mode, Escape, overlay grace window.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ConfirmDialog from '../ConfirmDialog';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('ConfirmDialog', () => {
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
    // Portal leftovers
    document.querySelectorAll('.confirm-dialog-overlay').forEach((el) => el.remove());
    vi.restoreAllMocks();
  });

  it('renders title/message and calls onConfirm from the confirm button', async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();

    await act(async () => {
      root.render(
        <ConfirmDialog
          title="DOCKING REQUEST"
          message={'Approach cleared.\nConfirm dock?'}
          confirmLabel="Dock"
          onConfirm={onConfirm}
          onCancel={onCancel}
        />,
      );
    });

    const dialog = document.querySelector('.confirm-dialog-panel');
    expect(dialog?.getAttribute('role')).toBe('dialog');
    expect(dialog?.getAttribute('aria-label')).toBe('DOCKING REQUEST');
    expect(document.body.textContent).toContain('Approach cleared.');
    expect(document.querySelector('.confirm-dialog-btn.cancel')).toBeTruthy();

    await act(async () => {
      (document.querySelector('.confirm-dialog-btn.confirm') as HTMLButtonElement).click();
    });
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('calls onCancel from the cancel button', async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();

    await act(async () => {
      root.render(
        <ConfirmDialog title="ABORT" message="Leave?" onConfirm={onConfirm} onCancel={onCancel} />,
      );
    });

    await act(async () => {
      (document.querySelector('.confirm-dialog-btn.cancel') as HTMLButtonElement).click();
    });
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('notice mode (no onCancel) shows a single button and Escape acknowledges', async () => {
    const onConfirm = vi.fn();

    await act(async () => {
      root.render(
        <ConfirmDialog title="NOTICE" message="Systems nominal." confirmLabel="Ack" onConfirm={onConfirm} />,
      );
    });

    expect(document.querySelector('.confirm-dialog-btn.cancel')).toBeNull();
    expect(document.querySelectorAll('.confirm-dialog-btn').length).toBe(1);

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('Escape invokes onCancel when cancel mode is active', async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();

    await act(async () => {
      root.render(
        <ConfirmDialog title="CONFIRM" message="Sure?" onConfirm={onConfirm} onCancel={onCancel} />,
      );
    });

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('ignores overlay mousedown within the 250ms grace window, then dismisses', async () => {
    const onCancel = vi.fn();
    let now = 10_000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);

    await act(async () => {
      root.render(
        <ConfirmDialog title="GRACE" message="Wait" onConfirm={vi.fn()} onCancel={onCancel} />,
      );
    });

    const overlay = document.querySelector('.confirm-dialog-overlay') as HTMLElement;

    await act(async () => {
      now = 10_100; // +100ms < 250
      overlay.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });
    expect(onCancel).not.toHaveBeenCalled();

    await act(async () => {
      now = 10_400; // +400ms > 250
      overlay.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });
    expect(onCancel).toHaveBeenCalledOnce();
  });
});

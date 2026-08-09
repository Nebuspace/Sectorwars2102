// @vitest-environment jsdom
/**
 * NicknameConfirm — UI shell over nicknameConfirmLogic (prompt / yes / decline / retry).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import NicknameConfirm from '../NicknameConfirm';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('NicknameConfirm', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
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

  it('prompts with the extracted callsign and confirms Yes', async () => {
    const onResolved = vi.fn();
    await act(async () => {
      root.render(<NicknameConfirm extractedName="Nova" onResolved={onResolved} />);
    });

    expect(container.textContent).toContain('Register callsign "Nova"?');
    await act(async () => {
      (container.querySelector('.nickname-confirm-yes') as HTMLButtonElement).click();
    });
    expect(onResolved).toHaveBeenCalledWith({ confirmed: true, override: null });
    expect(container.querySelector('.nickname-confirm')).toBeNull();
  });

  it('declines from the prompt via No', async () => {
    const onResolved = vi.fn();
    await act(async () => {
      root.render(<NicknameConfirm extractedName="Nova" onResolved={onResolved} />);
    });

    await act(async () => {
      (container.querySelector('.nickname-confirm-decline') as HTMLButtonElement).click();
    });
    expect(onResolved).toHaveBeenCalledWith({ confirmed: false, override: null });
  });

  it('renders nothing when there is no extracted name (skip)', async () => {
    const onResolved = vi.fn();
    await act(async () => {
      root.render(<NicknameConfirm extractedName={null} onResolved={onResolved} />);
    });
    // Skip is terminal in the shell (step==='skip') — onResolved is the
    // caller's concern via OutcomeDisplay; this component just mounts empty.
    expect(container.querySelector('.nickname-confirm')).toBeNull();
    expect(onResolved).not.toHaveBeenCalled();
  });

  it('enters retry on invalid extracted confirm and can bail without a callsign', async () => {
    const onResolved = vi.fn();
    await act(async () => {
      root.render(<NicknameConfirm extractedName="ab" onResolved={onResolved} />);
    });

    await act(async () => {
      (container.querySelector('.nickname-confirm-yes') as HTMLButtonElement).click();
    });
    expect(container.textContent).toContain('Callsigns must be 3-20 characters');
    expect(container.textContent).toMatch(/2 attempts remaining/);
    expect(container.querySelector('.nickname-confirm-input')).toBeTruthy();

    await act(async () => {
      (container.querySelector('.nickname-confirm-decline') as HTMLButtonElement).click();
    });
    expect(onResolved).toHaveBeenCalledWith({ confirmed: false, override: null });
  });
});

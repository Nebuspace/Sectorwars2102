// @vitest-environment jsdom
/**
 * MFDPageBoundary — fault UI, RETRY remount, resetKey clear.
 */
import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MFDPageBoundary from '../MFDPageBoundary';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function ThrowingChild(): React.ReactElement {
  throw new Error('simulated MFD page crash');
}

function ControllableChild({ shouldThrow }: { shouldThrow: boolean }): React.ReactElement {
  if (shouldThrow) throw new Error('simulated MFD page crash');
  return <div data-testid="page-ok">page body</div>;
}

describe('MFDPageBoundary', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let consoleSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    consoleSpy.mockRestore();
  });

  it('renders children when the page body is healthy', async () => {
    await act(async () => {
      root.render(
        <MFDPageBoundary resetKey="nav">
          <div>healthy page</div>
        </MFDPageBoundary>,
      );
    });
    expect(container.textContent).toBe('healthy page');
    expect(container.querySelector('.mfd-fault')).toBeNull();
  });

  it('shows PAGE FAULT + RETRY when the child throws', async () => {
    await act(async () => {
      root.render(
        <MFDPageBoundary resetKey="nav">
          <ThrowingChild />
        </MFDPageBoundary>,
      );
    });
    const fault = container.querySelector('.mfd-fault');
    expect(fault).toBeTruthy();
    expect(fault?.getAttribute('role')).toBe('alert');
    expect(container.textContent).toContain('PAGE FAULT');
    expect(container.querySelector('.mfd-fault-retry')).toBeTruthy();
  });

  it('RETRY remounts the page body after a fault', async () => {
    // Heal control lives outside the boundary — after fault the page subtree is gone.
    function Outer() {
      const [shouldThrow, setShouldThrow] = useState(true);
      return (
        <>
          <button type="button" data-testid="heal" onClick={() => setShouldThrow(false)}>
            heal
          </button>
          <MFDPageBoundary resetKey="nav">
            <ControllableChild shouldThrow={shouldThrow} />
          </MFDPageBoundary>
        </>
      );
    }

    await act(async () => {
      root.render(<Outer />);
    });
    expect(container.querySelector('.mfd-fault')).toBeTruthy();

    await act(async () => {
      (container.querySelector('[data-testid="heal"]') as HTMLButtonElement).click();
    });
    // Still faulted until RETRY / resetKey — children not remounted yet.
    expect(container.querySelector('.mfd-fault')).toBeTruthy();

    await act(async () => {
      (container.querySelector('.mfd-fault-retry') as HTMLButtonElement).click();
    });
    expect(container.querySelector('.mfd-fault')).toBeNull();
    expect(container.querySelector('[data-testid="page-ok"]')).toBeTruthy();
  });

  it('clears the fault when resetKey changes (page switch)', async () => {
    function Outer({ resetKey, shouldThrow }: { resetKey: string; shouldThrow: boolean }) {
      return (
        <MFDPageBoundary resetKey={resetKey}>
          <ControllableChild shouldThrow={shouldThrow} />
        </MFDPageBoundary>
      );
    }

    await act(async () => {
      root.render(<Outer resetKey="nav" shouldThrow />);
    });
    expect(container.querySelector('.mfd-fault')).toBeTruthy();

    await act(async () => {
      root.render(<Outer resetKey="comms" shouldThrow={false} />);
    });
    expect(container.querySelector('.mfd-fault')).toBeNull();
    expect(container.querySelector('[data-testid="page-ok"]')).toBeTruthy();
  });
});

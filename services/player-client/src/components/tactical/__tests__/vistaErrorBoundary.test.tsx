// @vitest-environment jsdom
/**
 * VistaErrorBoundary — focused unit tests
 *
 * Asserts the two core contracts of the error boundary that gates the Vista
 * engine overlay in SolarSystemViewscreen:
 *
 *   (a) A child that throws during render → onError is called once + the
 *       boundary renders nothing (null).  This is the "fallback" path: the
 *       parent sets vistaEngineFailed=true and the legacy drawLandedScene
 *       on the canvas behind the overlay takes over — windshield not blank.
 *
 *   (b) A child that renders safely → onError is NOT called and the child
 *       is visible in the DOM.
 */

import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { VistaErrorBoundary } from '../VistaErrorBoundary';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** A component that unconditionally throws during render (simulates a Vista engine crash). */
function ThrowingChild(): React.ReactElement {
  throw new Error('simulated vista engine failure');
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe('VistaErrorBoundary', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
  });

  // -------------------------------------------------------------------------
  // (a) Child throws → onError fired, null render (not blank windshield)
  // -------------------------------------------------------------------------
  it('calls onError once and renders nothing when the child throws', async () => {
    // Suppress React's own console.error output for the expected boundary catch.
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const onError = vi.fn();

    await act(async () => {
      root.render(
        <VistaErrorBoundary onError={onError}>
          <ThrowingChild />
        </VistaErrorBoundary>
      );
    });

    // onError must have been called exactly once (not zero, not multiple times).
    expect(onError).toHaveBeenCalledOnce();

    // The boundary renders null on catch — no DOM content left in the container.
    // (The real fallback is the legacy canvas drawLandedScene running behind the
    // now-removed overlay; we assert the boundary's half of that contract here.)
    expect(container.textContent).toBe('');

    consoleSpy.mockRestore();
  });

  // -------------------------------------------------------------------------
  // (b) Child renders safely → onError not called, child visible
  // -------------------------------------------------------------------------
  it('renders children normally and does not call onError when no error occurs', async () => {
    const onError = vi.fn();

    await act(async () => {
      root.render(
        <VistaErrorBoundary onError={onError}>
          <div>vista content</div>
        </VistaErrorBoundary>
      );
    });

    expect(onError).not.toHaveBeenCalled();
    expect(container.textContent).toBe('vista content');
  });
});

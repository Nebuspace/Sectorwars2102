/**
 * VistaErrorBoundary — React error boundary for the Vista engine overlay.
 *
 * Class component is required: function components cannot be error boundaries.
 * Catches render-phase throws from VistaCanvas; calls onError so the parent
 * can set its engineFailed flag and let drawLandedScene resume on the legacy
 * canvas.  Renders null on catch — the parent unmounts the boundary entirely
 * once engineFailed is set, so null is just a transient stop-gap.
 *
 * Draw-phase throws (RAF / setTime) are NOT caught here; they are caught by
 * the onError prop wired directly into VistaCanvas (see src/vista/react.tsx).
 * Both paths converge on the same onError callback in the parent.
 */

import React from 'react';

interface VistaErrorBoundaryProps {
  onError: () => void;
  children: React.ReactNode;
}

interface VistaErrorBoundaryState {
  caught: boolean;
}

export class VistaErrorBoundary extends React.Component<
  VistaErrorBoundaryProps,
  VistaErrorBoundaryState
> {
  state: VistaErrorBoundaryState = { caught: false };

  static getDerivedStateFromError(): VistaErrorBoundaryState {
    return { caught: true };
  }

  componentDidCatch(error: unknown): void {
    // Notify the parent so it can set vistaEngineFailed and resume legacy.
    this.props.onError();
  }

  render(): React.ReactNode {
    if (this.state.caught) {
      // Render nothing: the parent will stop mounting the boundary entirely
      // once onError fires and vistaEngineFailed becomes true.
      return null;
    }
    return this.props.children;
  }
}

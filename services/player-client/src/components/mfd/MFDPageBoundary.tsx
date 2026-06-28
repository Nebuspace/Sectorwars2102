/**
 * MFDPageBoundary — per-page error isolation.
 *
 * Wraps ONLY the page body; the softkey rail and route rail live outside
 * so navigation survives any page crash. A resetKey change (page switch)
 * clears the fault; RETRY bumps a nonce to force a clean remount of the
 * same page.
 */

import React from 'react';

interface MFDPageBoundaryProps {
  resetKey: string;
  children: React.ReactNode;
}

interface MFDPageBoundaryState {
  faulted: boolean;
  nonce: number;
}

class MFDPageBoundary extends React.Component<MFDPageBoundaryProps, MFDPageBoundaryState> {
  state: MFDPageBoundaryState = { faulted: false, nonce: 0 };

  static getDerivedStateFromError(): Partial<MFDPageBoundaryState> {
    return { faulted: true };
  }

  componentDidCatch(error: unknown): void {
    console.error('MFD page fault:', error);
  }

  componentDidUpdate(prevProps: MFDPageBoundaryProps): void {
    if (prevProps.resetKey !== this.props.resetKey && this.state.faulted) {
      this.setState({ faulted: false });
    }
  }

  private handleRetry = (): void => {
    this.setState((prev) => ({ faulted: false, nonce: prev.nonce + 1 }));
  };

  render(): React.ReactNode {
    if (this.state.faulted) {
      return (
        <div className="mfd-fault" role="alert">
          <span className="mfd-fault-title">PAGE FAULT</span>
          <span className="mfd-fault-sub">DISPLAY PROCESSOR HALTED</span>
          <button type="button" className="mfd-fault-retry" onClick={this.handleRetry}>
            RETRY
          </button>
        </div>
      );
    }
    // Nonce as key: RETRY remounts the page subtree from scratch.
    return <React.Fragment key={this.state.nonce}>{this.props.children}</React.Fragment>;
  }
}

export default MFDPageBoundary;

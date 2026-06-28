import React from 'react';
import './cockpit-colony.css';

export interface CockpitPanelProps {
  /** LED header designation, e.g. "CITADEL" */
  title: string;
  /** Per-panel accent color (Law 5) */
  accent: string;
  /** Right-aligned readout in the header, e.g. "Lv 3" / "4/9" / "62%" */
  readout?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}

/**
 * CockpitPanel — the small instrument frame used by the landed-cockpit colony
 * management HUD (Screen 2). A bezel-less accent-tinted card with an LED header
 * carrying a glanceable readout; the body scrolls internally so a deep wrapped
 * manager (grid / research) never blows out the landed console's own scroll.
 */
const CockpitPanel: React.FC<CockpitPanelProps> = ({ title, accent, readout, className, children }) => (
  <div
    className={`cockpit-panel${className ? ` ${className}` : ''}`}
    style={{ '--panel-accent': accent } as React.CSSProperties}
  >
    <div className="cp-header">
      <span className="cp-title">{title}</span>
      {readout !== undefined && readout !== null && <span className="cp-readout">{readout}</span>}
    </div>
    <div className="cp-body">{children}</div>
  </div>
);

export default CockpitPanel;

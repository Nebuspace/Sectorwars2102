import React from 'react';
import './cockpit-instrument.css';

export interface CockpitInstrumentProps {
  /** Monitor designation rendered in the LED header, e.g. "NAV CHART" */
  title: string;
  /** Per-system accent color (Law 5), e.g. "#00D9FF" */
  accent: string;
  /** Optional secondary readout, right-aligned in the LED header */
  subtitle?: string;
  /** Extra class on the outer monitor frame for page-scoped styling */
  className?: string;
  children: React.ReactNode;
}

/**
 * CockpitInstrument — the universal full-page monitor frame (Law 3).
 *
 * Every secondary /game route renders as a console instrument: a metal
 * bezel with corner rivets, an LED header tinted by the system accent
 * (--instrument-accent, Law 5), and a SINGLE internal scroll region
 * (.screen-hud-content). The frame fills the content region exactly;
 * the document never scrolls (Law 2) — only the screen interior does.
 *
 * Pages must keep their own loading/error branches INSIDE the frame so
 * the monitor chrome never unmounts between states.
 */
const CockpitInstrument: React.FC<CockpitInstrumentProps> = ({
  title,
  accent,
  subtitle,
  className,
  children,
}) => {
  return (
    <div
      className={`instrument-monitor${className ? ` ${className}` : ''}`}
      style={{ '--instrument-accent': accent } as React.CSSProperties}
    >
      <div className="monitor-bezel" aria-hidden="true">
        <span className="bezel-corner tl"></span>
        <span className="bezel-corner tr"></span>
        <span className="bezel-corner bl"></span>
        <span className="bezel-corner br"></span>
      </div>
      <div className="monitor-screen">
        <div className="screen-hud-header">
          <span className="instrument-title">{title}</span>
          {subtitle && <span className="instrument-subtitle">{subtitle}</span>}
        </div>
        <div className="screen-hud-content">{children}</div>
      </div>
    </div>
  );
};

export default CockpitInstrument;

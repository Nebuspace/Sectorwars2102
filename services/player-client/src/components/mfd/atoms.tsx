/**
 * atoms — shared presentational primitives for MFD pages.
 *
 * Pages compose these so every screen speaks the same instrument
 * dialect: LED header line, honest empty states, tabular fields.
 */

import React from 'react';
import type { MFDFeatureStatus } from './mfdTypes';
import './mfd.css';

export const MFDPageHeader: React.FC<{
  title: string;
  accent: string;
  status: MFDFeatureStatus;
  // WO-UI0-SHELL-TRANSPLANT integration cleanup (item 2): MFDScreen.tsx
  // now renders its own `<b className="mfd-unit-title">{unit} · {page}</b>`
  // inside `.scr`, so a live-registered MFD page's OWN title here would
  // double-stack. Those pages (mfd/pages/VesselPage, CargoPage, QuantumPage,
  // NavPositionPage, CommsCrewPage) pass `showTitle={false}` to suppress
  // just the title text while keeping this header's non-title chrome (the
  // PARTIAL status chip, the accent LED border). Pages reused OUTSIDE
  // MFDScreen — ReputationPage inside PlayerInfo.tsx's dossier tab is the
  // one live case — keep the default `true` and render their own title as
  // before, since no ancestor supplies it there.
  showTitle?: boolean;
}> = ({ title, accent, status, showTitle = true }) => {
  if (!showTitle && status !== 'partial') return null;
  return (
    <header
      className="mfd-page-header"
      style={{ '--mfd-accent': accent } as React.CSSProperties}
    >
      {showTitle ? <span className="mfd-page-title">{title}</span> : null}
      {status === 'partial' ? <span className="mfd-chip-partial">PARTIAL</span> : null}
      {/* Page-change announcement lives in MFDScreen: a live region that
          remounts with the page is not reliably announced — the region must
          persist and have its CONTENT change. */}
    </header>
  );
};

export const MFDPageBody: React.FC<{
  children: React.ReactNode;
  scrollKey?: string;
}> = ({ children, scrollKey }) => (
  // Stable key keeps the scroll container mounted across data refreshes;
  // changing scrollKey deliberately resets scroll position.
  <div className="mfd-page-body" key={scrollKey}>
    {children}
  </div>
);

export const MFDField: React.FC<{
  label: string;
  value: React.ReactNode;
  accent?: boolean;
}> = ({ label, value, accent = false }) => (
  <div className={accent ? 'mfd-field mfd-field-accent' : 'mfd-field'}>
    <span className="mfd-field-label">{label}</span>
    <span className="mfd-field-value">{value}</span>
  </div>
);

export const MFDEmpty: React.FC<{ text: string }> = ({ text }) => (
  <div className="mfd-empty">{text}</div>
);

export const MFDInsufficient: React.FC<{ text?: string }> = ({
  text = 'INSUFFICIENT DATA',
}) => <div className="mfd-insufficient">{text}</div>;

export const MFDPageSkeleton: React.FC = () => (
  <div className="mfd-skeleton" aria-hidden="true">
    <div className="mfd-skeleton-line mfd-skeleton-w60" />
    <div className="mfd-skeleton-line mfd-skeleton-w80" />
    <div className="mfd-skeleton-line mfd-skeleton-w45" />
    <div className="mfd-skeleton-line mfd-skeleton-w70" />
  </div>
);

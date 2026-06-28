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
}> = ({ title, accent, status }) => (
  <header
    className="mfd-page-header"
    style={{ '--mfd-accent': accent } as React.CSSProperties}
  >
    <span className="mfd-page-title">{title}</span>
    {status === 'partial' ? <span className="mfd-chip-partial">PARTIAL</span> : null}
    {/* Page-change announcement lives in MFDScreen: a live region that
        remounts with the page is not reliably announced — the region must
        persist and have its CONTENT change. */}
  </header>
);

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

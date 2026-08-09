// @vitest-environment jsdom
/**
 * mfd/atoms — header chrome, fields, empty/insufficient, skeleton.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  MFDEmpty,
  MFDField,
  MFDInsufficient,
  MFDPageBody,
  MFDPageHeader,
  MFDPageSkeleton,
} from '../atoms';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('mfd/atoms', () => {
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

  it('MFDPageHeader renders title by default and PARTIAL chip when status is partial', async () => {
    await act(async () => {
      root.render(
        <MFDPageHeader title="VESSEL" accent="#0ff" status="partial" />,
      );
    });
    expect(container.querySelector('.mfd-page-title')?.textContent).toBe('VESSEL');
    expect(container.querySelector('.mfd-chip-partial')?.textContent).toBe('PARTIAL');
  });

  it('MFDPageHeader with showTitle=false and non-partial status renders nothing', async () => {
    await act(async () => {
      root.render(
        <MFDPageHeader title="VESSEL" accent="#0ff" status="live" showTitle={false} />,
      );
    });
    expect(container.querySelector('.mfd-page-header')).toBeNull();
    expect(container.textContent).toBe('');
  });

  it('MFDPageHeader keeps PARTIAL chip when showTitle is false', async () => {
    await act(async () => {
      root.render(
        <MFDPageHeader title="NAV" accent="#0ff" status="partial" showTitle={false} />,
      );
    });
    expect(container.querySelector('.mfd-page-title')).toBeNull();
    expect(container.querySelector('.mfd-chip-partial')).toBeTruthy();
  });

  it('MFDField / Empty / Insufficient / Body / Skeleton render expected chrome', async () => {
    await act(async () => {
      root.render(
        <>
          <MFDField label="FUEL" value="42%" accent />
          <MFDEmpty text="No contacts" />
          <MFDInsufficient />
          <MFDPageBody scrollKey="k1">
            <span>body</span>
          </MFDPageBody>
          <MFDPageSkeleton />
        </>,
      );
    });

    expect(container.querySelector('.mfd-field-accent')).toBeTruthy();
    expect(container.querySelector('.mfd-field-label')?.textContent).toBe('FUEL');
    expect(container.querySelector('.mfd-field-value')?.textContent).toBe('42%');
    expect(container.querySelector('.mfd-empty')?.textContent).toBe('No contacts');
    expect(container.querySelector('.mfd-insufficient')?.textContent).toBe('INSUFFICIENT DATA');
    expect(container.querySelector('.mfd-page-body')?.textContent).toBe('body');
    expect(container.querySelector('.mfd-skeleton')?.getAttribute('aria-hidden')).toBe('true');
  });
});

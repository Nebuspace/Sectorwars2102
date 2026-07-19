// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import CourseConfirmPopup from '../CourseConfirmPopup';
import type { CourseReachable } from '../../../contexts/AutopilotContext';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const course: CourseReachable = {
  success: true,
  reachable: true,
  target_sector_id: 214,
  total_turns: 5,
  hops: [
    {
      sector_id: 101,
      name: 'Alpha',
      turn_cost: 2,
      visited: true,
      safety_rating: 0.8,
      via_tunnel: false,
    },
    {
      sector_id: 214,
      name: 'Beta Deep',
      turn_cost: 3,
      visited: false,
      safety_rating: null,
      via_tunnel: true,
    },
  ],
};

describe('CourseConfirmPopup UI', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('renders destination, turns, and uncharted risk without engaging', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    act(() => {
      root.render(
        <CourseConfirmPopup course={course} onConfirm={onConfirm} onCancel={onCancel} />,
      );
    });

    expect(container.textContent).toContain('LAY IN COURSE');
    expect(container.textContent).toContain('Beta Deep');
    expect(container.textContent).toContain('5 TURNS');
    expect(container.textContent).toContain('UNCHARTED CONDITIONS');
    expect(container.textContent).toMatch(/COMMIT 5 TURNS/);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('Cancel does not commit', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    act(() => {
      root.render(
        <CourseConfirmPopup course={course} onConfirm={onConfirm} onCancel={onCancel} />,
      );
    });
    const cancel = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Cancel',
    );
    expect(cancel).toBeTruthy();
    act(() => { cancel!.click(); });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('Confirm fires once', () => {
    const onConfirm = vi.fn();
    act(() => {
      root.render(
        <CourseConfirmPopup course={course} onConfirm={onConfirm} onCancel={() => {}} />,
      );
    });
    const commit = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('COMMIT'),
    );
    act(() => { commit!.click(); });
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

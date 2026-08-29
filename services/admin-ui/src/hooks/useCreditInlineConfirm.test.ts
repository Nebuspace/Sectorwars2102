import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import {
  CONFIRM_ARM_MS,
  CREDIT_CONFIRM_THRESHOLD,
  creditConfirmLabel,
  formatCredits,
  useCreditInlineConfirm,
} from './useCreditInlineConfirm';

describe('useCreditInlineConfirm', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('exports canon threshold and arm duration', () => {
    expect(CREDIT_CONFIRM_THRESHOLD).toBe(1000);
    expect(CONFIRM_ARM_MS).toBe(3000);
  });

  it('formatCredits prefixes the ₡ glyph with grouping', () => {
    expect(formatCredits(5000)).toBe('₡5,000');
    expect(formatCredits(null)).toBe('₡0');
  });

  it('creditConfirmLabel includes amount and optional context', () => {
    expect(creditConfirmLabel(1500)).toBe('Confirm? · ₡1,500');
    expect(creditConfirmLabel(1500, 'refund')).toBe('Confirm? · ₡1,500 refund');
  });

  it('gateCreditAction runs immediately at or below threshold', () => {
    const { result } = renderHook(() => useCreditInlineConfirm());
    const onConfirm = vi.fn();

    act(() => {
      result.current.gateCreditAction('a', 1000, onConfirm);
    });

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(result.current.isArmed('a')).toBe(false);
  });

  it('gateCreditAction arms on first click above threshold and confirms on second', () => {
    const { result } = renderHook(() => useCreditInlineConfirm());
    const onConfirm = vi.fn();

    act(() => {
      result.current.gateCreditAction('b', 1500, onConfirm);
    });
    expect(onConfirm).not.toHaveBeenCalled();
    expect(result.current.isArmed('b')).toBe(true);

    act(() => {
      result.current.gateCreditAction('b', 1500, onConfirm);
    });
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(result.current.isArmed('b')).toBe(false);
  });

  it('disarms after CONFIRM_ARM_MS if untouched', () => {
    const { result } = renderHook(() => useCreditInlineConfirm());
    const onConfirm = vi.fn();

    act(() => {
      result.current.gateCreditAction('c', 2000, onConfirm);
    });
    expect(result.current.isArmed('c')).toBe(true);

    act(() => {
      vi.advanceTimersByTime(CONFIRM_ARM_MS);
    });
    expect(result.current.isArmed('c')).toBe(false);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

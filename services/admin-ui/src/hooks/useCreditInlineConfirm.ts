/**
 * ADR-0093 item 9 / ui-flows.md §3.6 — inline confirm when credit consequence > ₡1,000.
 * Shared admin-ui hook; player-client first venue lives in ContractBoardVenue.tsx.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export const CREDIT_CONFIRM_THRESHOLD = 1000;
export const CONFIRM_ARM_MS = 3000;
export const CREDITS_SYMBOL = '₡';

export const formatCredits = (amount: number | null | undefined): string => {
  const n = typeof amount === 'number' && Number.isFinite(amount) ? amount : 0;
  return `${CREDITS_SYMBOL}${n.toLocaleString()}`;
};

export const creditConfirmLabel = (creditConsequence: number, context?: string): string => {
  const base = `Confirm? · ${formatCredits(creditConsequence)}`;
  return context ? `${base} ${context}` : base;
};

export function useCreditInlineConfirm() {
  const [armedKey, setArmedKey] = useState<string | null>(null);
  const timeoutRef = useRef<number | null>(null);

  const clearArmTimeout = useCallback(() => {
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  useEffect(() => () => clearArmTimeout(), [clearArmTimeout]);

  const disarm = useCallback(() => {
    clearArmTimeout();
    setArmedKey(null);
  }, [clearArmTimeout]);

  const arm = useCallback(
    (key: string) => {
      clearArmTimeout();
      setArmedKey(key);
      timeoutRef.current = window.setTimeout(() => {
        setArmedKey((current) => (current === key ? null : current));
        timeoutRef.current = null;
      }, CONFIRM_ARM_MS);
    },
    [clearArmTimeout],
  );

  const isArmed = useCallback((key: string) => armedKey === key, [armedKey]);

  const gateCreditAction = useCallback(
    (key: string, creditConsequence: number, onConfirm: () => void) => {
      if (creditConsequence <= CREDIT_CONFIRM_THRESHOLD) {
        onConfirm();
        return;
      }
      if (armedKey === key) {
        disarm();
        onConfirm();
        return;
      }
      arm(key);
    },
    [armedKey, arm, disarm],
  );

  const needsCreditConfirm = useCallback(
    (creditConsequence: number) => creditConsequence > CREDIT_CONFIRM_THRESHOLD,
    [],
  );

  return {
    armedKey,
    isArmed,
    arm,
    disarm,
    gateCreditAction,
    needsCreditConfirm,
  };
}

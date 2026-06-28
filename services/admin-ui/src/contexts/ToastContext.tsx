import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from 'react';
import '../components/common/toast.css';

/**
 * App-wide, non-blocking feedback primitives that replace native
 * window.alert()/window.confirm() across the admin UI.
 *
 *   const toast = useToast();
 *   toast.success('Saved');
 *
 *   const confirm = useConfirm();
 *   if (await confirm({ message: 'Delete this?', danger: true })) { ... }
 *
 * Native dialogs freeze all browser automation and clash with the dark shell;
 * these render in-shell, are styled with the design tokens, and the confirm
 * variant returns a Promise so it drops into existing async handlers cleanly.
 */

type ToastVariant = 'success' | 'error' | 'info' | 'warning';

interface ToastItem {
  id: number;
  variant: ToastVariant;
  message: string;
}

interface ToastApi {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
  warning: (message: string) => void;
}

export interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Red styling for destructive actions. */
  danger?: boolean;
  /** When set, the user must type this exact string to enable Confirm. */
  typeToConfirm?: string;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ToastContext = createContext<ToastApi | null>(null);
const ConfirmContext = createContext<ConfirmFn | null>(null);

export const useToast = (): ToastApi => {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within a ToastProvider');
  return ctx;
};

export const useConfirm = (): ConfirmFn => {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error('useConfirm must be used within a ToastProvider');
  return ctx;
};

let nextToastId = 1;

const ICONS: Record<ToastVariant, string> = {
  success: '✓',
  error: '✕',
  warning: '⚠',
  info: 'ℹ',
};

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const remove = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (variant: ToastVariant, message: string) => {
      const id = nextToastId++;
      setToasts((current) => [...current, { id, variant, message }]);
      window.setTimeout(() => remove(id), 5000);
    },
    [remove],
  );

  const toastApi = useMemo<ToastApi>(
    () => ({
      success: (message) => push('success', message),
      error: (message) => push('error', message),
      info: (message) => push('info', message),
      warning: (message) => push('warning', message),
    }),
    [push],
  );

  // --- Confirm dialog ---
  const [confirmState, setConfirmState] = useState<ConfirmOptions | null>(null);
  const [typed, setTyped] = useState('');
  const resolverRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>(
    (options) =>
      new Promise<boolean>((resolve) => {
        // Resolve any still-open confirm as false so its awaiter never hangs.
        resolverRef.current?.(false);
        resolverRef.current = resolve;
        setTyped('');
        setConfirmState(options);
      }),
    [],
  );

  const closeConfirm = useCallback((result: boolean) => {
    resolverRef.current?.(result);
    resolverRef.current = null;
    setConfirmState(null);
    setTyped('');
  }, []);

  const confirmBlocked =
    !!confirmState?.typeToConfirm && typed.trim() !== confirmState.typeToConfirm;

  return (
    <ToastContext.Provider value={toastApi}>
      <ConfirmContext.Provider value={confirm}>
        {children}

        <div className="toast-container" role="status" aria-live="polite">
          {toasts.map((t) => (
            <div
              key={t.id}
              className={`toast toast-${t.variant}`}
              onClick={() => remove(t.id)}
            >
              <span className="toast-icon" aria-hidden="true">{ICONS[t.variant]}</span>
              <span className="toast-message">{t.message}</span>
              <button
                type="button"
                className="toast-close"
                aria-label="Dismiss notification"
                onClick={(e) => {
                  e.stopPropagation();
                  remove(t.id);
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>

        {confirmState && (
          <div className="confirm-overlay" onClick={() => closeConfirm(false)}>
            <div
              className={`confirm-dialog${confirmState.danger ? ' danger' : ''}`}
              role="dialog"
              aria-modal="true"
              onClick={(e) => e.stopPropagation()}
            >
              {confirmState.title && <h3 className="confirm-title">{confirmState.title}</h3>}
              <p className="confirm-message">{confirmState.message}</p>
              {confirmState.typeToConfirm && (
                <input
                  className="confirm-input"
                  autoFocus
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  placeholder={`Type "${confirmState.typeToConfirm}" to confirm`}
                />
              )}
              <div className="confirm-actions">
                <button
                  type="button"
                  className="confirm-btn cancel"
                  onClick={() => closeConfirm(false)}
                >
                  {confirmState.cancelLabel || 'Cancel'}
                </button>
                <button
                  type="button"
                  className={`confirm-btn ${confirmState.danger ? 'danger' : 'primary'}`}
                  disabled={confirmBlocked}
                  onClick={() => closeConfirm(true)}
                >
                  {confirmState.confirmLabel || 'Confirm'}
                </button>
              </div>
            </div>
          </div>
        )}
      </ConfirmContext.Provider>
    </ToastContext.Provider>
  );
};

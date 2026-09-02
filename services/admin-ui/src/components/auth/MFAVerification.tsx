import React, { useState } from 'react';
import { axiosResponseStatus, detailFromResponse, formatAdminApiError } from '../../utils/adminApiError';
import './mfa-verification.css';

const MFA_VERIFY_FALLBACK = 'Verification failed';

function isNetworkTransportFailure(err: unknown): boolean {
  if (err instanceof TypeError) {
    return true;
  }
  if (err instanceof Error) {
    const msg = err.message;
    return msg === 'Failed to fetch' || msg === 'Network Error';
  }
  return false;
}

/** Collapse fetch/network failures; route HTTP status via formatAdminApiError; preserve intentional verify errors. */
export function mfaVerificationError(err: unknown): string {
  if (isNetworkTransportFailure(err)) {
    return formatAdminApiError(err, { fallback: MFA_VERIFY_FALLBACK });
  }
  const status = axiosResponseStatus(err);
  if (status !== undefined) {
    const detail = detailFromResponse(err);
    if (detail) {
      return detail;
    }
    return formatAdminApiError(err, { fallback: MFA_VERIFY_FALLBACK });
  }
  if (err instanceof Error) {
    return err.message;
  }
  return MFA_VERIFY_FALLBACK;
}

interface MFAVerificationProps {
  onVerify: (code: string) => Promise<void>;
  onUseBackupCode?: () => void;
  onCancel?: () => void;
  error?: string | null;
}

export const MFAVerification: React.FC<MFAVerificationProps> = ({
  onVerify,
  onUseBackupCode,
  onCancel,
  error: externalError
}) => {
  const [code, setCode] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [internalError, setInternalError] = useState<string | null>(null);
  
  const error = externalError || internalError;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (code.length !== 6) {
      setInternalError('Please enter a 6-digit code');
      return;
    }

    setLoading(true);
    setInternalError(null);

    try {
      await onVerify(code);
    } catch (err) {
      setInternalError(mfaVerificationError(err));
    } finally {
      setLoading(false);
    }
  };

  const handleCodeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value.replace(/\D/g, '').slice(0, 6);
    setCode(value);
    setInternalError(null);
  };

  return (
    <div className="mfa-verification-container">
      <div className="mfa-verification-header">
        <div className="mfa-icon">
          <i className="fas fa-shield-alt"></i>
        </div>
        <h2>Two-Factor Authentication</h2>
        <p>Enter the 6-digit code from your authenticator app</p>
      </div>

      <form onSubmit={handleSubmit} className="mfa-verification-form">
        {error && (
          <div className="mfa-error">
            <i className="fas fa-exclamation-circle"></i>
            {error}
          </div>
        )}

        <div className="code-input-container">
          <input
            type="text"
            value={code}
            onChange={handleCodeChange}
            placeholder="000000"
            maxLength={6}
            className="mfa-code-input"
            autoComplete="off"
            autoFocus
          />
          <div className="code-input-hint">
            {code.length}/6 digits
          </div>
        </div>

        <div className="mfa-actions">
          {onCancel && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={onCancel}
              disabled={loading}
            >
              Cancel
            </button>
          )}
          <button
            type="submit"
            className="btn btn-primary"
            disabled={loading || code.length !== 6}
          >
            {loading ? (
              <>
                <i className="fas fa-spinner fa-spin"></i>
                Verifying...
              </>
            ) : (
              'Verify'
            )}
          </button>
        </div>

        {onUseBackupCode && (
          <div className="backup-code-link">
            <button
              type="button"
              className="link-button"
              onClick={onUseBackupCode}
              disabled={loading}
            >
              Lost access to your authenticator? Use a backup code
            </button>
          </div>
        )}
      </form>

      <div className="mfa-help">
        <h3>Where to find your code:</h3>
        <ul>
          <li>
            <i className="fas fa-mobile-alt"></i>
            Open your authenticator app (Google Authenticator, Authy, etc.)
          </li>
          <li>
            <i className="fas fa-search"></i>
            Find the SectorWars 2102 entry
          </li>
          <li>
            <i className="fas fa-clock"></i>
            Enter the 6-digit code shown (it changes every 30 seconds)
          </li>
        </ul>
      </div>
    </div>
  );
};
import React, { useState } from 'react';
import { useAuth, MFARequiredError } from '../../contexts/AuthContext';
import './auth.css';

const LOGIN_NETWORK_FALLBACK =
  'Unable to sign in. Please check your connection and try again.';
const LOGIN_CREDENTIAL_FALLBACK = 'Invalid username or password';
const LOGIN_MFA_FALLBACK = 'Invalid authentication code';

const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

/** Collapses fetch TypeError / network noise; preserves structured API detail (LEG-3695). */
export function formatLoginError(err: unknown, options?: { mfa?: boolean }): string {
  if (err instanceof TypeError) {
    return LOGIN_NETWORK_FALLBACK;
  }
  if (typeof err === 'object' && err !== null && 'response' in err) {
    const detail = (err as { response?: { data?: { detail?: unknown } } }).response
      ?.data?.detail;
    if (typeof detail === 'string' && detail.trim() && !isNetworkCollapseMessage(detail)) {
      return detail;
    }
  }
  const raw = err instanceof Error ? err.message : String(err ?? '');
  if (isNetworkCollapseMessage(raw)) {
    return LOGIN_NETWORK_FALLBACK;
  }
  // Malformed JSON / parse noise from transport collapse — never surface raw text.
  if (raw.trim() && /unexpected token|json|syntaxerror/i.test(raw)) {
    return LOGIN_NETWORK_FALLBACK;
  }
  if (options?.mfa) {
    return LOGIN_MFA_FALLBACK;
  }
  if (raw.trim()) {
    return raw;
  }
  return LOGIN_CREDENTIAL_FALLBACK;
}

interface LoginFormProps {
  onLoginSuccess?: () => void;
  switchToRegister?: () => void;
  onClose?: () => void;
}

const LoginForm: React.FC<LoginFormProps> = ({ onLoginSuccess, switchToRegister, onClose }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // WO-FIX-MFA-BYPASS-LOGIN-ROUTES: no existing player-client MFA-entry
  // component was found (searched src/ for "mfa" — only a hit was an
  // unrelated lighting.ts constant; the admin-ui MFAVerification component
  // lives in a separate npm package/service and isn't importable here), so
  // this is a minimal inline prompt rather than a duplicate of one.
  const [mfaRequired, setMfaRequired] = useState(false);
  const [mfaCode, setMfaCode] = useState('');

  const { login, loginWithOAuth } = useAuth();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!username || !password) {
      setError('Please enter both username and password');
      return;
    }
    if (mfaRequired && mfaCode.trim().length === 0) {
      setError('Please enter the code from your authenticator app');
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      await login(username, password, mfaRequired ? mfaCode : undefined);
      if (onLoginSuccess) {
        onLoginSuccess();
      }
      // Navigate to game - use window.location to ensure MainApp re-checks auth state
      window.location.href = '/game';
    } catch (err) {
      if (err instanceof MFARequiredError) {
        // Not a failed login — this account has MFA enabled. Stay on the
        // form, reveal the code field, and let the user retry with it.
        setMfaRequired(true);
        setMfaCode('');
        setError(null);
      } else if (mfaRequired) {
        console.error('MFA verification failed:', err);
        setError(formatLoginError(err, { mfa: true }));
        setMfaCode('');
      } else {
        console.error('Login failed:', err);
        setError(formatLoginError(err));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCancelMfa = () => {
    setMfaRequired(false);
    setMfaCode('');
    setError(null);
  };

  const handleOAuthLogin = (provider: string) => {
    loginWithOAuth(provider);
  };

  return (
    <div className="login-form-container">
      <form className="login-form" onSubmit={handleSubmit}>
        {onClose && (
          <button type="button" className="close-button" onClick={onClose}>
            ✕
          </button>
        )}
        <h2>Access Your Universe</h2>

        {error && <div className="error-message">{error}</div>}

        {!mfaRequired && (
          <>
            <div className="form-group">
              <label htmlFor="username">Commander ID</label>
              <input
                type="text"
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={isSubmitting}
                autoComplete="username"
                placeholder="Enter your commander name"
              />
            </div>

            <div className="form-group">
              <label htmlFor="password">Security Code</label>
              <input
                type="password"
                id="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={isSubmitting}
                autoComplete="current-password"
                placeholder="Enter your password"
              />
            </div>
          </>
        )}

        {mfaRequired && (
          // WO-FIX-MFA-BYPASS-LOGIN-ROUTES: two-factor step — the account's
          // password already checked out; this authenticator code is the
          // second factor before login() is retried.
          <div className="form-group">
            <label htmlFor="mfa-code">Authenticator Code</label>
            <input
              type="text"
              id="mfa-code"
              value={mfaCode}
              onChange={(e) => setMfaCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              disabled={isSubmitting}
              autoComplete="one-time-code"
              inputMode="numeric"
              placeholder="6-digit code"
              autoFocus
            />
          </div>
        )}

        <button
          type="submit"
          className="login-button"
          disabled={isSubmitting}
        >
          {isSubmitting ? 'Launching...' : mfaRequired ? 'Verify' : 'Play Now'}
        </button>

        {mfaRequired && (
          <div className="register-link">
            <button type="button" onClick={handleCancelMfa} className="text-button">
              Use a different account
            </button>
          </div>
        )}

        {!mfaRequired && (
          <>
            <div className="register-link">
              New to Sector Wars? <button type="button" onClick={switchToRegister} className="text-button">Create Account</button>
            </div>

            <div className="oauth-divider">
              <span>Or Sign In With</span>
            </div>

            <div className="oauth-buttons">
              <button
                type="button"
                onClick={() => handleOAuthLogin('steam')}
                className="oauth-button steam-button"
              >
                Steam
              </button>
              <button
                type="button"
                onClick={() => handleOAuthLogin('github')}
                className="oauth-button github-button"
              >
                GitHub
              </button>
              <button
                type="button"
                onClick={() => handleOAuthLogin('google')}
                className="oauth-button google-button"
              >
                Google
              </button>
            </div>
          </>
        )}
      </form>
    </div>
  );
};

export default LoginForm;
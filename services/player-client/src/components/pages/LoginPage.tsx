import React from 'react';
import LoginForm from '../auth/LoginForm';
import LanguageSwitcher from '../common/LanguageSwitcher';
import '../auth/auth.css';

interface LoginPageProps {
  onLoginSuccess?: () => void;
  switchToRegister?: () => void;
  onClose?: () => void;
}

/**
 * Login surface wrapper — canon mount for LanguageSwitcher (ui-flows.md /
 * i18n.md / player-client.md). LoginForm still owns the modal card; the
 * switcher sits at the overlay corner so it stays visible without scroll.
 */
const LoginPage: React.FC<LoginPageProps> = (props) => {
  return (
    <div className="login-page">
      <div className="login-page-language">
        <LanguageSwitcher variant="compact" showProgress={false} />
      </div>
      <LoginForm {...props} />
    </div>
  );
};

export default LoginPage;

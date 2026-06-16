import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import axios from 'axios';
import apiClient from '../../services/apiClient';
import { useAuth } from '../../contexts/AuthContext';

const OAuthCallback: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const [status, setStatus] = useState<string>('Processing authentication...');
  const [error, setError] = useState<string | null>(null);
  const [userInfo, setUserInfo] = useState<any>(null);
  const [playerInfo, setPlayerInfo] = useState<any>(null);
  const { refreshToken: authRefreshToken } = useAuth();

  useEffect(() => {
    const handleOAuthCallback = async () => {
      try {
        // Parse the URL search params
        const params = new URLSearchParams(location.search);

        // ADR-0085: prefer the single-use authorization code — tokens are no
        // longer placed in the redirect URL (they would leak to history /
        // Referer / logs). Fall back to legacy URL tokens during the transition
        // window so this stays compatible until the server flips to code-only.
        let accessToken = params.get('access_token');
        let refreshToken = params.get('refresh_token');
        let userId = params.get('user_id');
        // Check for new user indicators either from query param or in session storage
        let isNewUser = params.get('is_new_user') === 'true' || sessionStorage.getItem('oauth_register') === 'true';

        const code = params.get('code');
        if (code) {
          const { data } = await apiClient.post('/api/v1/auth/exchange', { code });
          accessToken = data.access_token;
          refreshToken = data.refresh_token;
          userId = data.user_id;
          if (data.is_new_user) isNewUser = true;
        }

        if (!accessToken || !refreshToken || !userId) {
          throw new Error(`Invalid OAuth callback parameters: code=${Boolean(code)}, accessToken=${Boolean(accessToken)}, refreshToken=${Boolean(refreshToken)}, userId=${Boolean(userId)}`);
        }

        // Store tokens in localStorage
        localStorage.setItem('accessToken', accessToken);
        localStorage.setItem('refreshToken', refreshToken);
        localStorage.setItem('userId', userId);

        // Set default Authorization header for future requests
        axios.defaults.headers.common['Authorization'] = `Bearer ${accessToken}`;

        // Clear the oauth_register flag from session storage
        sessionStorage.removeItem('oauth_register');

        if (isNewUser) {
          setStatus('Registration successful! Setting up your account...');
        } else {
          setStatus('Login successful! Launching game...');
        }

        // NOTE: Removed API calls from OAuth callback due to GitHub Codespaces authentication requirements
        // The main app will fetch user/player info after redirect using proper authentication context
        setStatus(`Login successful! Launching game...`);

        // Force a reload of authentication state in the AuthContext
        try {
          await authRefreshToken();
        } catch {
          // Continue anyway, as we'll do a full page reload
        }
        
        // Set a sessionStorage flag to track the redirect
        sessionStorage.setItem('oauth_redirect_completed', 'true');
        
        // Create a function to directly navigate to the dashboard with the token
        const navigateWithToken = () => {
          try {
            // Make sure the token is correctly set in localStorage
            if (localStorage.getItem('accessToken') !== accessToken) {
              localStorage.setItem('accessToken', accessToken);
              localStorage.setItem('refreshToken', refreshToken);
              localStorage.setItem('userId', userId);
              
              // Set axios defaults for current session
              axios.defaults.headers.common['Authorization'] = `Bearer ${accessToken}`;
            }
            
            window.location.href = '/game';
          } catch (err) {
            console.error('Error during navigation:', err);
            // Fallback to simple navigation
            window.location.href = '/';
          }
        };
        
        // Redirect after a brief delay
        setTimeout(navigateWithToken, 1500);
      } catch (error) {
        console.error('OAuth callback error:', error);
        setError('Authentication failed. Please try again.');
      }
    };

    handleOAuthCallback();
  }, [location, navigate, authRefreshToken]);

  return (
    <div className="oauth-callback-container">
      {error ? (
        <div className="error-message">
          <h3>Authentication Error</h3>
          <p>{error}</p>
          <button 
            onClick={() => navigate('/')} 
            className="login-button"
          >
            Back to Login
          </button>
        </div>
      ) : (
        <>
          <div className="loading-spinner"></div>
          <p>{status}</p>
          <div style={{marginTop: '20px', padding: '10px', border: '1px solid #ccc', borderRadius: '5px'}}>
            <h4>Authentication Status:</h4>
            <p>✅ GitHub OAuth successful</p>
            <p>✅ Access token stored</p>
            <p>⏳ Redirecting to game dashboard...</p>
            <p><small>Player data will be loaded in the main application</small></p>
          </div>
        </>
      )}
    </div>
  );
};

export default OAuthCallback;
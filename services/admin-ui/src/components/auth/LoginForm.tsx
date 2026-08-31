import React, { useState, useEffect } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { axiosResponseStatus, detailFromResponse, formatAdminApiError } from '../../utils/adminApiError';
import { MFAVerification } from './MFAVerification';

const loginApiError = (err: unknown): string => {
  const status = axiosResponseStatus(err);
  if (status === 401) {
    return 'Invalid username or password';
  }
  if (status === 400) {
    return 'Invalid login request. Please check your input.';
  }
  const detail = detailFromResponse(err);
  if (detail) {
    return detail;
  }
  return formatAdminApiError(err, {
    fallback:
      'Login failed — unable to reach the gameserver. Check your connection and try again.',
  });
};

interface LoginFormProps {
  onLoginSuccess?: () => void;
}

const LoginForm: React.FC<LoginFormProps> = ({ onLoginSuccess }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [debug, setDebug] = useState<string>('');
  const [showMFA, setShowMFA] = useState(false);
  const [mfaSessionToken, setMfaSessionToken] = useState<string | null>(null);
  
  const { login, verifyMFA } = useAuth();
  
  // Add debug information about the API connection
  useEffect(() => {
    // Use API_URL from environment or relative URL via proxy
    const apiUrl = import.meta.env.VITE_API_URL || '';
    setDebug(`Using API at ${apiUrl || 'via proxy'}`);
  }, []);
  
  // Function to make a direct login request to test the API
  const testDirectLogin = async () => {
    setIsSubmitting(true);
    setError(null);
    
    try {
      // Use API URL from environment or default to relative URL
      const apiUrl = import.meta.env.VITE_API_URL || '';
      setDebug(`Testing direct API at ${apiUrl || 'via proxy'}/api/v1/auth/login/direct...`);
      
      const response = await fetch(`${apiUrl}/api/v1/auth/login/direct`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify({
          username: username,
          password: password
        })
      });
      
      // Get response text first to debug any parsing issues
      const responseText = await response.text();
      setDebug(`Raw API response: ${responseText}`);
      
      try {
        // Now parse the JSON
        const data = JSON.parse(responseText);
        setDebug(`API test results:\nStatus: ${response.status}\nResponse: ${JSON.stringify(data, null, 2)}`);
        
        if (response.ok) {
          if (data.access_token && data.refresh_token) {
            // Store tokens manually
            localStorage.setItem('accessToken', data.access_token);
            localStorage.setItem('refreshToken', data.refresh_token);
            
            setDebug(`Login successful! 
AccessToken: ${data.access_token?.substring(0, 15)}...
RefreshToken: ${data.refresh_token?.substring(0, 15)}...
Redirecting to dashboard...`);
            
            // Reload the page to apply tokens
            setTimeout(() => {
              window.location.href = '/';
            }, 2000);
          } else {
            setError(`API response missing tokens. Access token: ${data.access_token ? 'Yes' : 'No'}, Refresh token: ${data.refresh_token ? 'Yes' : 'No'}`);
          }
        } else {
          setError(`API test failed with status ${response.status}: ${data.detail || 'Unknown error'}`);
        }
      } catch (jsonError) {
        setError(`Failed to parse JSON response: ${jsonError instanceof Error ? jsonError.message : 'Unknown error'}`);
      }
    } catch (err: unknown) {
      console.error('API test failed:', err);
      setError(
        formatAdminApiError(err, {
          fallback:
            'API test failed — unable to reach the gameserver. Check your connection and try again.',
        })
      );
    } finally {
      setIsSubmitting(false);
    }
  };
  
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!username || !password) {
      setError('Please enter both username and password');
      return;
    }
    
    setError(null);
    setIsSubmitting(true);
    
    try {
      const result = await login(username, password);
      
      if (result.requiresMFA && result.sessionToken) {
        // Show MFA verification
        setShowMFA(true);
        setMfaSessionToken(result.sessionToken);
      } else {
        // Login successful without MFA
        if (onLoginSuccess) {
          onLoginSuccess();
        }
      }
    } catch (err: unknown) {
      console.error('Login failed:', err);
      setError(loginApiError(err));
    } finally {
      setIsSubmitting(false);
    }
  };
  
  const handleMFAVerify = async (code: string) => {
    if (!mfaSessionToken) {
      throw new Error('No MFA session token');
    }
    
    await verifyMFA(code, mfaSessionToken);
    
    if (onLoginSuccess) {
      onLoginSuccess();
    }
  };
  
  const handleMFACancel = () => {
    setShowMFA(false);
    setMfaSessionToken(null);
    setError(null);
  };
  
  // No environment check needed - OAuth works properly
  
  if (showMFA) {
    return (
      <MFAVerification
        onVerify={handleMFAVerify}
        onCancel={handleMFACancel}
        error={error}
      />
    );
  }
  
  return (
    <div>
      <form className="space-y-4" onSubmit={handleSubmit}>
        <h2 className="text-lg font-semibold text-center mb-4">Admin Login</h2>
        
        {error && <div className="alert alert-error">{error}</div>}
        
        <div className="form-group">
          <label htmlFor="username" className="form-label">Username</label>
          <input
            type="text"
            id="username"
            className="form-input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={isSubmitting}
            autoComplete="username"
          />
        </div>
        
        <div className="form-group">
          <label htmlFor="password" className="form-label">Password</label>
          <input
            type="password"
            id="password"
            className="form-input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={isSubmitting}
            autoComplete="current-password"
          />
        </div>
        
        <div className="flex gap-2">
          <button 
            type="submit" 
            className="btn btn-primary flex-1" 
            disabled={isSubmitting}
          >
            {isSubmitting ? 'Logging in...' : 'Login'}
          </button>
          
          <button 
            type="button"
            className="btn btn-secondary"
            onClick={testDirectLogin}
            disabled={isSubmitting}
          >
            Test Direct API
          </button>
        </div>
        
        {debug && (
          <div className="alert alert-info">
            <pre className="text-xs font-mono whitespace-pre-wrap">{debug}</pre>
          </div>
        )}
      </form>
    </div>
  );
};

export default LoginForm;
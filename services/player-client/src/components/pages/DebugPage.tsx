import React, { useState, useEffect } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
import axios from 'axios';

const DebugPage: React.FC = () => {
  const { user, isAuthenticated, isLoading: authLoading } = useAuth();
  const { playerState, currentShip, isLoading: gameLoading, error: gameError } = useGame();
  const [apiTest, setApiTest] = useState<any>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  // Test API connectivity
  useEffect(() => {
    const testApi = async () => {
      const token = localStorage.getItem('accessToken');
      if (!token) {
        setApiError('No access token found');
        return;
      }

      try {
        // Get API URL
        const getApiUrl = () => {
          if (import.meta.env.VITE_API_URL) {
            return import.meta.env.VITE_API_URL;
          }
          const windowUrl = window.location.origin;
          if (windowUrl.includes('.app.github.dev')) {
            const hostname = window.location.hostname;
            const parts = hostname.split('.');
            const hostnamePart = parts[0];
            const lastDashIndex = hostnamePart.lastIndexOf('-');
            const codespaceName = lastDashIndex !== -1 ? hostnamePart.substring(0, lastDashIndex) : hostnamePart;
            return `https://${codespaceName}-8080.app.github.dev`;
          }
          if (windowUrl.includes('localhost')) {
            return 'http://localhost:8080';
          }
          return '';
        };

        const apiUrl = getApiUrl();

        // Test auth endpoint
        const authResponse = await axios.get(`${apiUrl}/api/v1/auth/me`, {
          headers: { Authorization: `Bearer ${token}` }
        });

        // Test player endpoint
        const playerResponse = await axios.get(`${apiUrl}/api/v1/player/state`, {
          headers: { Authorization: `Bearer ${token}` }
        });

        setApiTest({
          apiUrl,
          authResponse: authResponse.data,
          playerResponse: playerResponse.data,
          token: token.substring(0, 10) + '...'
        });
        setApiError(null);
      } catch (error: any) {
        console.error('API test failed:', error);
        setApiError(`API test failed: ${error.response?.data?.detail || error.message}`);
      }
    };

    if (isAuthenticated) {
      testApi();
    }
  }, [isAuthenticated]);

  const clearStorage = () => {
    localStorage.clear();
    sessionStorage.clear();
    window.location.reload();
  };

  return (
    <div style={{ padding: '20px', fontFamily: 'monospace' }}>
      <h1>Debug Information</h1>
      
      <div style={{ marginBottom: '20px', padding: '10px', border: '1px solid #ccc' }}>
        <h2>Authentication Status</h2>
        <p>Is Authenticated: {isAuthenticated ? 'Yes' : 'No'}</p>
        <p>Auth Loading: {authLoading ? 'Yes' : 'No'}</p>
        <p>User: {user ? user.username : 'None'}</p>
        <p>Access Token: {localStorage.getItem('accessToken') ? 'Present' : 'Missing'}</p>
        <p>Refresh Token: {localStorage.getItem('refreshToken') ? 'Present' : 'Missing'}</p>
        <p>User ID: {localStorage.getItem('userId') || 'Missing'}</p>
      </div>

      <div style={{ marginBottom: '20px', padding: '10px', border: '1px solid #ccc' }}>
        <h2>Game State</h2>
        <p>Game Loading: {gameLoading ? 'Yes' : 'No'}</p>
        <p>Game Error: {gameError || 'None'}</p>
        <p>Player State: {playerState ? 'Loaded' : 'Not loaded'}</p>
        {playerState && (
          <div style={{ marginLeft: '20px' }}>
            <p>Player ID: {playerState.id}</p>
            <p>Username: {playerState.username}</p>
            <p>Credits: {playerState.credits}</p>
            <p>Turns: {playerState.turns}</p>
            <p>Current Ship ID: {playerState.current_ship_id}</p>
            <p>Current Sector: {playerState.current_sector_id}</p>
          </div>
        )}
        <p>Current Ship: {currentShip ? currentShip.name : 'Not loaded'}</p>
      </div>

      <div style={{ marginBottom: '20px', padding: '10px', border: '1px solid #ccc' }}>
        <h2>API Test Results</h2>
        {apiError && <p style={{ color: 'red' }}>Error: {apiError}</p>}
        {apiTest && (
          <div>
            <p>API URL: {apiTest.apiUrl}</p>
            <p>Token: {apiTest.token}</p>
            <h3>Auth Response:</h3>
            <pre>{JSON.stringify(apiTest.authResponse, null, 2)}</pre>
            <h3>Player Response:</h3>
            <pre>{JSON.stringify(apiTest.playerResponse, null, 2)}</pre>
          </div>
        )}
      </div>

      <div style={{ marginBottom: '20px' }}>
        <button onClick={clearStorage} style={{ padding: '10px', marginRight: '10px' }}>
          Clear All Storage & Reload
        </button>
        <button onClick={() => window.location.href = '/auth/callback'} style={{ padding: '10px' }}>
          Test OAuth Callback
        </button>
      </div>
    </div>
  );
};

export default DebugPage;
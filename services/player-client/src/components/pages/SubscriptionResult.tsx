import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import apiClient from '../../services/apiClient';
import './subscription-result.css';

// Pull the backend's verbatim detail string out of an axios error (mirrors
// the established FETCH-CONVERGE idiom, e.g. GatewrightPanel.tsx's errDetail).
const errDetail = (e: unknown, fallback: string): string => {
  if (e && typeof e === 'object') {
    const resp = (e as { response?: { data?: unknown } }).response;
    const data = resp?.data;
    if (data && typeof data === 'object') {
      const detail = (data as Record<string, unknown>).detail;
      if (typeof detail === 'string' && detail) return detail;
    }
  }
  return fallback;
};

interface SubscriptionDetails {
  subscription_id: string;
  status: string;
  plan_id: string;
  start_time: string;
  next_billing_time?: string;
}

const SubscriptionResult: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const [loading, setLoading] = useState(true);
  const [subscriptionDetails, setSubscriptionDetails] = useState<SubscriptionDetails | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const urlParams = new URLSearchParams(location.search);
    const subscriptionId = urlParams.get('subscription_id');
    const token_param = urlParams.get('token');
    const ba_token = urlParams.get('ba_token');

    // PayPal can return subscription_id, token, or ba_token depending on flow
    const paypalSubscriptionId = subscriptionId || token_param || ba_token;

    if (paypalSubscriptionId && isAuthenticated) {
      fetchSubscriptionDetails(paypalSubscriptionId);
    } else if (location.pathname.includes('cancelled')) {
      setLoading(false);
    } else {
      setError('Invalid subscription response');
      setLoading(false);
    }
  }, [location, isAuthenticated]);

  const fetchSubscriptionDetails = async (subscriptionId: string) => {
    try {
      // apiClient's request interceptor attaches the current access token
      // (and its response interceptor handles 401 refresh-and-retry) —
      // no manual Authorization header needed.
      const response = await apiClient.get(`/api/v1/paypal/subscriptions/${subscriptionId}`);
      setSubscriptionDetails(response.data);
    } catch (err) {
      setError(errDetail(err, 'Failed to retrieve subscription details'));
      console.error('Error fetching subscription details:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleContinue = () => {
    navigate('/dashboard');
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const getSubscriptionType = (planId: string) => {
    if (planId?.includes('galactic')) return 'Galactic Citizenship';
    if (planId?.includes('regional')) return 'Regional Ownership';
    return 'Premium Subscription';
  };

  if (location.pathname.includes('cancelled')) {
    return (
      <div className="subscription-result-container">
        <div className="result-card cancelled">
          <div className="result-icon">❌</div>
          <h2>Subscription Cancelled</h2>
          <p>
            Your subscription process was cancelled. No charges have been made to your account.
          </p>
          <p>
            You can try again anytime or explore our platform with the free tier.
          </p>
          <button onClick={handleContinue} className="continue-button">
            Return to Dashboard
          </button>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="subscription-result-container">
        <div className="result-card loading">
          <div className="loading-spinner"></div>
          <h2>Processing Your Subscription...</h2>
          <p>Please wait while we confirm your subscription details.</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="subscription-result-container">
        <div className="result-card error">
          <div className="result-icon">⚠️</div>
          <h2>Subscription Error</h2>
          <p>{error}</p>
          <p>
            If this problem persists, please contact our support team with your transaction details.
          </p>
          <button onClick={handleContinue} className="continue-button">
            Return to Dashboard
          </button>
        </div>
      </div>
    );
  }

  if (subscriptionDetails) {
    return (
      <div className="subscription-result-container">
        <div className="result-card success">
          <div className="result-icon">✅</div>
          <h2>Subscription Activated!</h2>
          <p>
            Thank you for subscribing to <strong>{getSubscriptionType(subscriptionDetails.plan_id)}</strong>!
          </p>
          
          <div className="subscription-summary">
            <div className="summary-item">
              <span className="label">Subscription ID:</span>
              <span className="value">{subscriptionDetails.subscription_id}</span>
            </div>
            <div className="summary-item">
              <span className="label">Status:</span>
              <span className={`value status-${subscriptionDetails.status.toLowerCase()}`}>
                {subscriptionDetails.status}
              </span>
            </div>
            <div className="summary-item">
              <span className="label">Started:</span>
              <span className="value">{formatDate(subscriptionDetails.start_time)}</span>
            </div>
            {subscriptionDetails.next_billing_time && (
              <div className="summary-item">
                <span className="label">Next Billing:</span>
                <span className="value">{formatDate(subscriptionDetails.next_billing_time)}</span>
              </div>
            )}
          </div>

          <div className="next-steps">
            <h3>What's Next?</h3>
            <ul>
              {subscriptionDetails.plan_id?.includes('galactic') && (
                <>
                  <li>✓ Access to all active regions</li>
                  <li>✓ Inter-regional travel enabled</li>
                  <li>✓ Central Nexus access granted</li>
                  <li>✓ Cross-regional trading privileges</li>
                </>
              )}
              {subscriptionDetails.plan_id?.includes('regional') && (
                <>
                  <li>✓ Your region is being generated</li>
                  <li>✓ Governor dashboard access granted</li>
                  <li>✓ Governance tools available</li>
                  <li>✓ Economic controls enabled</li>
                  <li>✓ All galactic citizen benefits included</li>
                </>
              )}
            </ul>
          </div>

          <div className="action-buttons">
            <button onClick={handleContinue} className="continue-button primary">
              Explore Your New Privileges
            </button>
            <button 
              onClick={() => navigate('/subscription')} 
              className="continue-button secondary"
            >
              Manage Subscriptions
            </button>
          </div>

          <div className="support-info">
            <p>
              <strong>Need help?</strong> Contact our support team if you have any questions about your subscription.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return null;
};

export default SubscriptionResult;
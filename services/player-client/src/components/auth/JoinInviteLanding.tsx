import React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { inviteCodeFromUrl } from './inviteCodeFromUrl';
import './auth.css';

/**
 * JoinInviteLanding — region invite on-ramp (LEG-3151 / region-invite-onramp residual).
 * Owners share `/join?invite=CODE` links from RegionInvitePanel; this route consumes them
 * and routes newcomers to registration with the invite prefilled.
 */
const JoinInviteLanding: React.FC = () => {
  const navigate = useNavigate();
  const { search } = useLocation();
  const inviteCode = inviteCodeFromUrl(search);
  const hasCode = inviteCode.length > 0;

  const goRegister = () => {
    if (hasCode) {
      navigate(`/register?invite=${encodeURIComponent(inviteCode)}`);
    } else {
      navigate('/register');
    }
  };

  return (
    <div className="join-invite-landing" data-testid="join-invite-landing">
      <header className="join-invite-header">
        <h1>Region Invite</h1>
        <p className="join-invite-lead">
          {hasCode
            ? 'You were invited to join a Sector Wars region as a voting citizen.'
            : 'This link is missing an invite code — ask the region owner for a fresh join URL.'}
        </p>
      </header>

      {hasCode ? (
        <div className="join-invite-code-block" data-testid="join-invite-code">
          <span className="join-invite-code-label">Invite code</span>
          <code className="join-invite-code-value">{inviteCode}</code>
        </div>
      ) : (
        <p className="join-invite-missing" role="status" data-testid="join-invite-missing">
          No invite code was found in this URL. Expected <code>/join?invite=YOUR_CODE</code>.
        </p>
      )}

      <div className="join-invite-actions">
        <button
          type="button"
          className="join-invite-cta primary"
          data-testid="join-invite-register-cta"
          onClick={goRegister}
        >
          {hasCode ? 'Create account with this invite' : 'Create account anyway'}
        </button>
        <button
          type="button"
          className="join-invite-cta"
          data-testid="join-invite-home-cta"
          onClick={() => navigate('/')}
        >
          Back to home
        </button>
      </div>
    </div>
  );
};

export default JoinInviteLanding;

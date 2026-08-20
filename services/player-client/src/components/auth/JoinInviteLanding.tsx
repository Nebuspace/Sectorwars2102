import { Navigate, useSearchParams } from 'react-router-dom';
import { persistRegionInvite, sanitizeOauthInvite } from './regionInvite';

/** Public `/join?invite=` — persist a sanitized code then open the home register flow. */
export default function JoinInviteLanding() {
  const [params] = useSearchParams();
  const code = sanitizeOauthInvite(params.get('invite'));
  if (code) persistRegionInvite(code);
  return <Navigate to="/" replace />;
}

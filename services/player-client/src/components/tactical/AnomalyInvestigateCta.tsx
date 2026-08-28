import React, { useState } from 'react';
import { playerAPI } from '../../services/api';

type Props = {
  sectorId: number;
  sectorType?: string | null;
  anomalyInvestigated?: boolean;
};

/**
 * SectorType.ANOMALY investigate CTA (LEG-478). Distinct from formation
 * INVESTIGATE — POSTs /player/sectors/{id}/investigate-anomaly.
 */
const AnomalyInvestigateCta: React.FC<Props> = ({
  sectorId,
  sectorType,
  anomalyInvestigated = false,
}) => {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(anomalyInvestigated);
  const [message, setMessage] = useState<string | null>(null);

  if ((sectorType || '').toUpperCase() !== 'ANOMALY') {
    return null;
  }

  const onInvestigate = async () => {
    if (busy || done) return;
    setBusy(true);
    setMessage(null);
    try {
      const data = await playerAPI.investigateAnomaly(sectorId);
      setDone(true);
      const rewardBits = [
        typeof data?.reward?.credits === 'number' ? `+${data.reward.credits} cr` : null,
        typeof data?.credits_remaining === 'number'
          ? `${data.credits_remaining} cr remaining`
          : null,
      ].filter(Boolean);
      setMessage(rewardBits.length ? rewardBits.join(' · ') : 'Anomaly investigated.');
    } catch (error: unknown) {
      const err = error as { status?: number; response?: { status?: number; data?: { detail?: string } }; message?: string };
      const statusCode = err?.status ?? err?.response?.status;
      const detail = err?.response?.data?.detail ?? err?.message;
      if (statusCode === 409) {
        setDone(true);
        setMessage(
          typeof detail === 'string' && detail
            ? detail
            : 'Anomaly has already been investigated.',
        );
      } else {
        setMessage(
          (typeof detail === 'string' && detail) || 'Investigation failed. Please try again.',
        );
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="row anomaly-investigate-cta" data-testid="anomaly-investigate-cta">
      <b>✧ ANOMALY</b>
      <button
        type="button"
        className="act"
        data-testid="anomaly-investigate-btn"
        onClick={() => void onInvestigate()}
        disabled={busy || done}
        title={done ? 'Already investigated' : 'Investigate this sector anomaly'}
      >
        {busy ? '🔬 …' : done ? '✓ INVESTIGATED' : '🔬 INVESTIGATE'}
      </button>
      {message && (
        <span className="dim" role="status" data-testid="anomaly-investigate-status">
          {message}
        </span>
      )}
    </div>
  );
};

export default AnomalyInvestigateCta;

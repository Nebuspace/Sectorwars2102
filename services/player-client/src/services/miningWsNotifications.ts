/**
 * Player-client copy for gameserver mining WS frames (mining.md:258).
 * Pure helpers — no invent of game rules; maps server subtypes/payload only.
 */

export type MiningToast = {
  title: string;
  content: string;
  level: 'info' | 'success' | 'warning';
};

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

const deliveryIncludesToast = (message: Record<string, unknown>): boolean => {
  const delivery = message.delivery;
  if (!Array.isArray(delivery)) return true;
  return delivery.some((entry) => String(entry) === 'toast');
};

export function miningHarvestNotificationToast(
  message: Record<string, unknown>,
): MiningToast | null {
  if (!deliveryIncludesToast(message)) return null;
  const subtype = typeof message.subtype === 'string' ? message.subtype : '';
  const payload = asRecord(message.payload) ?? {};

  switch (subtype) {
    case 'harvest_success': {
      const ore = Number(payload.ore ?? 0);
      const precious = Number(payload.precious_metals ?? 0);
      const shards = Number(payload.quantum_shards ?? 0);
      const parts = [`Extracted ${ore} ore`];
      if (precious > 0) parts.push(`${precious} precious metals`);
      if (shards > 0) parts.push(`${shards} quantum shard${shards === 1 ? '' : 's'}`);
      return {
        title: 'Harvest Complete',
        content: `${parts.join(' · ')}.`,
        level: 'success',
      };
    }
    case 'precious_metals': {
      const amount = Number(payload.amount ?? 0);
      return {
        title: 'Rare Drop — Precious Metals',
        content: `Mining laser recovered ${amount} precious metal${amount === 1 ? '' : 's'}.`,
        level: 'success',
      };
    }
    case 'quantum_shards': {
      const amount = Number(payload.amount ?? 0);
      return {
        title: 'Trace Drop — Quantum Shards',
        content: `Deep-core scan yielded ${amount} quantum shard${amount === 1 ? '' : 's'}.`,
        level: 'success',
      };
    }
    default:
      return null;
  }
}

export function miningLicenseExpiryWarningToast(
  message: Record<string, unknown>,
): MiningToast | null {
  if (!deliveryIncludesToast(message)) return null;
  const payload = asRecord(message.payload) ?? {};
  const sector =
    typeof payload.sector_number === 'number'
      ? `sector ${payload.sector_number}`
      : 'your claim';
  const expiresAt =
    typeof payload.expires_at === 'string' && payload.expires_at.length > 0
      ? new Date(payload.expires_at).toLocaleString()
      : 'within the hour';
  return {
    title: 'Mining License Expiring',
    content: `AM claim license for ${sector} expires ${expiresAt}. Renew at Astral Mining while docked.`,
    level: 'warning',
  };
}

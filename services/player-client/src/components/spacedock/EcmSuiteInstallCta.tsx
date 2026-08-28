/**
 * LEG-126 — Install ECM Suite (equipment_slots) on Armory venue.
 * Mirrors LEG-109/115/117/120 install CTAs; ModuleGrid combat ladders stay
 * out of scope.
 *
 * Canon: ship-systems.md — ₡45,000; Scout / Defender / Carrier / Warp Jumper;
 * grants ecm_hit_penalty 0.15 (combat_service consumer — server-side).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { shipUpgradeAPI } from '../../services/api';
import './tactical-equipment-install.css';

/** Canon catalog cost — EQUIPMENT_DEFINITIONS.ecm_suite (ship-systems.md). */
export const ECM_SUITE_INSTALL_COST_CR = 45_000;

/** Hulls that may fit ecm_suite (server EQUIPMENT_DEFINITIONS). */
export const ECM_SUITE_COMPATIBLE_HULLS = [
  'SCOUT_SHIP',
  'DEFENDER',
  'CARRIER',
  'WARP_JUMPER',
] as const;

export function normalizeShipType(shipType: string | null | undefined): string | null {
  if (!shipType) return null;
  const norm = shipType.trim().toUpperCase().replace(/\s+/g, '_');
  // Client often stores short "SCOUT"; server enum is SCOUT_SHIP.
  if (norm === 'SCOUT') return 'SCOUT_SHIP';
  return norm;
}

export function isEcmSuiteHullCompatible(
  shipType: string | null | undefined,
): boolean {
  const norm = normalizeShipType(shipType);
  if (!norm) return false;
  return (ECM_SUITE_COMPATIBLE_HULLS as readonly string[]).includes(norm);
}

export interface EcmSuiteInstallCtaProps {
  shipId?: string | null;
  shipType?: string | null;
  compact?: boolean;
  onInstalled?: (result: {
    remainingCredits?: number;
    message?: string;
  }) => void | Promise<void>;
}

const EcmSuiteInstallCta: React.FC<EcmSuiteInstallCtaProps> = ({
  shipId,
  shipType,
  compact = false,
  onInstalled,
}) => {
  const compatible = isEcmSuiteHullCompatible(shipType);

  const [slotPresent, setSlotPresent] = useState<boolean | null>(null);
  const [installCost, setInstallCost] = useState(ECM_SUITE_INSTALL_COST_CR);
  const [isInstalling, setIsInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [installSuccess, setInstallSuccess] = useState<string | null>(null);

  const applyFromUpgrades = useCallback((info: any) => {
    const catalog = info?.equipment?.ecm_suite;
    const slot = info?.equipped?.ecm_suite;
    const present = !!(catalog?.installed || slot != null);
    setSlotPresent(present);
    if (typeof catalog?.cost === 'number' && catalog.cost > 0) {
      setInstallCost(catalog.cost);
    }
  }, []);

  const refreshEquipment = useCallback(async () => {
    if (!shipId || !compatible) {
      setSlotPresent(null);
      return;
    }
    try {
      const info = await shipUpgradeAPI.getUpgrades(shipId);
      applyFromUpgrades(info);
    } catch {
      /* leave prior state */
    }
  }, [shipId, compatible, applyFromUpgrades]);

  useEffect(() => {
    void refreshEquipment();
  }, [refreshEquipment]);

  const handleInstall = async () => {
    if (!shipId || isInstalling || slotPresent !== false) return;
    setIsInstalling(true);
    setInstallError(null);
    setInstallSuccess(null);
    try {
      const result = await shipUpgradeAPI.installEquipment(shipId, 'ecm_suite');
      const paid =
        typeof result?.cost_paid === 'number'
          ? result.cost_paid.toLocaleString()
          : installCost.toLocaleString();
      setInstallSuccess(
        result?.message
          ? `${result.message} — ${paid} cr.`
          : `ECM Suite installed — ${paid} cr.`,
      );
      setSlotPresent(true);
      await onInstalled?.({
        remainingCredits:
          typeof result?.remaining_credits === 'number'
            ? result.remaining_credits
            : undefined,
        message: result?.message,
      });
      void refreshEquipment();
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message;
      setInstallError(
        typeof detail === 'string' && detail
          ? detail
          : 'ECM Suite install failed',
      );
    } finally {
      setIsInstalling(false);
    }
  };

  if (!compatible || !shipId) return null;
  if (slotPresent === null) {
    return (
      <div
        className={`tactical-equip-cta${compact ? ' compact' : ''}`}
        data-testid="ecm-suite-cta-checking"
      >
        <span className="tactical-equip-cta-status">Checking ECM Suite…</span>
      </div>
    );
  }

  if (slotPresent) {
    return (
      <div
        className={`tactical-equip-cta fitted${compact ? ' compact' : ''}`}
        data-testid="ecm-suite-cta-fitted"
      >
        <span className="tactical-equip-cta-status">
          ECM Suite fitted — incoming hit chance reduced (combat consumer
          server-side)
        </span>
        {installSuccess && (
          <span className="tactical-equip-cta-ok" role="status">
            {installSuccess}
          </span>
        )}
      </div>
    );
  }

  return (
    <div
      className={`tactical-equip-cta needs-install${compact ? ' compact' : ''}`}
      data-testid="ecm-suite-cta"
    >
      <p className="tactical-equip-cta-copy">
        Fit an ECM Suite for electronic countermeasures (equipment slot — not a
        ModuleGrid combat ladder).
      </p>
      <button
        type="button"
        className="tactical-equip-cta-btn"
        onClick={(e) => {
          e.stopPropagation();
          void handleInstall();
        }}
        disabled={isInstalling}
        data-testid="ecm-suite-install-btn"
        title="Install ECM Suite equipment (catalog cost)"
      >
        {isInstalling
          ? 'INSTALLING…'
          : `INSTALL ECM SUITE (${installCost.toLocaleString()} CR)`}
      </button>
      {installError && (
        <span className="tactical-equip-cta-err" role="alert">
          {installError}
        </span>
      )}
    </div>
  );
};

export default EcmSuiteInstallCta;

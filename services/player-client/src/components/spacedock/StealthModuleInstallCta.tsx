/**
 * LEG-126 — Install Stealth Module (equipment_slots) on Armory venue.
 * Mirrors LEG-109/115/117/120 install CTAs; ModuleGrid combat ladders stay
 * out of scope.
 *
 * Canon: ship-systems.md — ₡40,000; Scout / Fast Courier / Warp Jumper;
 * grants stealth_evasion_bonus 15 (+ contraband detection mult server-side).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { shipUpgradeAPI } from '../../services/api';
import './tactical-equipment-install.css';

/** Canon catalog cost — EQUIPMENT_DEFINITIONS.stealth_module (ship-systems.md). */
export const STEALTH_MODULE_INSTALL_COST_CR = 40_000;

/** Hulls that may fit stealth_module (server EQUIPMENT_DEFINITIONS). */
export const STEALTH_MODULE_COMPATIBLE_HULLS = [
  'SCOUT_SHIP',
  'FAST_COURIER',
  'WARP_JUMPER',
] as const;

export function normalizeShipType(shipType: string | null | undefined): string | null {
  if (!shipType) return null;
  const norm = shipType.trim().toUpperCase().replace(/\s+/g, '_');
  if (norm === 'SCOUT') return 'SCOUT_SHIP';
  return norm;
}

export function isStealthModuleHullCompatible(
  shipType: string | null | undefined,
): boolean {
  const norm = normalizeShipType(shipType);
  if (!norm) return false;
  return (STEALTH_MODULE_COMPATIBLE_HULLS as readonly string[]).includes(norm);
}

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

export function formatStealthModuleInstallError(err: unknown): string {
  const fallback = 'Stealth Module install failed';
  if (err instanceof TypeError) return fallback;
  const responseDetail =
    (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  if (typeof responseDetail === 'string' && responseDetail) return responseDetail;
  const message = (err as { message?: string })?.message;
  if (typeof message === 'string' && message) {
    if (isNetworkCollapseMessage(message)) return fallback;
    return message;
  }
  return fallback;
}

export interface StealthModuleInstallCtaProps {
  shipId?: string | null;
  shipType?: string | null;
  compact?: boolean;
  onInstalled?: (result: {
    remainingCredits?: number;
    message?: string;
  }) => void | Promise<void>;
}

const StealthModuleInstallCta: React.FC<StealthModuleInstallCtaProps> = ({
  shipId,
  shipType,
  compact = false,
  onInstalled,
}) => {
  const compatible = isStealthModuleHullCompatible(shipType);

  const [slotPresent, setSlotPresent] = useState<boolean | null>(null);
  const [installCost, setInstallCost] = useState(STEALTH_MODULE_INSTALL_COST_CR);
  const [isInstalling, setIsInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [installSuccess, setInstallSuccess] = useState<string | null>(null);

  const applyFromUpgrades = useCallback((info: any) => {
    const catalog = info?.equipment?.stealth_module;
    const slot = info?.equipped?.stealth_module;
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
      const result = await shipUpgradeAPI.installEquipment(shipId, 'stealth_module');
      const paid =
        typeof result?.cost_paid === 'number'
          ? result.cost_paid.toLocaleString()
          : installCost.toLocaleString();
      setInstallSuccess(
        result?.message
          ? `${result.message} — ${paid} cr.`
          : `Stealth Module installed — ${paid} cr.`,
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
    } catch (err: unknown) {
      setInstallError(formatStealthModuleInstallError(err));
    } finally {
      setIsInstalling(false);
    }
  };

  if (!compatible || !shipId) return null;
  if (slotPresent === null) {
    return (
      <div
        className={`tactical-equip-cta${compact ? ' compact' : ''}`}
        data-testid="stealth-module-cta-checking"
      >
        <span className="tactical-equip-cta-status">Checking Stealth Module…</span>
      </div>
    );
  }

  if (slotPresent) {
    return (
      <div
        className={`tactical-equip-cta fitted${compact ? ' compact' : ''}`}
        data-testid="stealth-module-cta-fitted"
      >
        <span className="tactical-equip-cta-status">
          Stealth Module fitted — evasion bonus active (combat / contraband
          consumers server-side)
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
      data-testid="stealth-module-cta"
    >
      <p className="tactical-equip-cta-copy">
        Fit a Stealth Module for signature dampers (equipment slot — not a
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
        data-testid="stealth-module-install-btn"
        title="Install Stealth Module equipment (catalog cost)"
      >
        {isInstalling
          ? 'INSTALLING…'
          : `INSTALL STEALTH MODULE (${installCost.toLocaleString()} CR)`}
      </button>
      {installError && (
        <span className="tactical-equip-cta-err" role="alert">
          {installError}
        </span>
      )}
    </div>
  );
};

export default StealthModuleInstallCta;

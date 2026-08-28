/**
 * LEG-117 — Install Planetary Lander (equipment_slots) on claim / disembark
 * surfaces. Mirrors LEG-109/115 install CTAs; ModuleGrid `lander` family stays
 * out of scope.
 *
 * Canon: ship-systems.md — ₡20,000; Colony Ship / Light Freighter / Cargo Hauler;
 * grants landing_bonus 1.25 on claim_planet + disembark.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { shipUpgradeAPI } from '../../services/api';
import './planetary-lander-install.css';

/** Canon catalog cost — EQUIPMENT_DEFINITIONS.planetary_lander (ship-systems.md). */
export const PLANETARY_LANDER_INSTALL_COST_CR = 20_000;

/** Hulls that may fit planetary_lander (server EQUIPMENT_DEFINITIONS). */
export const PLANETARY_LANDER_COMPATIBLE_HULLS = [
  'COLONY_SHIP',
  'LIGHT_FREIGHTER',
  'CARGO_HAULER',
] as const;

export function normalizeShipType(shipType: string | null | undefined): string | null {
  if (!shipType) return null;
  return shipType.trim().toUpperCase().replace(/\s+/g, '_');
}

export function isPlanetaryLanderHullCompatible(
  shipType: string | null | undefined,
): boolean {
  const norm = normalizeShipType(shipType);
  if (!norm) return false;
  return (PLANETARY_LANDER_COMPATIBLE_HULLS as readonly string[]).includes(norm);
}

export interface PlanetaryLanderInstallCtaProps {
  shipId?: string | null;
  shipType?: string | null;
  /** Optional compact layout for confirm dialogs / modals. */
  compact?: boolean;
  /** After a successful install — refresh player / credits (parent owns GameContext). */
  onInstalled?: (result: {
    remainingCredits?: number;
    message?: string;
  }) => void | Promise<void>;
}

const PlanetaryLanderInstallCta: React.FC<PlanetaryLanderInstallCtaProps> = ({
  shipId,
  shipType,
  compact = false,
  onInstalled,
}) => {
  const compatible = isPlanetaryLanderHullCompatible(shipType);

  const [slotPresent, setSlotPresent] = useState<boolean | null>(null);
  const [installCost, setInstallCost] = useState(PLANETARY_LANDER_INSTALL_COST_CR);
  const [isInstalling, setIsInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [installSuccess, setInstallSuccess] = useState<string | null>(null);

  const applyFromUpgrades = useCallback((info: any) => {
    const catalog = info?.equipment?.planetary_lander;
    const slot = info?.equipped?.planetary_lander;
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
      // Leave prior state; claim/disembark still work without lander.
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
      const result = await shipUpgradeAPI.installEquipment(shipId, 'planetary_lander');
      const paid =
        typeof result?.cost_paid === 'number'
          ? result.cost_paid.toLocaleString()
          : installCost.toLocaleString();
      setInstallSuccess(
        result?.message
          ? `${result.message} — ${paid} cr.`
          : `Planetary Lander installed — ${paid} cr.`,
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
          : 'Planetary Lander install failed',
      );
    } finally {
      setIsInstalling(false);
    }
  };

  if (!compatible || !shipId) return null;
  if (slotPresent === null) {
    return (
      <div
        className={`planetary-lander-cta${compact ? ' compact' : ''}`}
        data-testid="planetary-lander-cta-checking"
      >
        <span className="planetary-lander-cta-status">Checking Planetary Lander…</span>
      </div>
    );
  }

  if (slotPresent) {
    return (
      <div
        className={`planetary-lander-cta fitted${compact ? ' compact' : ''}`}
        data-testid="planetary-lander-cta-fitted"
      >
        <span className="planetary-lander-cta-status">
          Planetary Lander fitted — landing bonus ×1.25 active on claim &amp; unload
        </span>
        {installSuccess && (
          <span className="planetary-lander-cta-ok" role="status">{installSuccess}</span>
        )}
      </div>
    );
  }

  return (
    <div
      className={`planetary-lander-cta needs-install${compact ? ' compact' : ''}`}
      data-testid="planetary-lander-cta"
    >
      <p className="planetary-lander-cta-copy">
        Fit a Planetary Lander for ×1.25 colonist throughput on claim and unload
        (equipment slot — not the ModuleGrid lander family).
      </p>
      <button
        type="button"
        className="planetary-lander-cta-btn"
        onClick={(e) => {
          e.stopPropagation();
          void handleInstall();
        }}
        disabled={isInstalling}
        data-testid="planetary-lander-install-btn"
        title="Install Planetary Lander equipment (catalog cost)"
      >
        {isInstalling
          ? 'INSTALLING…'
          : `INSTALL PLANETARY LANDER (${installCost.toLocaleString()} CR)`}
      </button>
      {installError && (
        <span className="planetary-lander-cta-err" role="alert">{installError}</span>
      )}
    </div>
  );
};

export default PlanetaryLanderInstallCta;

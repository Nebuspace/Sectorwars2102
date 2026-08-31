/**
 * LEG-120 — Install Tractor Beam (equipment_slots) on TowConsentPanel.
 * Mirrors LEG-109/115/117 install CTAs; ModuleGrid `tractor` family and
 * combat-face tractor UI stay out of scope (Design-only).
 *
 * Canon: ship-systems.md — ₡40,000; Cargo Hauler / Defender / Carrier /
 * Warp Jumper; grants tow_capable (+ weapon_mode:tractor combat face Design-only).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { shipUpgradeAPI } from '../../services/api';
import './tractor-beam-install.css';

/** Canon catalog cost — EQUIPMENT_DEFINITIONS.tractor_beam (ship-systems.md). */
export const TRACTOR_BEAM_INSTALL_COST_CR = 40_000;

/** Hulls that may fit tractor_beam (server EQUIPMENT_DEFINITIONS). */
export const TRACTOR_BEAM_COMPATIBLE_HULLS = [
  'CARGO_HAULER',
  'DEFENDER',
  'CARRIER',
  'WARP_JUMPER',
] as const;

export function normalizeShipType(shipType: string | null | undefined): string | null {
  if (!shipType) return null;
  return shipType.trim().toUpperCase().replace(/\s+/g, '_');
}

export function isTractorBeamHullCompatible(
  shipType: string | null | undefined,
): boolean {
  const norm = normalizeShipType(shipType);
  if (!norm) return false;
  return (TRACTOR_BEAM_COMPATIBLE_HULLS as readonly string[]).includes(norm);
}

/** Normalize GS/API detail from axios-shaped response or Error.message. */
function tractorBeamInstallServerDetail(err: unknown): string | undefined {
  // Network collapse (fetch TypeError) is not gameserver copy.
  if (err instanceof TypeError) return undefined;

  if (err && typeof err === 'object') {
    const rawDetail = (err as { response?: { data?: { detail?: unknown } } }).response?.data
      ?.detail;
    if (typeof rawDetail === 'string' && rawDetail.trim()) return rawDetail.trim();
  }
  const message = err instanceof Error ? err.message : undefined;
  if (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  ) {
    return message.trim();
  }
  return undefined;
}

/** Surface gameserver tractor-beam install refusal detail. */
export function formatTractorBeamInstallError(err: unknown): string {
  return tractorBeamInstallServerDetail(err) ?? 'Tractor Beam install failed';
}

export interface TractorBeamInstallCtaProps {
  shipId?: string | null;
  shipType?: string | null;
  /** Optional compact layout for the collapsed tow rail. */
  compact?: boolean;
  /** After a successful install — refresh player / credits (parent owns GameContext). */
  onInstalled?: (result: {
    remainingCredits?: number;
    message?: string;
  }) => void | Promise<void>;
}

const TractorBeamInstallCta: React.FC<TractorBeamInstallCtaProps> = ({
  shipId,
  shipType,
  compact = false,
  onInstalled,
}) => {
  const compatible = isTractorBeamHullCompatible(shipType);

  const [slotPresent, setSlotPresent] = useState<boolean | null>(null);
  const [installCost, setInstallCost] = useState(TRACTOR_BEAM_INSTALL_COST_CR);
  const [isInstalling, setIsInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [installSuccess, setInstallSuccess] = useState<string | null>(null);

  const applyFromUpgrades = useCallback((info: any) => {
    const catalog = info?.equipment?.tractor_beam;
    const slot = info?.equipped?.tractor_beam;
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
      // Leave prior state; tow consent still works if hauler already fitted.
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
      const result = await shipUpgradeAPI.installEquipment(shipId, 'tractor_beam');
      const paid =
        typeof result?.cost_paid === 'number'
          ? result.cost_paid.toLocaleString()
          : installCost.toLocaleString();
      setInstallSuccess(
        result?.message
          ? `${result.message} — ${paid} cr.`
          : `Tractor Beam installed — ${paid} cr.`,
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
      setInstallError(formatTractorBeamInstallError(err));
    } finally {
      setIsInstalling(false);
    }
  };

  if (!compatible || !shipId) return null;
  if (slotPresent === null) {
    return (
      <div
        className={`tractor-beam-cta${compact ? ' compact' : ''}`}
        data-testid="tractor-beam-cta-checking"
      >
        <span className="tractor-beam-cta-status">Checking Tractor Beam…</span>
      </div>
    );
  }

  if (slotPresent) {
    return (
      <div
        className={`tractor-beam-cta fitted${compact ? ' compact' : ''}`}
        data-testid="tractor-beam-cta-fitted"
      >
        <span className="tractor-beam-cta-status">
          Tractor Beam fitted — tow-capable (consent tow live; combat face Design-only)
        </span>
        {installSuccess && (
          <span className="tractor-beam-cta-ok" role="status">
            {installSuccess}
          </span>
        )}
      </div>
    );
  }

  return (
    <div
      className={`tractor-beam-cta needs-install${compact ? ' compact' : ''}`}
      data-testid="tractor-beam-cta"
    >
      <p className="tractor-beam-cta-copy">
        Fit a Tractor Beam to request tow locks (equipment slot — not the ModuleGrid
        tractor family; combat tractor face stays Design-only).
      </p>
      <button
        type="button"
        className="tractor-beam-cta-btn"
        onClick={(e) => {
          e.stopPropagation();
          void handleInstall();
        }}
        disabled={isInstalling}
        data-testid="tractor-beam-install-btn"
        title="Install Tractor Beam equipment (catalog cost)"
      >
        {isInstalling
          ? 'INSTALLING…'
          : `INSTALL TRACTOR BEAM (${installCost.toLocaleString()} CR)`}
      </button>
      {installError && (
        <span className="tractor-beam-cta-err" role="alert">
          {installError}
        </span>
      )}
    </div>
  );
};

export default TractorBeamInstallCta;

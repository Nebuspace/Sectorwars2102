import React from 'react';
import { formatCredits } from '../../utils/formatters';
import './spacedock.css';

// =====================================================================
// Armory — extracted verbatim from SpaceDockInterface's inline
// `renderArmory()` + `renderArmoryItemCard()` closures (WO-UI3-VENUES
// sub-part #1, pure refactor — zero behavior change). All state/handlers
// remain owned by SpaceDockInterface and are threaded through as props.
// =====================================================================

// Armory catalog item (GET /api/v1/armory/catalog) — mirrors
// SpaceDockInterface.tsx's identically-named interface.
export interface ArmoryCatalogItem {
  item: string;
  name: string;
  price: number;
  description?: string;
  available?: boolean;
  reason?: string | null;
  service?: string;
}

// Loadout snapshot returned by POST /api/v1/armory/purchase
export interface ArmoryLoadout {
  attack_drones: number;
  defense_drones: number;
  mines: number;
  caps: {
    attack_drones: number;
    defense_drones: number;
    mines: number;
  };
}

// Display metadata for known armory items (falls back gracefully for new items)
const ARMORY_ICONS: Record<string, string> = {
  attack_drone: '⚔️',
  defense_drone: '🛡️',
  limpet_mine: '💥',
  armored_mine: '☢️'
};

const ARMORY_CARD_CLASS: Record<string, string> = {
  attack_drone: 'attack',
  defense_drone: 'defense',
  limpet_mine: 'mine',
  armored_mine: 'mine-heavy'
};

// Which loadout counter an armory item feeds into
const loadoutKeyForItem = (itemId: string): 'attack_drones' | 'defense_drones' | 'mines' | null => {
  if (itemId.includes('attack')) return 'attack_drones';
  if (itemId.includes('defense')) return 'defense_drones';
  if (itemId.includes('mine')) return 'mines';
  return null;
};

interface ArmoryVenueProps {
  armoryCatalog: ArmoryCatalogItem[] | null;
  armoryLoading: boolean;
  armoryCatalogError: string | null;
  fetchArmoryCatalog: () => void;
  armoryLoadout: ArmoryLoadout | null;
  armoryQuantities: Record<string, number>;
  setArmoryQuantities: React.Dispatch<React.SetStateAction<Record<string, number>>>;
  armoryBuying: string | null;
  armoryError: string | null;
  armorySuccess: string | null;
  purchaseArmoryItem: (item: ArmoryCatalogItem, quantity: number) => void;
  displayCredits: number;
  stationServices: Record<string, any>;
  stationIsSpacedock: boolean | undefined;
  playerAttackDrones: number | undefined;
  playerDefenseDrones: number | undefined;
  onBack: () => void;
  blackMarketButton: React.ReactNode;
}

const ArmoryVenue: React.FC<ArmoryVenueProps> = ({
  armoryCatalog,
  armoryLoading,
  armoryCatalogError,
  fetchArmoryCatalog,
  armoryLoadout,
  armoryQuantities,
  setArmoryQuantities,
  armoryBuying,
  armoryError,
  armorySuccess,
  purchaseArmoryItem,
  displayCredits,
  stationServices,
  stationIsSpacedock,
  playerAttackDrones,
  playerDefenseDrones,
  onBack,
  blackMarketButton,
}) => {
  const renderArmoryItemCard = (item: ArmoryCatalogItem) => {
    const loadoutKey = loadoutKeyForItem(item.item);
    // Purchasable ceiling for the qty slider: bounded by whatever's left in
    // the loadout cap (when this item feeds one) and by what the player can
    // actually afford — never a flat 100 that dead-ends short of usable.
    const capFree = armoryLoadout && loadoutKey
      ? Math.max(0, armoryLoadout.caps[loadoutKey] - armoryLoadout[loadoutKey])
      : null;
    const affordable = item.price > 0 ? Math.floor(displayCredits / item.price) : 100;
    const effectiveMax = Math.max(1, Math.min(100, capFree ?? 100, affordable));
    const qty = Math.min(armoryQuantities[item.item] ?? 1, effectiveMax);
    const totalCost = item.price * qty;
    // Gate on the station's services map via the item's service key —
    // the catalog doesn't send an 'available' flag
    const gated = item.available === false ||
      (item.service ? !stationServices[item.service] && !stationIsSpacedock : false);

    // Determine why purchase is blocked, if anything
    let blockReason: string | null = null;
    if (gated) {
      blockReason = item.reason || 'Service not available at this station';
    } else if (armoryLoadout && loadoutKey) {
      const cap = armoryLoadout.caps[loadoutKey];
      const current = armoryLoadout[loadoutKey];
      if (current >= cap) {
        blockReason = 'At capacity';
      } else if (current + qty > cap) {
        blockReason = `Exceeds capacity — ${cap - current} slot${cap - current === 1 ? '' : 's'} free`;
      }
    }
    if (!blockReason && displayCredits < totalCost) {
      blockReason = 'Insufficient credits';
    }

    const cardClass = ARMORY_CARD_CLASS[item.item];
    const isBuying = armoryBuying === item.item;

    return (
      <div
        key={item.item}
        className={`equipment-card${cardClass ? ` ${cardClass}` : ''}${gated ? ' unavailable' : ''}`}
      >
        <div className="eq-icon">{ARMORY_ICONS[item.item] || '📦'}</div>
        <div className="eq-info">
          <h4>{item.name}</h4>
          {item.description && <p>{item.description}</p>}
          {gated && (
            <div className="eq-unavailable-reason">
              {item.reason || 'Service not available at this station'}
            </div>
          )}
        </div>
        <div className="eq-purchase">
          <span className="eq-price">{formatCredits(item.price)}</span>
          <div className="qty-controls">
            <input
              type="range"
              id={`armory-qty-${item.item}`}
              min={1}
              max={effectiveMax}
              step={1}
              value={qty}
              onChange={e => {
                const next = Math.max(1, Math.min(effectiveMax, parseInt(e.target.value, 10) || 1));
                setArmoryQuantities(prev => ({ ...prev, [item.item]: next }));
              }}
              disabled={gated || Boolean(armoryBuying) || effectiveMax <= 1}
              aria-label={`${item.name} quantity`}
              aria-valuetext={`${qty} of ${effectiveMax}`}
              aria-describedby={effectiveMax <= 1 && !gated && blockReason ? `armory-reason-${item.item}` : undefined}
            />
            <output htmlFor={`armory-qty-${item.item}`} className="qty-readout">{qty}</output>
            <button
              className="buy-btn"
              onClick={() => purchaseArmoryItem(item, qty)}
              disabled={Boolean(armoryBuying) || Boolean(blockReason)}
              title={blockReason ?? undefined}
            >
              {isBuying ? '...' : 'Buy'}
            </button>
          </div>
          {effectiveMax <= 1 && !gated && blockReason && (
            <div id={`armory-reason-${item.item}`} className="qty-disabled-reason">
              {blockReason}
            </div>
          )}
          {qty > 1 && !gated && (
            <span className="eq-total">Total: {formatCredits(totalCost)}</span>
          )}
        </div>
      </div>
    );
  };

  const items = armoryCatalog ?? [];
  const droneItems = items.filter(i => i.item.includes('drone'));
  const mineItems = items.filter(i => !i.item.includes('drone') && i.item.includes('mine'));
  const otherItems = items.filter(i => !i.item.includes('drone') && !i.item.includes('mine'));

  return (
    <div className="venue-container armory">
      <div className="venue-header">
        <button className="back-button" onClick={onBack}>
          ← Back to Hub
        </button>
        <h2>⚔️ Armory</h2>
      </div>
      <div className="venue-content-area">
        {armorySuccess && (
          <div className="genesis-success-message">
            <span className="success-icon">✅</span>
            {armorySuccess}
          </div>
        )}
        {armoryError && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {armoryError}
          </div>
        )}

        {armoryLoading && !armoryCatalog && (
          <div className="catalog-loading">Unlocking the weapons lockers...</div>
        )}
        {armoryCatalogError && !armoryLoading && (
          <div className="genesis-error-message">
            <span className="error-icon">❌</span>
            {armoryCatalogError}
            <button className="action-button" onClick={fetchArmoryCatalog}>Retry</button>
          </div>
        )}

        <div className="current-loadout">
          <h4>📊 Current Ship Loadout</h4>
          <div className="loadout-stats">
            <div className="loadout-item">
              <span className="item-label">Attack Drones</span>
              <span className="item-value">
                {armoryLoadout
                  ? `${armoryLoadout.attack_drones} / ${armoryLoadout.caps.attack_drones}`
                  : (playerAttackDrones ?? 0)}
              </span>
            </div>
            <div className="loadout-item">
              <span className="item-label">Defense Drones</span>
              <span className="item-value">
                {armoryLoadout
                  ? `${armoryLoadout.defense_drones} / ${armoryLoadout.caps.defense_drones}`
                  : (playerDefenseDrones ?? 0)}
              </span>
            </div>
            <div className="loadout-item">
              <span className="item-label">Mines</span>
              <span className="item-value">
                {armoryLoadout
                  ? `${armoryLoadout.mines} / ${armoryLoadout.caps.mines}`
                  : '—'}
              </span>
            </div>
          </div>
        </div>

        {!armoryCatalogError && armoryCatalog && (
          <div className="armory-categories">
            {droneItems.length > 0 && (
              <div className="armory-section">
                <h3>🤖 Combat Drones</h3>
                <div className="equipment-grid">
                  {droneItems.map(renderArmoryItemCard)}
                </div>
              </div>
            )}

            {mineItems.length > 0 && (
              <div className="armory-section">
                <h3>💣 Tactical Mines</h3>
                <div className="equipment-grid">
                  {mineItems.map(renderArmoryItemCard)}
                </div>
              </div>
            )}

            {otherItems.length > 0 && (
              <div className="armory-section">
                <h3>🎯 Tactical Systems</h3>
                <div className="equipment-grid">
                  {otherItems.map(renderArmoryItemCard)}
                </div>
              </div>
            )}

            {items.length === 0 && (
              <p className="section-description">The armory shelves are empty at this station.</p>
            )}
          </div>
        )}
      </div>
      {blackMarketButton}
    </div>
  );
};

export default ArmoryVenue;

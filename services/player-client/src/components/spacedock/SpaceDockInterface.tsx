import React, { useState, useCallback } from 'react';
import { useGame } from '../../contexts/GameContext';
import type { Station } from '../../contexts/GameContext';
import ConstructionVenue from './ConstructionVenue';
import PortOfficeVenue from './PortOfficeVenue';
import ContractBoardVenue from './ContractBoardVenue';
import TradingVenue from './TradingVenue';
import ShipyardVenue from './ShipyardVenue';
import GenesisVenue from './GenesisVenue';
import ArmoryVenue from './ArmoryVenue';
import ServicesVenue from './ServicesVenue';
import MiningVenue from './MiningVenue';
import GamblingVenue from './GamblingVenue';
import { getStationClassInfo } from '../common/stationIdentity';
import { shipAPI } from '../../services/api';
import { formatCredits } from '../../utils/formatters';
import './spacedock.css';

// Use same API URL logic as GameContext for Codespaces compatibility
const getApiBaseUrl = () => {
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL;
  }
  // Use current origin to leverage Vite proxy (works in Codespaces)
  return window.location.origin;
};

// Venue type definitions
type VenueType = 'hub' | 'trading' | 'shipyard' | 'construction' | 'portoffice' | 'contracts' | 'genesis' | 'armory' | 'services' | 'gambling' | 'mining';
type GamblingGame = 'menu' | 'slots' | 'dice' | 'blackjack' | 'lottery';

// Blackjack card types
interface BlackjackCard {
  rank: string;
  suit: string;
  hidden?: boolean;
}

interface BlackjackGameState {
  playerCards: BlackjackCard[];
  dealerCards: BlackjackCard[];
  playerTotal: number;
  dealerTotal: number;
  gameOver: boolean;
  result: string | null;
  canDouble: boolean;
  deckSeed: number;
}

interface Venue {
  id: VenueType;
  name: string;
  icon: string;
  description: string;
  available: boolean;
  services?: string[];
}

// Extra fields the sector stations endpoint returns beyond the base Station type
interface DockedStation extends Station {
  station_class?: number | null;
  is_spacedock?: boolean;
  tradedock_tier?: string | null;
}

// Shipyard catalog entry (GET /api/v1/ships/catalog)
interface ShipCatalogEntry {
  type: string;
  name: string;
  base_cost: number;
  purchasable: boolean;
  speed: number;
  turn_cost: number;
  max_cargo: number;
  max_colonists: number;
  max_drones: number;
  max_shields: number;
  hull_points: number;
  attack_rating: number;
  defense_rating: number;
  max_genesis_devices: number;
  description: string;
  reason?: string | null;
}

// Armory catalog item (GET /api/v1/armory/catalog)
interface ArmoryCatalogItem {
  item: string;
  name: string;
  price: number;
  description?: string;
  available?: boolean;
  reason?: string | null;
  service?: string;
}

// Loadout snapshot returned by POST /api/v1/armory/purchase
interface ArmoryLoadout {
  attack_drones: number;
  defense_drones: number;
  mines: number;
  caps: {
    attack_drones: number;
    defense_drones: number;
    mines: number;
  };
}

// Per-STATION-CLASS static greeter, keyed by the same numeric class as
// stationIdentity.tsx's STATION_CLASSES (WO-UI3-CONCIERGE). Tone is drawn
// from sw2102-docs/FEATURES/economy/haggling.md's "Port personality types"
// table (Federation/Border/Frontier/Luxury/Black Market archetypes, matched
// to a class by that table's "Found at" column where it names an exact
// class, otherwise the closest-fit archetype for that class range) plus the
// class's own trade-pattern blurb. STATIC text/art only — no LLM, no
// backend call; narrative haggling (the LLM-driven trader dialogue) is
// 📐 design-only in canon and explicitly out of scope here.
const STATION_GREETERS: Record<number, string> = {
  // Federation core (Class 0-4 per haggling.md) — formal, procedural.
  0: '"Welcome to Sol Hub, pilot — humanity\'s gateway. Papers in order? Right this way; the manifest desk is ready when you are."',
  // Border archetype — economic, mutual-benefit framing.
  1: '"Ore\'s the currency out here. Fill your hold and we\'ll make the trip worth it — organics and equipment waiting on the racks."',
  2: '"Fresh off the hydro-domes and ready to trade. Bring ore, take organics — fair weight, fair price, same as always."',
  // Federation-adjacent, procedural.
  3: '"Fabrication bay\'s running hot. Equipment orders go on the manifest — queue at the desk and we\'ll have your parts crated by shift\'s end."',
  4: '"We cross-dock exotic tech through here daily — ore, organics, equipment, fuel, all logged and cleared. State your manifest and we\'ll route you through."',
  // Border archetype continues through the mid classes.
  5: '"We take in raw and refined alike — ore, organics, equipment, fuel — and pay out in the finer stock. Everyone walks away ahead."',
  6: '"A little of everything moves through here. Ore and organics in, equipment and fuel out — no fuss, no surprises."',
  // Frontier archetype (outer rim, low faction control) — personal, blunt.
  7: '"Out this far you learn to trust the scales, not the flag on your hull. Equipment and fuel for ore and organics — square deal, no questions."',
  // Black Market archetype — exact match, Class 8 in canon. Risk/discretion.
  8: '"Everything moves through the Hole at a premium, and nobody asks where it came from. We both walk away with nothing in writing."',
  // Not in the canon table directly; volatile-market urgency befitting a
  // stellar-proximity exchange that sells everything at a premium.
  9: '"Prices run hot this close to the flare, pilot. Everything sells at a premium here — buy now or buy later at a worse rate."',
  // Luxury archetype — exact match, Class 10 in canon. Exclusive, prestige.
  10: '"Welcome to the Market, pilot — mind the display cases. Gourmet stock in, luxury goods and exotic tech out. Taste is the only currency that matters here."',
  // Federation-adjacent, precision/tech formality.
  11: '"Exotic tech in, precision components out — every unit certified before it leaves this bay. State your order and we\'ll begin calibration."',
};

// Fallback for stations with no recognized class (legacy/unclassified data) —
// same tone as the pre-WO generic line.
const DEFAULT_GREETER = 'Welcome aboard. Choose a destination to access this station’s services.';

function getStationGreeting(stationClass?: number | null): string {
  if (stationClass == null) return DEFAULT_GREETER;
  return STATION_GREETERS[stationClass] ?? DEFAULT_GREETER;
}

// Service flags worth surfacing in the hub header, with display icons
const SERVICE_ICONS: Array<{ key: string; icon: string; label: string }> = [
  { key: 'ship_dealer', icon: '🛠️', label: 'Shipyard' },
  { key: 'ship_repair', icon: '🔧', label: 'Ship Repair' },
  { key: 'ship_maintenance', icon: '⚙️', label: 'Maintenance' },
  { key: 'ship_upgrades', icon: '📈', label: 'Upgrades' },
  { key: 'insurance', icon: '📜', label: 'Insurance' },
  { key: 'drone_shop', icon: '🤖', label: 'Drone Shop' },
  { key: 'genesis_dealer', icon: '🌍', label: 'Genesis Dealer' },
  { key: 'mine_dealer', icon: '💣', label: 'Mine Dealer' },
  { key: 'storage_rental', icon: '📦', label: 'Storage Rental' },
  { key: 'market_intelligence', icon: '📊', label: 'Market Intelligence' },
  { key: 'refining_facility', icon: '🏭', label: 'Refining Facility' },
  { key: 'luxury_amenities', icon: '✨', label: 'Luxury Amenities' },
  { key: 'diplomatic_services', icon: '🕊️', label: 'Diplomatic Services' }
];

// Black-market contraband catalog row (GET /api/v1/trading/black-market/{id}).
// Mirrors ContrabandService.get_catalog's per-commodity listing shape.
interface ContrabandListing {
  commodity: string;
  base_price: number;
  category_multiplier: number;
  severity: string;
  indicative_unit_price: number;
  federation_rep_delta: number;
}

interface BlackMarketCatalog {
  station_id: string;
  station_name: string;
  haggle_swing: number;
  commodities: ContrabandListing[];
}

// Human-readable label for a contraband commodity enum value (e.g.
// "stolen_goods" → "Stolen Goods"). Falls back gracefully for new commodities.
const prettyCommodity = (value: string): string =>
  value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

// Slot machine symbols
const SLOT_SYMBOLS = ['🌍', '⭐', '🚀', '💳', '🕳️', '💎'];
const SLOT_PAYOUTS: Record<string, number> = {
  '💎💎💎': 50,  // Jackpot
  '🚀🚀🚀': 10,  // Ships
  '⭐⭐⭐': 8,   // Stars
  '🌍🌍🌍': 5,   // Planets
  '💳💳💳': 3,   // Credits
};

interface SpaceDockProps {
  onUndock?: () => void;
  helmBusy?: boolean;
}

const SpaceDockInterface: React.FC<SpaceDockProps> = ({ onUndock, helmBusy = false }) => {
  const { playerState, stationsInSector, updatePlayerCredits, updateShipGenesis, refreshPlayerState, loadShips, getStationSlips } = useGame();
  const [activeVenue, setActiveVenue] = useState<VenueType>('hub');

  // Transient slips gauge for the hub header (fetched when docked)
  const [slipsGauge, setSlipsGauge] = useState<{ occupied: number; capacity: number } | null>(null);

  React.useEffect(() => {
    const stationId = playerState?.current_port_id;
    if (!stationId || !playerState?.is_docked) {
      setSlipsGauge(null);
      return;
    }
    let cancelled = false;
    getStationSlips(stationId).then(info => {
      if (!cancelled && info) {
        setSlipsGauge({ occupied: info.occupied, capacity: info.capacity });
      }
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playerState?.current_port_id, playerState?.is_docked]);

  // Track local credits for immediate UI feedback
  const [localCredits, setLocalCredits] = useState<number | null>(null);

  // Get token from localStorage (AuthContext doesn't expose it)
  const getToken = () => localStorage.getItem('accessToken');

  // Use local credits if set, otherwise use playerState credits
  const displayCredits = localCredits ?? playerState?.credits ?? 0;

  // Sync local credits when playerState changes
  React.useEffect(() => {
    if (playerState?.credits !== undefined) {
      setLocalCredits(playerState.credits);
    }
  }, [playerState?.credits]);

  // Gambling state
  const [currentGame, setCurrentGame] = useState<GamblingGame>('menu');
  const [betAmount, setBetAmount] = useState<number>(100);
  const [slotReels, setSlotReels] = useState<string[]>(['❓', '❓', '❓']);
  const [isSpinning, setIsSpinning] = useState(false);
  const [lastWin, setLastWin] = useState<number | null>(null);
  const [diceValues, setDiceValues] = useState<number[]>([0, 0]);
  const [diceBetType, setDiceBetType] = useState<'high' | 'low' | 'exact'>('high');
  const [diceExactBet, setDiceExactBet] = useState<number>(7);
  const [isSupernova, setIsSupernova] = useState(false);
  const [isVoid, setIsVoid] = useState(false);
  const [isJackpot, setIsJackpot] = useState(false);

  // Lottery state
  const [lotteryNumbers, setLotteryNumbers] = useState<number[]>([]);
  const [winningNumbers, setWinningNumbers] = useState<number[]>([]);
  const [lotteryMatches, setLotteryMatches] = useState<number | null>(null);
  const [isLotteryPlaying, setIsLotteryPlaying] = useState(false);

  // Blackjack state
  const [blackjackGame, setBlackjackGame] = useState<BlackjackGameState | null>(null);
  const [isBlackjackDealing, setIsBlackjackDealing] = useState(false);

  // Black market state
  const [showBlackMarket, setShowBlackMarket] = useState(false);

  // Real contraband catalog (GET /api/v1/trading/black-market/{station_id}).
  // The endpoint is the authoritative gate: a 404 means either this isn't a
  // BLACK_MARKET venue OR the player's OUTLAWS rep is below RECOGNIZED — both
  // collapse to "no underworld contacts" so the gate never advertises itself.
  const [bmCatalog, setBmCatalog] = useState<BlackMarketCatalog | null>(null);
  const [bmLoading, setBmLoading] = useState(false);
  const [bmGateClosed, setBmGateClosed] = useState(false); // 404 from the catalog GET
  const [bmCatalogError, setBmCatalogError] = useState<string | null>(null);
  const [bmQuantities, setBmQuantities] = useState<Record<string, number>>({});
  const [bmBusy, setBmBusy] = useState<string | null>(null); // `buy:<c>` / `sell:<c>` in flight
  const [bmError, setBmError] = useState<string | null>(null);
  const [bmSuccess, setBmSuccess] = useState<string | null>(null);
  const [bmDetected, setBmDetected] = useState<string | null>(null); // bust feedback (fine/heat)

  // Planetary registry lookup (shadow-broker widget inside the black market modal)
  const [registryQueryName, setRegistryQueryName] = useState('');
  const [registryLoading, setRegistryLoading] = useState(false);
  const [registryError, setRegistryError] = useState<string | null>(null);
  const [registryResults, setRegistryResults] = useState<
    { name: string; sectorId: number | string; planetType: string; registrationStatus: string }[] | null
  >(null);

  // Error state
  const [gamblingError, setGamblingError] = useState<string | null>(null);
  const [genesisError, setGenesisError] = useState<string | null>(null);
  const [genesisPurchasing, setGenesisPurchasing] = useState(false);
  const [genesisSuccess, setGenesisSuccess] = useState<string | null>(null);
  // Weekly acquisition limit readout (canon: 3/week).
  const [genesisWeeklyRemaining, setGenesisWeeklyRemaining] = useState<number | null>(null);
  const [genesisWeeklyLimit, setGenesisWeeklyLimit] = useState<number>(3);
  // Federation reputation gate (ADR-0088). Rides a separate deploy window
  // from the rest of this response — GRACEFUL-DEGRADE: null (hide the rep
  // row entirely) unless the server actually sends the field.
  const [genesisRepGate, setGenesisRepGate] = useState<{ required: number; current: number; met: boolean } | null>(null);

  // Local genesis tracking for immediate UI feedback
  const [localGenesisDevices, setLocalGenesisDevices] = useState<number | null>(null);
  const [localMaxGenesis, setLocalMaxGenesis] = useState<number | null>(null);

  // Prefetch the weekly acquisition allowance when the genesis venue opens so the
  // "N of 3 left" readout is present before the first purchase.
  React.useEffect(() => {
    if (activeVenue !== 'genesis') return;
    const token = getToken();
    if (!token) return;
    fetch(`${getApiBaseUrl()}/api/v1/genesis/available`, {
      headers: { Authorization: `Bearer ${token}` }
    })
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (!data) return;
        if (typeof data.purchases_remaining === 'number') setGenesisWeeklyRemaining(data.purchases_remaining);
        if (typeof data.max_purchases_per_week === 'number') setGenesisWeeklyLimit(data.max_purchases_per_week);
        const gate = data.reputation_gate;
        if (gate && typeof gate === 'object'
          && typeof gate.required === 'number'
          && typeof gate.current === 'number'
          && typeof gate.met === 'boolean') {
          setGenesisRepGate(gate);
        } else {
          setGenesisRepGate(null);
        }
      })
      .catch(() => {});
  }, [activeVenue]);

  // Shipyard state
  const [shipCatalog, setShipCatalog] = useState<ShipCatalogEntry[] | null>(null);
  const [shipCatalogLoading, setShipCatalogLoading] = useState(false);
  const [shipCatalogError, setShipCatalogError] = useState<string | null>(null);
  const [confirmShip, setConfirmShip] = useState<ShipCatalogEntry | null>(null);
  const [newShipName, setNewShipName] = useState('');
  const [shipPurchasing, setShipPurchasing] = useState(false);
  const [shipPurchaseError, setShipPurchaseError] = useState<string | null>(null);
  const [shipPurchaseSuccess, setShipPurchaseSuccess] = useState<string | null>(null);

  // Armory state
  const [armoryCatalog, setArmoryCatalog] = useState<ArmoryCatalogItem[] | null>(null);
  const [armoryLoading, setArmoryLoading] = useState(false);
  const [armoryCatalogError, setArmoryCatalogError] = useState<string | null>(null);
  const [armoryLoadout, setArmoryLoadout] = useState<ArmoryLoadout | null>(null);
  const [armoryQuantities, setArmoryQuantities] = useState<Record<string, number>>({});
  const [armoryBuying, setArmoryBuying] = useState<string | null>(null);
  const [armoryError, setArmoryError] = useState<string | null>(null);
  const [armorySuccess, setArmorySuccess] = useState<string | null>(null);

  // Get the current docked station
  const currentStation = stationsInSector?.find(
    s => s.id === playerState?.current_port_id
  ) as DockedStation | undefined;

  // TradeDock construction tier — the field arrives via the sector stations
  // payload and may be absent entirely on older payloads; feature-detect it.
  const rawTier = typeof currentStation?.tradedock_tier === 'string'
    ? currentStation.tradedock_tier.toUpperCase()
    : null;
  const tradedockTier: 'A' | 'B' | null = rawTier === 'A' || rawTier === 'B' ? rawTier : null;

  // Black-market entry affordance: show the "figure in the shadows" knock-on-
  // the-door button when the DOCKED STATION is a BLACK_MARKET venue. The real
  // access gate (Fringe-Alliance/OUTLAWS rep ≥ RECOGNIZED) is enforced
  // server-side by the catalog endpoint — the modal surfaces "no underworld
  // contacts" on a 404 — so we deliberately do NOT gate the button on the local
  // personal_reputation<0 flag (that flag is not the backend gate).
  const stationIsBlackMarket =
    typeof currentStation?.type === 'string' &&
    currentStation.type.toUpperCase() === 'BLACK_MARKET';
  const hasBlackMarketAccess = stationIsBlackMarket;

  // Define available venues based on station services
  const stationServices = currentStation?.services || {};

  const venues: Venue[] = [
    {
      id: 'trading',
      name: 'Trading Hub',
      icon: '🏪',
      description: 'Premium commodity trading with bulk discounts and special goods',
      available: true,
      services: ['Bulk Discounts', 'Special Commodities', 'No Transaction Fees']
    },
    {
      id: 'shipyard',
      name: 'Shipyard',
      icon: '🛠️',
      description: 'Build custom ships from resources or purchase pre-fabricated vessels',
      available: Boolean(stationServices.ship_dealer),
      services: ['Custom Ship Building', 'Dock Slip Rental', 'Ship Customization']
    },
    // Construction only exists at TradeDock stations (tier A or B) —
    // it is omitted entirely everywhere else rather than shown as unavailable
    ...(tradedockTier ? [{
      id: 'construction' as VenueType,
      name: 'Construction',
      icon: '🏗️',
      description: 'Order new hulls built to spec in this TradeDock\'s construction slips',
      available: true,
      services: [`Tier ${tradedockTier} Slips`, 'Ship Orders', 'Build Tracking']
    }] : []),
    // Port Office is universal — every station keeps a registry desk,
    // whether or not the deed itself is purchasable
    {
      id: 'portoffice',
      name: 'Port Office',
      icon: '🏛️',
      description: 'Station registry — ownership deeds, sealed-bid sales, tariffs, and takeover filings',
      available: true,
      services: ['Ownership Registry', 'Sealed-Bid Offers', 'Tariff & Treasury', 'Takeover War Room']
    },
    // Contract Board is universal — every station keeps a delivery board,
    // whether or not it has any NPC or player-posted contracts yet
    {
      id: 'contracts',
      name: 'Contract Board',
      icon: '📋',
      description: 'Delivery contracts — accept board postings, post your own, track your obligations',
      available: true,
      services: ['Delivery Board', 'Player-Posted Contracts', 'Escrow', 'Deadline Tracking']
    },
    {
      id: 'genesis',
      name: 'Genesis Store',
      icon: '🌍',
      description: 'Acquire Genesis Devices - the key to creating new worlds',
      available: Boolean(stationServices.genesis_dealer),
      services: ['Genesis Devices', 'World Creation', 'Terraforming Tech']
    },
    {
      id: 'armory',
      name: 'Armory',
      icon: '⚔️',
      description: 'Combat drones, defense systems, and tactical equipment',
      // SpaceDocks carry every armory service automatically (matches the server's
      // _station_offers_service); without this the Armory tab was hidden at a
      // SpaceDock that didn't explicitly list drone_shop/mine_dealer.
      available: Boolean(stationServices.drone_shop) || Boolean(stationServices.mine_dealer) || Boolean(currentStation?.is_spacedock),
      services: ['Attack Drones', 'Defense Drones', 'Mines', 'Tactical Systems']
    },
    {
      id: 'services',
      name: 'Ship Services',
      icon: '🔧',
      description: 'Hull and shield repair plus ship condition readouts',
      available: Boolean(stationServices.ship_repair) || Boolean(stationServices.ship_maintenance),
      services: ['Ship Repair', 'Hull & Shield Status', 'Cargo Readout']
    },
    {
      id: 'mining',
      name: 'Astral Mining',
      icon: '⛏️',
      description: 'Astral Mining Consortium — claim-license desk and Mining Laser refits',
      // The Consortium maintains an office at every dock; the claim license is
      // sector-bound (server-enforced), the laser refit is universal.
      available: true,
      services: ['Claim Licenses', 'Mining Laser Refits']
    },
    {
      id: 'gambling',
      name: 'Gambling Hall',
      icon: '🎰',
      description: 'Test your luck with games of chance and skill',
      available: true,
      services: ['Cosmic Slots', 'Nebula Dice', 'Stellar Blackjack', 'Sector Lottery']
    }
  ];

  // Gambling game logic - API based
  const spinSlots = useCallback(async () => {
    const token = getToken();
    if (isSpinning || displayCredits < betAmount || !token) {
      if (!token) setGamblingError('Not authenticated. Please log in again.');
      return;
    }

    setIsSpinning(true);
    setLastWin(null);
    setIsJackpot(false);
    setGamblingError(null);

    // Immediately deduct credits from UI for instant feedback
    setLocalCredits(prev => (prev ?? displayCredits) - betAmount);

    // Simulate spinning animation
    let spins = 0;
    const spinInterval = setInterval(() => {
      setSlotReels([
        SLOT_SYMBOLS[Math.floor(Math.random() * SLOT_SYMBOLS.length)],
        SLOT_SYMBOLS[Math.floor(Math.random() * SLOT_SYMBOLS.length)],
        SLOT_SYMBOLS[Math.floor(Math.random() * SLOT_SYMBOLS.length)]
      ]);
      spins++;

      if (spins >= 15) {
        clearInterval(spinInterval);
      }
    }, 100);

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/gambling/slots/spin`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ bet_amount: betAmount })
      });

      // Wait for animation to finish
      await new Promise(resolve => setTimeout(resolve, 1500));
      clearInterval(spinInterval);

      if (!response.ok) {
        const error = await response.json();
        setGamblingError(error.detail || 'Spin failed');
        setSlotReels(['❌', '❌', '❌']);
        // Restore credits on error
        setLocalCredits(prev => (prev ?? displayCredits) + betAmount);
        setIsSpinning(false);
        return;
      }

      const result = await response.json();
      setSlotReels(result.reels);
      setLastWin(result.net_result);
      setIsJackpot(result.jackpot);

      // Update credits from server response for accuracy
      setLocalCredits(result.new_credits);
      // Update global player state credits for header display
      updatePlayerCredits(result.new_credits);
    } catch (error) {
      console.error('Slots error:', error);
      setGamblingError('Connection error. Please try again.');
      setSlotReels(['❌', '❌', '❌']);
      // Restore credits on error
      setLocalCredits(prev => (prev ?? displayCredits) + betAmount);
    } finally {
      setIsSpinning(false);
    }
  }, [betAmount, isSpinning, displayCredits, updatePlayerCredits]);

  const rollDice = useCallback(async () => {
    const token = getToken();
    if (displayCredits < betAmount || !token) {
      if (!token) setGamblingError('Not authenticated. Please log in again.');
      return;
    }

    setLastWin(null);
    setIsSupernova(false);
    setIsVoid(false);
    setGamblingError(null);

    // Immediately deduct credits from UI for instant feedback
    setLocalCredits(prev => (prev ?? displayCredits) - betAmount);

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/gambling/dice/roll`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          bet_amount: betAmount,
          bet_type: diceBetType,
          exact_number: diceBetType === 'exact' ? diceExactBet : null
        })
      });

      if (!response.ok) {
        const error = await response.json();
        setGamblingError(error.detail || 'Roll failed');
        // Restore credits on error
        setLocalCredits(prev => (prev ?? displayCredits) + betAmount);
        return;
      }

      const result = await response.json();
      setDiceValues(result.dice);
      setLastWin(result.net_result);
      setIsSupernova(result.supernova);
      setIsVoid(result.void);

      // Update credits from server response for accuracy
      setLocalCredits(result.new_credits);
      // Update global player state credits for header display
      updatePlayerCredits(result.new_credits);
    } catch (error) {
      console.error('Dice error:', error);
      setGamblingError('Connection error. Please try again.');
      // Restore credits on error
      setLocalCredits(prev => (prev ?? displayCredits) + betAmount);
    }
  }, [betAmount, diceBetType, diceExactBet, displayCredits, updatePlayerCredits]);

  // Lottery functions
  const toggleLotteryNumber = useCallback((num: number) => {
    setLotteryNumbers(prev => {
      if (prev.includes(num)) {
        return prev.filter(n => n !== num);
      }
      if (prev.length >= 4) {
        return prev; // Max 4 numbers
      }
      return [...prev, num];
    });
  }, []);

  const playLottery = useCallback(async () => {
    const token = getToken();
    if (lotteryNumbers.length !== 4 || !token || displayCredits < betAmount) {
      if (!token) setGamblingError('Not authenticated. Please log in again.');
      return;
    }

    setIsLotteryPlaying(true);
    setLotteryMatches(null);
    setWinningNumbers([]);
    setLastWin(null);
    setGamblingError(null);

    // Immediately deduct credits from UI for instant feedback
    setLocalCredits(prev => (prev ?? displayCredits) - betAmount);

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/gambling/lottery/buy-ticket`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          numbers: lotteryNumbers,
          bet_amount: betAmount
        })
      });

      if (!response.ok) {
        const error = await response.json();
        setGamblingError(error.detail || 'Lottery failed');
        // Restore credits on error
        setLocalCredits(prev => (prev ?? displayCredits) + betAmount);
        setIsLotteryPlaying(false);
        return;
      }

      const result = await response.json();
      setWinningNumbers(result.winning_numbers);
      setLotteryMatches(result.matches);
      setLastWin(result.net_result);
      setIsJackpot(result.jackpot);

      // Update credits from server response for accuracy
      setLocalCredits(result.new_credits);
      // Update global player state credits for header display
      updatePlayerCredits(result.new_credits);
    } catch (error) {
      console.error('Lottery error:', error);
      setGamblingError('Connection error. Please try again.');
      // Restore credits on error
      setLocalCredits(prev => (prev ?? displayCredits) + betAmount);
    } finally {
      setIsLotteryPlaying(false);
    }
  }, [lotteryNumbers, betAmount, displayCredits, updatePlayerCredits]);

  // Blackjack functions
  const dealBlackjack = useCallback(async () => {
    const token = getToken();
    if (isBlackjackDealing || displayCredits < betAmount || !token) {
      if (!token) setGamblingError('Not authenticated. Please log in again.');
      return;
    }

    setIsBlackjackDealing(true);
    setGamblingError(null);
    setLastWin(null);
    setBlackjackGame(null);

    // Immediately deduct credits from UI for instant feedback
    setLocalCredits(prev => (prev ?? displayCredits) - betAmount);

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/gambling/blackjack/deal`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ bet_amount: betAmount })
      });

      if (!response.ok) {
        const error = await response.json();
        setGamblingError(error.detail || 'Deal failed');
        setLocalCredits(prev => (prev ?? displayCredits) + betAmount);
        setIsBlackjackDealing(false);
        return;
      }

      const result = await response.json();
      setBlackjackGame({
        playerCards: result.player_cards,
        dealerCards: result.dealer_cards,
        playerTotal: result.player_total,
        dealerTotal: result.dealer_total,
        gameOver: result.game_over,
        result: result.result,
        canDouble: result.can_double,
        deckSeed: result.deck_seed
      });

      if (result.game_over) {
        setLastWin(result.net_result);
        setLocalCredits(result.new_credits);
        updatePlayerCredits(result.new_credits);
      }
    } catch (error) {
      console.error('Blackjack deal error:', error);
      setGamblingError('Connection error. Please try again.');
      setLocalCredits(prev => (prev ?? displayCredits) + betAmount);
    } finally {
      setIsBlackjackDealing(false);
    }
  }, [betAmount, isBlackjackDealing, displayCredits, updatePlayerCredits]);

  const blackjackAction = useCallback(async (action: 'hit' | 'stand' | 'double') => {
    const token = getToken();
    if (!blackjackGame || blackjackGame.gameOver || !token) return;

    // For double down, check if player has enough credits
    if (action === 'double') {
      if (displayCredits < betAmount) {
        setGamblingError('Insufficient credits to double down');
        return;
      }
      // Deduct additional bet immediately for double
      setLocalCredits(prev => (prev ?? displayCredits) - betAmount);
    }

    setGamblingError(null);

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/gambling/blackjack/action`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          bet_amount: betAmount,
          player_cards: blackjackGame.playerCards,
          dealer_cards: blackjackGame.dealerCards,
          deck_seed: blackjackGame.deckSeed,
          action: action
        })
      });

      if (!response.ok) {
        const error = await response.json();
        setGamblingError(error.detail || 'Action failed');
        // Restore credits if double failed
        if (action === 'double') {
          setLocalCredits(prev => (prev ?? displayCredits) + betAmount);
        }
        return;
      }

      const result = await response.json();
      setBlackjackGame({
        playerCards: result.player_cards,
        dealerCards: result.dealer_cards,
        playerTotal: result.player_total,
        dealerTotal: result.dealer_total,
        gameOver: result.game_over,
        result: result.result,
        canDouble: result.can_double,
        deckSeed: result.deck_seed
      });

      if (result.game_over) {
        setLastWin(result.net_result);
        setLocalCredits(result.new_credits);
        updatePlayerCredits(result.new_credits);
      }
    } catch (error) {
      console.error('Blackjack action error:', error);
      setGamblingError('Connection error. Please try again.');
      if (action === 'double') {
        setLocalCredits(prev => (prev ?? displayCredits) + betAmount);
      }
    }
  }, [blackjackGame, betAmount, displayCredits, updatePlayerCredits]);

  // Genesis Device Purchase function
  // Genesis devices are a single fungible consumable; the tier + credit cost are
  // chosen at deploy. Acquiring one costs a flat GENESIS_DEVICE_PRICE and is
  // rate-limited to 3/week (server-enforced).
  const GENESIS_DEVICE_PRICE = 25000;

  // The raw purchase endpoint uses `fetch` directly (not the apiRequest/apiClient
  // wrapper), so it doesn't get that layer's detail-extraction for free. A plain
  // `error.detail || 'Purchase failed'` silently loses the real reason whenever
  // the gameserver's global error handler wraps it as `{message}` instead, or
  // renders `[object Object]` when a 422 sends `detail` as FastAPI's validation
  // array (`[{loc, msg, type}, ...]`) rather than a string.
  const extractGenesisErrorDetail = (error: unknown, fallback: string): string => {
    const raw = (error as { detail?: unknown; message?: unknown } | null)?.detail
      ?? (error as { detail?: unknown; message?: unknown } | null)?.message;
    if (typeof raw === 'string' && raw) return raw;
    if (Array.isArray(raw)) {
      const msgs = raw
        .map(e => (e && typeof e === 'object' && typeof (e as { msg?: unknown }).msg === 'string' ? (e as { msg: string }).msg : null))
        .filter((m): m is string => Boolean(m));
      if (msgs.length) return msgs.join('; ');
    }
    return fallback;
  };

  const purchaseGenesisDevice = useCallback(async () => {
    const token = getToken();
    if (!token || genesisPurchasing) return;

    const price = GENESIS_DEVICE_PRICE;
    if (displayCredits < price) {
      setGenesisError(`Insufficient credits. Need ${price.toLocaleString()}, have ${displayCredits.toLocaleString()}`);
      return;
    }

    setGenesisPurchasing(true);
    setGenesisError(null);
    setGenesisSuccess(null);

    // Immediately deduct credits for instant feedback
    setLocalCredits(prev => (prev ?? displayCredits) - price);

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/player/genesis/purchase`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({})
      });

      if (!response.ok) {
        const error = await response.json().catch(() => null);
        setGenesisError(extractGenesisErrorDetail(error, 'Purchase failed'));
        // Restore credits on error
        setLocalCredits(prev => (prev ?? displayCredits) + price);
        setGenesisPurchasing(false);
        return;
      }

      const result = await response.json();
      setLocalCredits(result.new_credits);
      setLocalGenesisDevices(result.genesis_devices);
      setLocalMaxGenesis(result.max_genesis_devices);
      if (typeof result.purchases_remaining === 'number') setGenesisWeeklyRemaining(result.purchases_remaining);
      if (typeof result.weekly_limit === 'number') setGenesisWeeklyLimit(result.weekly_limit);
      updatePlayerCredits(result.new_credits);
      updateShipGenesis(result.genesis_devices);  // Update sidebar immediately
      setGenesisSuccess(result.message);

      // Clear success message after 3 seconds
      setTimeout(() => setGenesisSuccess(null), 3000);
    } catch (error) {
      console.error('Genesis purchase error:', error);
      setGenesisError('Connection error. Please try again.');
      setLocalCredits(prev => (prev ?? displayCredits) + price);
    } finally {
      setGenesisPurchasing(false);
    }
  }, [displayCredits, genesisPurchasing, updatePlayerCredits, updateShipGenesis]);

  // Fetch current ship data including genesis device info
  const [shipData, setShipData] = useState<{
    id: string;
    genesis_devices: number;
    max_genesis_devices: number;
    type: string;
    name: string;
    combat?: Record<string, unknown> | null;
    cargo?: Record<string, number> | null;
    cargo_capacity?: number;
    current_value?: number;
  } | null>(null);

  const [showInsurance, setShowInsurance] = useState(false);
  const [showMaintenance, setShowMaintenance] = useState(false);
  const [showUpgrades, setShowUpgrades] = useState(false);

  const fetchShipData = useCallback(async () => {
    const token = getToken();
    if (!token) return;

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/player/current-ship`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        const data = await response.json();
        setShipData(data);
        setLocalGenesisDevices(data.genesis_devices);
        setLocalMaxGenesis(data.max_genesis_devices);
      }
    } catch (error) {
      console.error('Failed to fetch ship data:', error);
    }
  }, []);

  React.useEffect(() => {
    fetchShipData();
  }, [fetchShipData]);

  // --- Shipyard: real catalog + purchase flow ---
  const fetchShipCatalog = useCallback(async () => {
    const token = getToken();
    if (!token) {
      setShipCatalogError('Not authenticated. Please log in again.');
      return;
    }

    setShipCatalogLoading(true);
    setShipCatalogError(null);

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/ships/catalog`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });

      if (!response.ok) {
        const error = await response.json().catch(() => null);
        setShipCatalogError(typeof (error?.message ?? error?.detail) === 'string' ? (error?.message ?? error?.detail) : 'Failed to load ship catalog');
        return;
      }

      const data = await response.json();
      setShipCatalog(data.ships || []);
    } catch (error) {
      console.error('Ship catalog error:', error);
      setShipCatalogError('Connection error. Please try again.');
    } finally {
      setShipCatalogLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (activeVenue === 'shipyard') {
      fetchShipCatalog();
      setShipPurchaseError(null);
      setShipPurchaseSuccess(null);
      setConfirmShip(null);
    }
  }, [activeVenue, fetchShipCatalog]);

  const purchaseShip = useCallback(async (entry: ShipCatalogEntry, requestedName: string) => {
    const token = getToken();
    if (!token || shipPurchasing) {
      if (!token) setShipPurchaseError('Not authenticated. Please log in again.');
      return;
    }

    if (displayCredits < entry.base_cost) {
      setShipPurchaseError(`Insufficient credits. Need ${entry.base_cost.toLocaleString()}, have ${displayCredits.toLocaleString()}`);
      return;
    }

    setShipPurchasing(true);
    setShipPurchaseError(null);
    setShipPurchaseSuccess(null);

    try {
      const body: { ship_type: string; name?: string } = { ship_type: entry.type };
      const trimmedName = requestedName.trim();
      if (trimmedName) {
        body.name = trimmedName;
      }

      const response = await fetch(`${getApiBaseUrl()}/api/v1/ships/purchase`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(body)
      });

      if (!response.ok) {
        const error = await response.json().catch(() => null);
        setShipPurchaseError(typeof (error?.message ?? error?.detail) === 'string' ? (error?.message ?? error?.detail) : 'Purchase failed');
        return;
      }

      const result = await response.json();
      setLocalCredits(result.remaining_credits);
      updatePlayerCredits(result.remaining_credits);
      setShipPurchaseSuccess(`Purchase complete — ${result.ship.name} is ready in the hangar.`);
      setConfirmShip(null);
      setNewShipName('');

      // Sync global player + fleet state so the rest of the UI catches up
      await Promise.allSettled([loadShips(), refreshPlayerState(), fetchShipData()]);
    } catch (error) {
      console.error('Ship purchase error:', error);
      setShipPurchaseError('Connection error. Please try again.');
    } finally {
      setShipPurchasing(false);
    }
  }, [shipPurchasing, displayCredits, updatePlayerCredits, loadShips, refreshPlayerState, fetchShipData]);

  // --- Armory: real catalog + purchase flow ---
  const fetchArmoryCatalog = useCallback(async () => {
    const token = getToken();
    if (!token) {
      setArmoryCatalogError('Not authenticated. Please log in again.');
      return;
    }

    setArmoryLoading(true);
    setArmoryCatalogError(null);

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/armory/catalog`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });

      if (!response.ok) {
        const error = await response.json().catch(() => null);
        setArmoryCatalogError(typeof (error?.message ?? error?.detail) === 'string' ? (error?.message ?? error?.detail) : 'Failed to load armory catalog');
        return;
      }

      const data = await response.json();
      setArmoryCatalog(Array.isArray(data) ? data : (data.items || []));
      if (data.loadout) {
        setArmoryLoadout(data.loadout);
      }
    } catch (error) {
      console.error('Armory catalog error:', error);
      setArmoryCatalogError('Connection error. Please try again.');
    } finally {
      setArmoryLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (activeVenue === 'armory') {
      fetchArmoryCatalog();
      setArmoryError(null);
      setArmorySuccess(null);
    }
  }, [activeVenue, fetchArmoryCatalog]);

  const purchaseArmoryItem = useCallback(async (item: ArmoryCatalogItem, quantity: number) => {
    const token = getToken();
    if (!token || armoryBuying) {
      if (!token) setArmoryError('Not authenticated. Please log in again.');
      return;
    }

    const totalCost = item.price * quantity;
    if (displayCredits < totalCost) {
      setArmoryError(`Insufficient credits. Need ${totalCost.toLocaleString()}, have ${displayCredits.toLocaleString()}`);
      return;
    }

    setArmoryBuying(item.item);
    setArmoryError(null);
    setArmorySuccess(null);

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/armory/purchase`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ item: item.item, quantity })
      });

      if (!response.ok) {
        const error = await response.json().catch(() => null);
        const rawDetail = error?.message ?? error?.detail;
        setArmoryError(typeof rawDetail === 'string' && rawDetail
          ? rawDetail
          : 'Purchase failed');
        return;
      }

      const result = await response.json();
      setLocalCredits(result.remaining_credits);
      updatePlayerCredits(result.remaining_credits);
      if (result.loadout) {
        setArmoryLoadout(result.loadout);
      }
      setArmorySuccess(`${quantity} × ${item.name} loaded aboard.`);
      setTimeout(() => setArmorySuccess(null), 3000);

      // Sync header credits and drone counts with the server
      refreshPlayerState();
    } catch (error) {
      console.error('Armory purchase error:', error);
      setArmoryError('Connection error. Please try again.');
    } finally {
      setArmoryBuying(null);
    }
  }, [armoryBuying, displayCredits, updatePlayerCredits, refreshPlayerState]);

  // --- Ship Services: real repair flow ---
  const [repairBusy, setRepairBusy] = useState(false);
  const [repairError, setRepairError] = useState<string | null>(null);
  const [repairSuccess, setRepairSuccess] = useState<string | null>(null);

  React.useEffect(() => {
    if (activeVenue === 'services') {
      // Refresh hull/shield/cargo readings on entry so the gauges are live
      fetchShipData();
      setRepairError(null);
      setRepairSuccess(null);
    }
  }, [activeVenue, fetchShipData]);

  // Hull insurance coverage tier — surfaced inline on the Services venue.
  // Coverage attaches to the hull for life (station-independent), so it's
  // shown regardless of whether this station's underwriter desk is open.
  const [insuranceTier, setInsuranceTier] = useState<string | null>(null);

  const fetchInsuranceStatus = useCallback(async (shipId: string) => {
    try {
      const data = await shipAPI.getInsurance(shipId) as { current_tier?: string };
      setInsuranceTier(typeof data?.current_tier === 'string' ? data.current_tier : null);
    } catch (error) {
      console.error('Insurance status error:', error);
      setInsuranceTier(null);
    }
  }, []);

  React.useEffect(() => {
    if (activeVenue === 'services' && shipData?.id) {
      fetchInsuranceStatus(shipData.id);
    }
  }, [activeVenue, shipData?.id, fetchInsuranceStatus]);

  const repairShip = useCallback(async () => {
    const token = getToken();
    const shipId = shipData?.id;
    if (!token || !shipId || repairBusy) {
      if (!token) setRepairError('Not authenticated. Please log in again.');
      return;
    }

    setRepairBusy(true);
    setRepairError(null);
    setRepairSuccess(null);

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/player/ships/${shipId}/repair`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });

      if (!response.ok) {
        const error = await response.json().catch(() => null);
        const rawDetail = error?.message ?? error?.detail;
        setRepairError(typeof rawDetail === 'string' && rawDetail ? rawDetail : 'Repair failed');
        return;
      }

      const result = await response.json();
      setLocalCredits(result.credits_remaining);
      updatePlayerCredits(result.credits_remaining);
      // Copy-reassign combat so the gauges re-render with restored values
      setShipData(prev => prev ? {
        ...prev,
        combat: {
          ...(prev.combat ?? {}),
          hull: result.hull,
          shields: result.shields,
          max_hull: result.max_hull,
          max_shields: result.max_shields
        }
      } : prev);
      setRepairSuccess(result.message || 'Ship repaired.');
      setTimeout(() => setRepairSuccess(null), 3000);

      // Sync header credits and ship condition with the server
      refreshPlayerState();
    } catch (error) {
      console.error('Ship repair error:', error);
      setRepairError('Connection error. Please try again.');
    } finally {
      setRepairBusy(false);
    }
  }, [shipData?.id, repairBusy, updatePlayerCredits, refreshPlayerState]);

  // Credits plumbing for the Construction venue — instant optimistic feedback
  // plus authoritative totals when the server returns them
  const handleCreditsDelta = useCallback((delta: number) => {
    setLocalCredits(prev => (prev ?? playerState?.credits ?? 0) + delta);
  }, [playerState?.credits]);

  const handleCreditsSet = useCallback((value: number) => {
    setLocalCredits(value);
    updatePlayerCredits(value);
  }, [updatePlayerCredits]);

  // --- Astral Mining: claim-license purchase + Mining Laser refit ---
  const [licenseBusy, setLicenseBusy] = useState(false);
  const [licenseError, setLicenseError] = useState<string | null>(null);
  const [licenseSuccess, setLicenseSuccess] = useState<string | null>(null);
  const [laserBusy, setLaserBusy] = useState(false);
  const [laserError, setLaserError] = useState<string | null>(null);
  const [laserSuccess, setLaserSuccess] = useState<string | null>(null);

  React.useEffect(() => {
    if (activeVenue === 'mining') {
      // Ensure ship telemetry (ship id) is fresh on entry; clear stale feedback.
      fetchShipData();
      setLicenseError(null);
      setLicenseSuccess(null);
      setLaserError(null);
      setLaserSuccess(null);
    }
  }, [activeVenue, fetchShipData]);

  // Purchase / renew the AM claim license for the current sector
  // (POST /api/v1/mining/license {ship_id}). The fee is sector-tier-scaled and
  // server-authoritative; a non-asteroid sector returns not_an_asteroid_field.
  const purchaseClaimLicense = useCallback(async () => {
    const token = getToken();
    const shipId = shipData?.id;
    if (!token || !shipId || licenseBusy) {
      if (!token) setLicenseError('Not authenticated. Please log in again.');
      else if (!shipId) setLicenseError('No active ship found.');
      return;
    }
    setLicenseBusy(true);
    setLicenseError(null);
    setLicenseSuccess(null);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/mining/license`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ ship_id: shipId })
      });
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        const rawDetail = error?.detail ?? error?.message;
        const reason = typeof rawDetail === 'string' && rawDetail ? rawDetail : 'License purchase failed';
        // Map the most common stable reason to a friendlier line.
        setLicenseError(
          reason === 'not_an_asteroid_field'
            ? 'Claim licenses are only sold for asteroid-field sectors. Fly to one to file a claim.'
            : reason === 'insufficient_credits'
              ? 'Insufficient credits for this claim license.'
              : reason
        );
        return;
      }
      const result = await response.json();
      const cost = formatCredits(result.cost_paid_cr ?? 0);
      const expires = result.expires_at ? new Date(result.expires_at).toLocaleString() : 'soon';
      setLicenseSuccess(`Claim filed for ${cost} — license valid until ${expires}.`);
      Promise.allSettled([refreshPlayerState(), fetchShipData()]);
    } catch (error) {
      console.error('Claim license error:', error);
      setLicenseError('Connection error. Please try again.');
    } finally {
      setLicenseBusy(false);
    }
  }, [shipData?.id, licenseBusy, refreshPlayerState, fetchShipData]);

  // Buy the next Mining Laser ladder level
  // (POST /api/v1/mining/laser-upgrade {ship_id}). Requires a Mining Laser
  // already installed (the server surfaces a clear message otherwise).
  const upgradeMiningLaser = useCallback(async () => {
    const token = getToken();
    const shipId = shipData?.id;
    if (!token || !shipId || laserBusy) {
      if (!token) setLaserError('Not authenticated. Please log in again.');
      else if (!shipId) setLaserError('No active ship found.');
      return;
    }
    setLaserBusy(true);
    setLaserError(null);
    setLaserSuccess(null);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/mining/laser-upgrade`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ ship_id: shipId })
      });
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        const rawDetail = error?.detail ?? error?.message;
        setLaserError(typeof rawDetail === 'string' && rawDetail ? rawDetail : 'Mining laser upgrade failed');
        return;
      }
      const result = await response.json();
      const mult = typeof result.yield_multiplier === 'number' ? `${result.yield_multiplier}× yield` : '';
      const cost = formatCredits(result.cost_paid ?? 0);
      setLaserSuccess(
        `${result.message || `Mining Laser upgraded to level ${result.new_level}`} — ${cost}${mult ? ` (${mult})` : ''}.`
      );
      if (typeof result.remaining_credits === 'number') {
        setLocalCredits(result.remaining_credits);
        updatePlayerCredits(result.remaining_credits);
      }
      Promise.allSettled([refreshPlayerState(), fetchShipData()]);
    } catch (error) {
      console.error('Mining laser upgrade error:', error);
      setLaserError('Connection error. Please try again.');
    } finally {
      setLaserBusy(false);
    }
  }, [shipData?.id, laserBusy, updatePlayerCredits, refreshPlayerState, fetchShipData]);

  // Get current genesis device counts (use local if set, otherwise from ship data)
  const currentGenesisDevices = localGenesisDevices ?? shipData?.genesis_devices ?? 0;
  const maxGenesisDevices = localMaxGenesis ?? shipData?.max_genesis_devices ?? 0;

  // Black Market button component (appears in certain venues)
  const BlackMarketButton = () => {
    if (!hasBlackMarketAccess) return null;

    return (
      <button
        className="black-market-contact"
        onClick={() => setShowBlackMarket(true)}
        title="A shadowy figure beckons..."
      >
        <span className="shadow-icon">👤</span>
        <span className="shadow-text">A figure watches from the shadows...</span>
      </button>
    );
  };

  // Shadow-broker registry lookup: pays 50,000 cr to reveal another player's
  // non-clandestine holdings. Raw fetch (like the genesis/gambling calls) so the
  // auth token rides along. The server is authoritative on whether/when it
  // charges (404 unknown name = no charge; empty list = no charge).
  const handleRegistryLookup = async () => {
    const name = registryQueryName.trim();
    if (!name) {
      setRegistryError('Enter a player name to query.');
      return;
    }
    const token = getToken();
    try {
      setRegistryLoading(true);
      setRegistryError(null);
      setRegistryResults(null);
      const response = await fetch(`${getApiBaseUrl()}/api/v1/registry/lookup`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ playerName: name })
      });
      if (!response.ok) {
        let detail = `Lookup failed (${response.status})`;
        try {
          const errBody = await response.json();
          if (errBody && (typeof errBody.detail === 'string' || errBody.message)) {
            detail = errBody.detail || errBody.message;
          }
        } catch { /* non-JSON error body — keep the generic message */ }
        if (response.status === 404) detail = `No pilot named "${name}" on record.`;
        throw new Error(detail);
      }
      const data = await response.json();
      setRegistryResults(Array.isArray(data?.planets) ? data.planets : []);
    } catch (err) {
      setRegistryError(err instanceof Error ? err.message : 'Lookup failed.');
    } finally {
      setRegistryLoading(false);
    }
  };

  // --- Black market: real contraband catalog + buy/sell flow ---
  // The catalog GET is the authoritative gate: a 404 means either this station
  // is not a BLACK_MARKET venue OR the player's Fringe-Alliance (OUTLAWS) rep is
  // below RECOGNIZED. Both are deliberately indistinguishable — we surface "no
  // underworld contacts" rather than the catalog. Raw fetch (the established
  // in-component pattern, like handleRegistryLookup / the gambling/genesis calls)
  // so the auth token rides along.
  const fetchBlackMarketCatalog = useCallback(async (stationId: string) => {
    const token = getToken();
    if (!token) {
      setBmCatalogError('Not authenticated. Please log in again.');
      return;
    }
    setBmLoading(true);
    setBmCatalogError(null);
    setBmGateClosed(false);
    setBmCatalog(null);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/trading/black-market/${stationId}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (response.status === 404) {
        // Gate unmet OR not a black-market venue — same response by design.
        setBmGateClosed(true);
        return;
      }
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        const rawDetail = error?.detail ?? error?.message;
        setBmCatalogError(typeof rawDetail === 'string' && rawDetail ? rawDetail : 'Could not reach the shadow market.');
        return;
      }
      const data: BlackMarketCatalog = await response.json();
      setBmCatalog(data);
    } catch (error) {
      console.error('Black-market catalog error:', error);
      setBmCatalogError('Connection error. Please try again.');
    } finally {
      setBmLoading(false);
    }
  }, []);

  // Fetch the real catalog when the modal opens (gates visibility off the
  // endpoint, never the local personal_reputation flag). Reset transient
  // buy/sell feedback on open/close.
  React.useEffect(() => {
    if (!showBlackMarket) {
      setBmError(null);
      setBmSuccess(null);
      setBmDetected(null);
      return;
    }
    const stationId = currentStation?.id;
    if (stationId) {
      fetchBlackMarketCatalog(stationId);
    } else {
      setBmGateClosed(true);
    }
    setBmError(null);
    setBmSuccess(null);
    setBmDetected(null);
  }, [showBlackMarket, currentStation?.id, fetchBlackMarketCatalog]);

  // Buy contraband: POST /trading/black-market/buy. SLAVES is never in the
  // catalog (server-excluded) so there is no path to request it from this UI.
  const buyContraband = useCallback(async (listing: ContrabandListing, quantity: number) => {
    const token = getToken();
    const stationId = currentStation?.id;
    if (!token || !stationId || bmBusy) {
      if (!token) setBmError('Not authenticated. Please log in again.');
      return;
    }
    setBmBusy(`buy:${listing.commodity}`);
    setBmError(null);
    setBmSuccess(null);
    setBmDetected(null);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/trading/black-market/buy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ station_id: stationId, commodity: listing.commodity, quantity })
      });
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        const rawDetail = error?.detail ?? error?.message;
        setBmError(typeof rawDetail === 'string' && rawDetail ? rawDetail : 'The deal fell through.');
        return;
      }
      const result = await response.json();
      if (typeof result.remaining_credits === 'number') {
        setLocalCredits(result.remaining_credits);
        updatePlayerCredits(result.remaining_credits);
      }
      setBmSuccess(`Acquired ${result.quantity} × ${prettyCommodity(listing.commodity)} for ${formatCredits(result.total_cost ?? 0)}.`);
      // Re-price the catalog (fresh haggle quotes) and sync ship cargo state.
      fetchBlackMarketCatalog(stationId);
      Promise.allSettled([refreshPlayerState(), fetchShipData()]);
    } catch (error) {
      console.error('Black-market buy error:', error);
      setBmError('Connection error. Please try again.');
    } finally {
      setBmBusy(null);
    }
  }, [currentStation?.id, bmBusy, updatePlayerCredits, fetchBlackMarketCatalog, refreshPlayerState, fetchShipData]);

  // Sell held contraband: POST /trading/black-market/sell. A DETECTED sell is
  // still a 2xx success — the response carries detected/fine/heat — so surface
  // the bust feedback distinctly from a clean payout.
  const sellContraband = useCallback(async (listing: ContrabandListing, quantity: number) => {
    const token = getToken();
    const stationId = currentStation?.id;
    if (!token || !stationId || bmBusy) {
      if (!token) setBmError('Not authenticated. Please log in again.');
      return;
    }
    setBmBusy(`sell:${listing.commodity}`);
    setBmError(null);
    setBmSuccess(null);
    setBmDetected(null);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/trading/black-market/sell`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ station_id: stationId, commodity: listing.commodity, quantity })
      });
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        const rawDetail = error?.detail ?? error?.message;
        setBmError(typeof rawDetail === 'string' && rawDetail ? rawDetail : 'The sale fell through.');
        return;
      }
      const result = await response.json();
      if (typeof result.remaining_credits === 'number') {
        setLocalCredits(result.remaining_credits);
        updatePlayerCredits(result.remaining_credits);
      }
      if (result.detected) {
        // Bust: cargo confiscated, fine levied, heat flipped.
        const fine = formatCredits(result.fine ?? 0);
        const heat = result.heat === 'wanted' ? 'You are now WANTED.' : 'You are now a SUSPECT.';
        setBmDetected(`Detected! ${result.confiscated_units ?? 0} units of contraband confiscated — fine of ${fine} levied. ${heat}`);
      } else {
        setBmSuccess(`Sold ${result.quantity} × ${prettyCommodity(listing.commodity)} for ${formatCredits(result.sale_value ?? 0)}. (No one was watching... this time.)`);
      }
      fetchBlackMarketCatalog(stationId);
      Promise.allSettled([refreshPlayerState(), fetchShipData()]);
    } catch (error) {
      console.error('Black-market sell error:', error);
      setBmError('Connection error. Please try again.');
    } finally {
      setBmBusy(null);
    }
  }, [currentStation?.id, bmBusy, updatePlayerCredits, fetchBlackMarketCatalog, refreshPlayerState, fetchShipData]);

  // Black Market Modal
  const renderBlackMarketModal = () => {
    if (!showBlackMarket) return null;

    // Held contraband counts per commodity (from the current ship cargo), so a
    // commodity the player actually holds can be sold. Cargo "contents" keys are
    // ``illegal:<commodity>`` (ContrabandService.cargo_key).
    const cargoContents = (shipData?.cargo && typeof shipData.cargo === 'object'
      ? (shipData.cargo as Record<string, unknown>).contents
      : null) as Record<string, number> | null | undefined;
    const heldOf = (commodity: string): number => {
      const v = cargoContents?.[`illegal:${commodity}`];
      return typeof v === 'number' && v > 0 ? v : 0;
    };

    return (
      <div className="black-market-overlay" onClick={() => setShowBlackMarket(false)}>
        <div className="black-market-modal" onClick={e => e.stopPropagation()}>
          <div className="bm-header">
            <div className="bm-icon">🕶️</div>
            <div className="bm-title">
              <h3>The Shadow Market</h3>
              <span className="bm-whisper">"Keep your voice down, friend..."</span>
            </div>
            <button className="bm-close" onClick={() => setShowBlackMarket(false)}>✕</button>
          </div>

          <div className="bm-reputation-warning">
            <span className="warning-icon">⚠️</span>
            Your reputation: <span className="rep-value negative">{playerState?.personal_reputation}</span>
            <span className="rep-tier">({playerState?.reputation_tier})</span>
          </div>

          {/* Buy/sell feedback */}
          {bmSuccess && (
            <div className="genesis-success-message">
              <span className="success-icon">✅</span>
              {bmSuccess}
            </div>
          )}
          {bmDetected && (
            <div className="genesis-error-message">
              <span className="error-icon">🚨</span>
              {bmDetected}
            </div>
          )}
          {bmError && (
            <div className="genesis-error-message">
              <span className="error-icon">❌</span>
              {bmError}
            </div>
          )}

          {/* Real contraband catalog — gated off the endpoint */}
          {bmLoading && (
            <div className="catalog-loading">Making contact with the fence...</div>
          )}

          {!bmLoading && bmGateClosed && (
            <div className="bm-registry-empty">
              No underworld contacts here. You have no business at this station —
              or none they'll acknowledge.
            </div>
          )}

          {!bmLoading && !bmGateClosed && bmCatalogError && (
            <div className="genesis-error-message">
              <span className="error-icon">❌</span>
              {bmCatalogError}
            </div>
          )}

          {!bmLoading && !bmGateClosed && bmCatalog && (
            <div className="bm-items">
              {bmCatalog.commodities.length === 0 ? (
                <div className="bm-registry-empty">The fence has nothing to move today.</div>
              ) : (
                bmCatalog.commodities.map(listing => {
                  const held = heldOf(listing.commodity);
                  const qty = bmQuantities[listing.commodity] ?? 1;
                  const totalBuy = listing.indicative_unit_price * qty;
                  const buyBusy = bmBusy === `buy:${listing.commodity}`;
                  const sellBusy = bmBusy === `sell:${listing.commodity}`;
                  const anyBusy = bmBusy !== null;
                  return (
                    <div key={listing.commodity} className="bm-item">
                      <div className="bm-item-info">
                        <h4>{prettyCommodity(listing.commodity)}</h4>
                        <div className="bm-item-stats">
                          <span className="bm-stat">severity: {listing.severity}</span>
                          <span className="bm-stat">~{formatCredits(listing.indicative_unit_price)}/unit</span>
                          {held > 0 && <span className="bm-stat">held: {held}</span>}
                        </div>
                      </div>
                      <div className="bm-item-purchase">
                        <input
                          type="number"
                          min={1}
                          max={100000}
                          className="bm-registry-input"
                          style={{ width: '72px' }}
                          value={qty}
                          onChange={e => {
                            const n = Math.max(1, Math.min(100000, parseInt(e.target.value, 10) || 1));
                            setBmQuantities(prev => ({ ...prev, [listing.commodity]: n }));
                          }}
                          disabled={anyBusy}
                          aria-label={`Quantity of ${prettyCommodity(listing.commodity)}`}
                        />
                        <span className="bm-price">{formatCredits(totalBuy)}</span>
                        <button
                          className="bm-buy-btn"
                          onClick={() => buyContraband(listing, qty)}
                          disabled={anyBusy || (playerState?.credits || 0) < totalBuy}
                        >
                          {buyBusy ? '...' : 'Buy'}
                        </button>
                        <button
                          className="bm-buy-btn"
                          onClick={() => sellContraband(listing, Math.min(qty, held))}
                          disabled={anyBusy || held <= 0}
                          title={held <= 0 ? 'None of this contraband in your hold' : undefined}
                        >
                          {sellBusy ? '...' : 'Sell'}
                        </button>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          )}

          <div className="bm-registry">
            <h4 className="bm-registry-title">🔍 Planetary Registry Lookup</h4>
            <p className="bm-registry-desc">
              "I know a clerk who'll pull a pilot's filed holdings... for a price."
            </p>
            <div className="bm-registry-query">
              <input
                type="text"
                className="bm-registry-input"
                placeholder="Pilot name..."
                value={registryQueryName}
                onChange={e => { setRegistryQueryName(e.target.value); if (registryError) setRegistryError(null); }}
                onKeyDown={e => { if (e.key === 'Enter' && !registryLoading) handleRegistryLookup(); }}
                disabled={registryLoading}
              />
              <button
                className="bm-buy-btn"
                onClick={handleRegistryLookup}
                disabled={registryLoading || !registryQueryName.trim()}
              >
                {registryLoading ? 'Querying...' : `Pay ${formatCredits(50000)} — Query`}
              </button>
            </div>
            <div className="bm-registry-meta">
              <span className="bm-price">{formatCredits(50000)}</span>
              <span className="bm-registry-caveat">Clandestine worlds never appear.</span>
            </div>

            {registryError && (
              <div className="bm-registry-error">{registryError}</div>
            )}

            {registryResults && (
              registryResults.length === 0 ? (
                <div className="bm-registry-empty">No registered holdings on file.</div>
              ) : (
                <div className="bm-registry-results">
                  {registryResults.map((planet, idx) => (
                    <div key={idx} className="bm-item">
                      <div className="bm-item-info">
                        <h4>{planet.name}</h4>
                        <div className="bm-item-stats">
                          <span className="bm-stat">sector: {planet.sectorId}</span>
                          <span className="bm-stat">type: {planet.planetType}</span>
                          <span className="bm-stat">status: {planet.registrationStatus}</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )
            )}
          </div>

          <div className="bm-footer">
            <p className="bm-warning-text">
              "Remember... you never saw me, and this transaction never happened."
            </p>
          </div>
        </div>
      </div>
    );
  };

  const renderHub = () => {
    const stationClass = currentStation?.station_class;
    const tagline = currentStation?.is_spacedock
      ? 'Premier Trading & Construction Facility'
      : getStationClassInfo(stationClass)?.name || 'Orbital Trading Station';
    const greeting = getStationGreeting(stationClass);

    const status = currentStation?.status;
    const statusLabel = status
      ? status.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
      : 'Unknown';
    const statusClass = status === 'OPERATIONAL' ? 'operational' : status ? 'degraded' : '';

    const activeServices = SERVICE_ICONS.filter(s => Boolean(stationServices[s.key]));

    return (
      <div className="spacedock-hub">
        <div className="hub-header">
          <div className="station-identity">
            <div className="station-logo">🚀</div>
            <div className="station-info">
              <h2>{currentStation?.name || 'SpaceDock'}</h2>
              <div className="station-tagline">{tagline}</div>
            </div>
          </div>
          <div className="station-status">
            <div className="status-item">
              <span className="status-label">Status</span>
              <span className={`status-value ${statusClass}`.trim()}>{statusLabel}</span>
            </div>
            {slipsGauge && (
              <div className="status-item">
                <span className="status-label">Slips</span>
                <span
                  className={`status-value ${slipsGauge.occupied >= slipsGauge.capacity ? 'degraded' : 'operational'}`}
                  title={`Transient slips occupied: ${slipsGauge.occupied} of ${slipsGauge.capacity}`}
                >
                  {slipsGauge.occupied}/{slipsGauge.capacity}
                </span>
              </div>
            )}
            {activeServices.length > 0 && (
              <div className="status-item">
                <span className="status-label">Services</span>
                <div className="station-service-icons">
                  {activeServices.map(s => (
                    <span key={s.key} className="station-service-icon" title={s.label}>
                      {s.icon}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="hub-welcome">
          <p>{greeting}</p>
        </div>

        <div className="venues-grid">
          {venues.map(venue => (
            <div
              key={venue.id}
              className={`venue-card ${!venue.available ? 'unavailable' : ''}`}
              onClick={() => venue.available && setActiveVenue(venue.id)}
              role="button"
              tabIndex={venue.available ? 0 : -1}
              onKeyDown={(e) => {
                if ((e.key === 'Enter' || e.key === ' ') && venue.available) {
                  e.preventDefault();
                  setActiveVenue(venue.id);
                }
              }}
            >
              <div className="venue-icon">{venue.icon}</div>
              <div className="venue-content">
                <h3 className="venue-name">{venue.name}</h3>
                <p className="venue-description">{venue.description}</p>
                {venue.services && (
                  <div className="venue-services">
                    {venue.services.map((service, idx) => (
                      <span key={idx} className="service-tag">{service}</span>
                    ))}
                  </div>
                )}
              </div>
              <div className="venue-status">
                {venue.available ? (
                  <span className="available-indicator">OPEN</span>
                ) : (
                  <span className="unavailable-indicator">UNAVAILABLE</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  };

  // Render appropriate venue
  const renderActiveVenue = () => {
    switch (activeVenue) {
      case 'hub':
        return renderHub();
      case 'trading':
        return (
          <TradingVenue
            onBack={() => setActiveVenue('hub')}
            blackMarketButton={<BlackMarketButton />}
          />
        );
      case 'shipyard':
        return (
          <ShipyardVenue
            shipId={shipData?.id}
            shipType={shipData?.type}
            tradedockTier={tradedockTier}
            displayCredits={displayCredits}
            refreshPlayerState={refreshPlayerState}
            fetchShipData={fetchShipData}
            shipPurchaseSuccess={shipPurchaseSuccess}
            shipPurchaseError={shipPurchaseError}
            shipCatalogLoading={shipCatalogLoading}
            shipCatalog={shipCatalog}
            shipCatalogError={shipCatalogError}
            fetchShipCatalog={fetchShipCatalog}
            confirmShip={confirmShip}
            setConfirmShip={setConfirmShip}
            newShipName={newShipName}
            setNewShipName={setNewShipName}
            shipPurchasing={shipPurchasing}
            setShipPurchaseError={setShipPurchaseError}
            setShipPurchaseSuccess={setShipPurchaseSuccess}
            purchaseShip={purchaseShip}
            onBack={() => setActiveVenue('hub')}
            onOpenConstruction={() => setActiveVenue('construction')}
          />
        );
      case 'construction':
        // Construction only exists at TradeDock stations — fall back to the
        // hub if the venue is reached without a tiered station docked
        return tradedockTier && currentStation ? (
          <ConstructionVenue
            stationId={currentStation.id}
            stationName={currentStation.name}
            tier={tradedockTier}
            credits={displayCredits}
            onCreditsDelta={handleCreditsDelta}
            onCreditsSet={handleCreditsSet}
            onBack={() => setActiveVenue('hub')}
          />
        ) : renderHub();
      case 'portoffice':
        // The registry desk needs a docked station to file against
        return currentStation ? (
          <PortOfficeVenue
            stationId={currentStation.id}
            stationName={currentStation.name}
            credits={displayCredits}
            onCreditsSet={handleCreditsSet}
            onBack={() => setActiveVenue('hub')}
          />
        ) : renderHub();
      case 'contracts':
        // The board is scoped to whichever station is currently docked
        return currentStation ? (
          <ContractBoardVenue
            stationId={currentStation.id}
            stationName={currentStation.name}
            credits={displayCredits}
            onCreditsSet={handleCreditsSet}
            onBack={() => setActiveVenue('hub')}
          />
        ) : renderHub();
      case 'genesis':
        return (
          <GenesisVenue
            shipName={shipData?.name}
            shipType={shipData?.type}
            currentGenesisDevices={currentGenesisDevices}
            maxGenesisDevices={maxGenesisDevices}
            genesisWeeklyRemaining={genesisWeeklyRemaining}
            genesisWeeklyLimit={genesisWeeklyLimit}
            genesisRepGate={genesisRepGate}
            genesisSuccess={genesisSuccess}
            genesisError={genesisError}
            genesisPurchasing={genesisPurchasing}
            displayCredits={displayCredits}
            genesisDevicePrice={GENESIS_DEVICE_PRICE}
            purchaseGenesisDevice={purchaseGenesisDevice}
            onBack={() => setActiveVenue('hub')}
          />
        );
      case 'armory':
        return (
          <ArmoryVenue
            armoryCatalog={armoryCatalog}
            armoryLoading={armoryLoading}
            armoryCatalogError={armoryCatalogError}
            fetchArmoryCatalog={fetchArmoryCatalog}
            armoryLoadout={armoryLoadout}
            armoryQuantities={armoryQuantities}
            setArmoryQuantities={setArmoryQuantities}
            armoryBuying={armoryBuying}
            armoryError={armoryError}
            armorySuccess={armorySuccess}
            purchaseArmoryItem={purchaseArmoryItem}
            displayCredits={displayCredits}
            stationServices={stationServices}
            stationIsSpacedock={currentStation?.is_spacedock}
            playerAttackDrones={playerState?.attack_drones}
            playerDefenseDrones={playerState?.defense_drones}
            onBack={() => setActiveVenue('hub')}
            blackMarketButton={<BlackMarketButton />}
          />
        );
      case 'services':
        return (
          <ServicesVenue
            shipData={shipData}
            displayCredits={displayCredits}
            stationServices={stationServices}
            repairSuccess={repairSuccess}
            repairError={repairError}
            repairBusy={repairBusy}
            repairShip={repairShip}
            showInsurance={showInsurance}
            setShowInsurance={setShowInsurance}
            showMaintenance={showMaintenance}
            setShowMaintenance={setShowMaintenance}
            showUpgrades={showUpgrades}
            setShowUpgrades={setShowUpgrades}
            insuranceTier={insuranceTier}
            fetchInsuranceStatus={fetchInsuranceStatus}
            refreshPlayerState={refreshPlayerState}
            fetchShipData={fetchShipData}
            onBack={() => setActiveVenue('hub')}
            blackMarketButton={<BlackMarketButton />}
          />
        );
      case 'mining':
        return (
          <MiningVenue
            shipId={shipData?.id}
            licenseBusy={licenseBusy}
            licenseError={licenseError}
            licenseSuccess={licenseSuccess}
            purchaseClaimLicense={purchaseClaimLicense}
            laserBusy={laserBusy}
            laserError={laserError}
            laserSuccess={laserSuccess}
            upgradeMiningLaser={upgradeMiningLaser}
            onBack={() => setActiveVenue('hub')}
            blackMarketButton={<BlackMarketButton />}
          />
        );
      case 'gambling':
        return (
          <GamblingVenue
            onBack={() => setActiveVenue('hub')}
            displayCredits={displayCredits}
            gamblingError={gamblingError}
            currentGame={currentGame}
            setCurrentGame={setCurrentGame}
            betAmount={betAmount}
            setBetAmount={setBetAmount}
            slotReels={slotReels}
            isSpinning={isSpinning}
            isJackpot={isJackpot}
            lastWin={lastWin}
            setLastWin={setLastWin}
            spinSlots={spinSlots}
            diceValues={diceValues}
            diceBetType={diceBetType}
            setDiceBetType={setDiceBetType}
            diceExactBet={diceExactBet}
            setDiceExactBet={setDiceExactBet}
            isSupernova={isSupernova}
            isVoid={isVoid}
            rollDice={rollDice}
            blackjackGame={blackjackGame}
            setBlackjackGame={setBlackjackGame}
            isBlackjackDealing={isBlackjackDealing}
            dealBlackjack={dealBlackjack}
            blackjackAction={blackjackAction}
            lotteryNumbers={lotteryNumbers}
            setLotteryNumbers={setLotteryNumbers}
            winningNumbers={winningNumbers}
            setWinningNumbers={setWinningNumbers}
            lotteryMatches={lotteryMatches}
            setLotteryMatches={setLotteryMatches}
            isLotteryPlaying={isLotteryPlaying}
            toggleLotteryNumber={toggleLotteryNumber}
            playLottery={playLottery}
            blackMarketButton={<BlackMarketButton />}
          />
        );
      default:
        return renderHub();
    }
  };

  return (
    <div className={`spacedock-interface${onUndock ? ' has-station-undock' : ''}`}>
      {/* Persistent UNDOCK (WO-UI3-STATION-MODE, de-duped by WO-UI3-CONCIERGE):
          originally the hub's own `renderHub()` rendered a second
          `.hub-undock-btn` inline in its header — every other venue
          (shipyard/armory/services/mining/gambling/trading/genesis/
          construction/portoffice/contracts) had NO undock at all, so
          STATION-MODE added this single instance in the OUTER FRAME,
          sibling to `renderActiveVenue()`, reachable from every venue.
          renderHub's own copy was removed once this one covered the hub
          view too, so exactly ONE undock button now exists anywhere in
          this component. Reuses `.hub-undock-btn`'s visual treatment;
          `.station-face-undock` (spacedock.css) only adds the fixed corner
          placement. `.has-station-undock` reserves that footprint on the
          hub/venue headers so the hub's "Services" status chip (and any
          venue title) never paints under the button. */}
      {onUndock && (
        <button
          type="button"
          className="hub-undock-btn station-face-undock"
          onClick={onUndock}
          disabled={helmBusy}
          aria-label={helmBusy ? 'Undock unavailable — helm is busy' : 'Undock and launch into space'}
          title="Undock and launch into space"
        >
          {helmBusy ? '🚀 LAUNCHING…' : '🚀 UNDOCK & LAUNCH'}
        </button>
      )}
      {renderActiveVenue()}
      {renderBlackMarketModal()}
    </div>
  );
};

export default SpaceDockInterface;

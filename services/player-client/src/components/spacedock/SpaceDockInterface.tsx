import React, { useState, useCallback } from 'react';
import { useGame } from '../../contexts/GameContext';
import type { Station } from '../../contexts/GameContext';
import TradingInterface from '../trading/TradingInterface';
import ConstructionVenue from './ConstructionVenue';
import PortOfficeVenue from './PortOfficeVenue';
import { InsuranceManager, MaintenanceManager } from '../ships';
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
type VenueType = 'hub' | 'trading' | 'shipyard' | 'construction' | 'portoffice' | 'genesis' | 'armory' | 'services' | 'gambling';
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

// Station class display labels (trading classification 0-11)
const CLASS_LABELS: Record<number, string> = {
  0: 'Sol Hub',
  1: 'Mining Operation',
  2: 'Agricultural Center',
  3: 'Industrial Hub',
  4: 'Distribution Center',
  5: 'Collection Hub',
  6: 'Mixed Market',
  7: 'Resource Exchange',
  8: 'Black Hole Exchange',
  9: 'Nova Market',
  10: 'Luxury Market',
  11: 'Premium Tech Hub'
};

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

// Normalize ship type strings for comparison (e.g. "Cargo Hauler" vs "CARGO_HAULER")
const normalizeShipType = (shipType?: string | null): string =>
  (shipType || '').toUpperCase().replace(/[\s-]+/g, '_');

// Slot machine symbols
const SLOT_SYMBOLS = ['🌍', '⭐', '🚀', '💳', '🕳️', '💎'];
const SLOT_PAYOUTS: Record<string, number> = {
  '💎💎💎': 50,  // Jackpot
  '🚀🚀🚀': 10,  // Ships
  '⭐⭐⭐': 8,   // Stars
  '🌍🌍🌍': 5,   // Planets
  '💳💳💳': 3,   // Credits
};

const SpaceDockInterface: React.FC = () => {
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

  // Error state
  const [gamblingError, setGamblingError] = useState<string | null>(null);
  const [genesisError, setGenesisError] = useState<string | null>(null);
  const [genesisPurchasing, setGenesisPurchasing] = useState(false);
  const [genesisSuccess, setGenesisSuccess] = useState<string | null>(null);

  // Local genesis tracking for immediate UI feedback
  const [localGenesisDevices, setLocalGenesisDevices] = useState<number | null>(null);
  const [localMaxGenesis, setLocalMaxGenesis] = useState<number | null>(null);

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

  // Check if player has negative reputation (access to black market)
  const hasBlackMarketAccess = (playerState?.personal_reputation || 0) < 0;

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
    {
      id: 'genesis',
      name: 'Genesis Store',
      icon: '🌍',
      description: 'Acquire Genesis Devices - the key to creating new worlds',
      available: Boolean(stationServices.genesis_dealer),
      services: ['Standard Devices', 'Advanced Devices', 'Experimental Devices']
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

  const renderCard = (card: BlackjackCard, index: number) => {
    const isRed = card.suit === '♥' || card.suit === '♦';
    if (card.hidden) {
      return (
        <div key={index} className="playing-card hidden">
          <div className="card-back">🂠</div>
        </div>
      );
    }
    return (
      <div key={index} className={`playing-card ${isRed ? 'red' : 'black'}`}>
        <div className="card-corner top">
          <span className="card-rank">{card.rank}</span>
          <span className="card-suit">{card.suit}</span>
        </div>
        <div className="card-center">{card.suit}</div>
        <div className="card-corner bottom">
          <span className="card-rank">{card.rank}</span>
          <span className="card-suit">{card.suit}</span>
        </div>
      </div>
    );
  };

  // Genesis Device Purchase function
  const purchaseGenesisDevice = useCallback(async (tier: 'standard' | 'advanced' | 'experimental') => {
    const token = getToken();
    if (!token || genesisPurchasing) return;

    const prices = { standard: 25000, advanced: 50000, experimental: 100000 };
    const price = prices[tier];

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
        body: JSON.stringify({ tier })
      });

      if (!response.ok) {
        const error = await response.json();
        setGenesisError(error.detail || 'Purchase failed');
        // Restore credits on error
        setLocalCredits(prev => (prev ?? displayCredits) + price);
        setGenesisPurchasing(false);
        return;
      }

      const result = await response.json();
      setLocalCredits(result.new_credits);
      setLocalGenesisDevices(result.genesis_devices);
      setLocalMaxGenesis(result.max_genesis_devices);
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

  // Black Market Modal
  const renderBlackMarketModal = () => {
    if (!showBlackMarket) return null;

    const getBlackMarketItems = () => {
      switch (activeVenue) {
        case 'trading':
          return [
            { name: 'Stolen Cargo Manifest', desc: 'Discounted goods of questionable origin', price: 5000, discount: '40% off market' },
            { name: 'Smuggling Contract', desc: 'High-risk, high-reward delivery job', price: 2000, reward: '15,000 cr on completion' },
            { name: 'Contraband Spices', desc: 'Illegal but highly valuable commodities', price: 10000, sellPrice: '25,000 at black ports' },
          ];
        case 'armory':
          return [
            { name: 'EMP Mine', desc: 'Disables ship systems on impact', price: 8000, stats: 'Disable: 3 turns' },
            { name: 'Cloaking Generator', desc: 'Temporary invisibility field', price: 50000, stats: 'Duration: 5 sectors' },
            { name: 'Illegal Weapon Mod', desc: '+50% damage, voids warranty', price: 15000, stats: '+50% ATK' },
          ];
        case 'services':
          return [
            { name: 'Identity Wipe', desc: 'Clear your criminal record... temporarily', price: 25000, effect: 'Reset bounty' },
            { name: 'Stolen Ship Parts', desc: 'Repair at 30% cost, 20% failure chance', price: 3000, risk: '20% failure' },
            { name: 'Unregistered Mods', desc: 'Performance upgrades with no paper trail', price: 20000, stats: '+10% speed' },
          ];
        default:
          return [];
      }
    };

    const items = getBlackMarketItems();

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

          <div className="bm-items">
            {items.map((item, idx) => (
              <div key={idx} className="bm-item">
                <div className="bm-item-info">
                  <h4>{item.name}</h4>
                  <p>{item.desc}</p>
                  <div className="bm-item-stats">
                    {Object.entries(item).filter(([k]) => !['name', 'desc', 'price'].includes(k)).map(([key, val]) => (
                      <span key={key} className="bm-stat">{key}: {String(val)}</span>
                    ))}
                  </div>
                </div>
                <div className="bm-item-purchase">
                  <span className="bm-price">{item.price.toLocaleString()} cr</span>
                  <button
                    className="bm-buy-btn"
                    disabled={(playerState?.credits || 0) < item.price}
                  >
                    Acquire
                  </button>
                </div>
              </div>
            ))}
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
      : (stationClass != null && CLASS_LABELS[stationClass]) || 'Orbital Trading Station';

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
          <p>Welcome aboard. Choose a destination to access this station&apos;s services.</p>
        </div>

        <div className="venues-grid">
          {venues.map(venue => (
            <div
              key={venue.id}
              className={`venue-card ${!venue.available ? 'unavailable' : ''}`}
              onClick={() => venue.available && setActiveVenue(venue.id)}
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

  const renderGamblingHall = () => (
    <div className="venue-container gambling">
      <div className="venue-header">
        <button className="back-button" onClick={() => {
          if (currentGame === 'menu') {
            setActiveVenue('hub');
          } else {
            setCurrentGame('menu');
            setLastWin(null);
          }
        }}>
          ← {currentGame === 'menu' ? 'Back to Hub' : 'Back to Games'}
        </button>
        <h2>🎰 Gambling Hall</h2>
      </div>

      <div className="venue-content-area gambling-area">
        {currentGame === 'menu' && (
          <div className="gambling-menu">
            <div className="gambling-welcome">
              <div className="neon-sign">FORTUNE FAVORS THE BOLD</div>
              <p>Choose your game and test your luck among the stars...</p>
            </div>

            <div className="games-grid">
              <div className="game-card slots" onClick={() => setCurrentGame('slots')}>
                <div className="game-icon">🎰</div>
                <h3>Cosmic Slots</h3>
                <p>Match symbols to win big! Jackpot pays 50x</p>
                <div className="game-stats">
                  <span>Min Bet: 10 cr</span>
                  <span>Max Win: 50x</span>
                </div>
              </div>

              <div className="game-card dice" onClick={() => setCurrentGame('dice')}>
                <div className="game-icon">🎲</div>
                <h3>Nebula Dice</h3>
                <p>Bet high, low, or exact. Avoid the Void!</p>
                <div className="game-stats">
                  <span>Min Bet: 10 cr</span>
                  <span>Max Win: 35x</span>
                </div>
              </div>

              <div className="game-card blackjack" onClick={() => setCurrentGame('blackjack')}>
                <div className="game-icon">🃏</div>
                <h3>Stellar Blackjack</h3>
                <p>Beat the dealer to 21 without busting!</p>
                <div className="game-stats">
                  <span>Min Bet: 10 cr</span>
                  <span>Blackjack: 3:2</span>
                </div>
              </div>

              <div className="game-card lottery" onClick={() => setCurrentGame('lottery')}>
                <div className="game-icon">🎫</div>
                <h3>Sector Sweep</h3>
                <p>Pick sectors, match the draw, win the jackpot!</p>
                <div className="game-stats">
                  <span>Ticket: 100 cr</span>
                  <span>Jackpot: 1M cr</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {currentGame === 'slots' && (
          <div className="game-view slots-game">
            <div className="slot-machine">
              <div className="slot-header">
                <h3>COSMIC SLOTS</h3>
                <div className="jackpot-display">
                  JACKPOT: <span className="jackpot-amount">💎💎💎 = 50x</span>
                </div>
              </div>

              {gamblingError && (
                <div className="gambling-error">{gamblingError}</div>
              )}

              {isJackpot && lastWin !== null && lastWin > 0 && (
                <div className="jackpot-alert">🎉 JACKPOT! 🎉</div>
              )}

              <div className="slot-reels">
                {slotReels.map((symbol, idx) => (
                  <div key={idx} className={`reel ${isSpinning ? 'spinning' : ''} ${isJackpot ? 'jackpot' : ''}`}>
                    <span className="symbol">{symbol}</span>
                  </div>
                ))}
              </div>

              <div className="slot-result">
                {lastWin !== null && (
                  <div className={`win-display ${lastWin > 0 ? 'winner' : lastWin < 0 ? 'loser' : 'push'}`}>
                    {lastWin > 0 ? `WIN! +${lastWin.toLocaleString()} credits!` :
                     lastWin < 0 ? `Lost ${Math.abs(lastWin).toLocaleString()} credits` :
                     'No match - try again!'}
                  </div>
                )}
              </div>

              <div className="slot-controls">
                <div className="bet-selector">
                  <label>Bet Amount:</label>
                  <div className="bet-buttons">
                    {[10, 50, 100, 500, 1000].map(amount => (
                      <button
                        key={amount}
                        className={`bet-btn ${betAmount === amount ? 'selected' : ''}`}
                        onClick={() => setBetAmount(amount)}
                        disabled={isSpinning}
                      >
                        {amount}
                      </button>
                    ))}
                  </div>
                </div>

                <button
                  className="spin-button"
                  onClick={spinSlots}
                  disabled={isSpinning || displayCredits < betAmount}
                >
                  {isSpinning ? 'SPINNING...' : 'SPIN'}
                </button>
              </div>

              <div className="paytable">
                <h4>Payouts</h4>
                <div className="paytable-grid">
                  <span>💎💎💎 = 50x</span>
                  <span>🚀🚀🚀 = 10x</span>
                  <span>⭐⭐⭐ = 8x</span>
                  <span>🌍🌍🌍 = 5x</span>
                  <span>💳💳💳 = 3x</span>
                  <span>2 Match = 0.5x</span>
                  <span>🕳️ = Lose</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {currentGame === 'dice' && (
          <div className="game-view dice-game">
            <div className="dice-table">
              <div className="dice-header">
                <h3>NEBULA DICE</h3>
                <p className="dice-subtitle">Roll the cosmic dice. Beware the Void (7)!</p>
              </div>

              {gamblingError && (
                <div className="gambling-error">{gamblingError}</div>
              )}

              <div className="dice-display">
                <div className={`die ${diceValues[0] > 0 ? 'rolled' : ''} ${isSupernova ? 'supernova' : ''} ${isVoid ? 'void' : ''}`}>
                  {diceValues[0] > 0 ? diceValues[0] : '?'}
                </div>
                <div className="dice-plus">+</div>
                <div className={`die ${diceValues[1] > 0 ? 'rolled' : ''} ${isSupernova ? 'supernova' : ''} ${isVoid ? 'void' : ''}`}>
                  {diceValues[1] > 0 ? diceValues[1] : '?'}
                </div>
                <div className="dice-equals">=</div>
                <div className={`dice-total ${isVoid ? 'void' : ''}`}>
                  {diceValues[0] + diceValues[1] > 0 ? diceValues[0] + diceValues[1] : '?'}
                </div>
              </div>

              {isSupernova && (
                <div className="supernova-alert">🌟 SUPERNOVA! 🌟</div>
              )}

              {isVoid && (
                <div className="void-alert">🕳️ THE VOID 🕳️</div>
              )}

              <div className="dice-result">
                {lastWin !== null && (
                  <div className={`win-display ${lastWin > 0 ? 'winner' : 'loser'}`}>
                    {lastWin > 0 ? `WIN! +${lastWin.toLocaleString()} credits!` :
                     `Lost ${Math.abs(lastWin).toLocaleString()} credits`}
                  </div>
                )}
              </div>

              <div className="dice-betting">
                <div className="bet-type-selector">
                  <label>Bet Type:</label>
                  <div className="bet-type-buttons">
                    <button
                      className={`type-btn ${diceBetType === 'low' ? 'selected' : ''}`}
                      onClick={() => setDiceBetType('low')}
                    >
                      LOW (2-6) 2x
                    </button>
                    <button
                      className={`type-btn ${diceBetType === 'high' ? 'selected' : ''}`}
                      onClick={() => setDiceBetType('high')}
                    >
                      HIGH (8-12) 2x
                    </button>
                    <button
                      className={`type-btn ${diceBetType === 'exact' ? 'selected' : ''}`}
                      onClick={() => setDiceBetType('exact')}
                    >
                      EXACT (5-35x)
                    </button>
                  </div>
                </div>

                {diceBetType === 'exact' && (
                  <div className="exact-number-selector">
                    <label>Pick your number:</label>
                    <div className="number-buttons">
                      {[2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(num => (
                        <button
                          key={num}
                          className={`num-btn ${diceExactBet === num ? 'selected' : ''} ${num === 7 ? 'void' : ''}`}
                          onClick={() => setDiceExactBet(num)}
                        >
                          {num}
                        </button>
                      ))}
                    </div>
                    <div className="exact-payout">
                      Payout: {diceExactBet === 2 || diceExactBet === 12 ? '35x' :
                               diceExactBet === 3 || diceExactBet === 11 ? '17x' :
                               diceExactBet === 4 || diceExactBet === 10 ? '11x' :
                               diceExactBet === 5 || diceExactBet === 9 ? '8x' :
                               diceExactBet === 6 || diceExactBet === 8 ? '6x' : '5x'}
                    </div>
                  </div>
                )}

                <div className="bet-amount-selector">
                  <label>Bet Amount:</label>
                  <div className="bet-buttons">
                    {[10, 50, 100, 500, 1000].map(amount => (
                      <button
                        key={amount}
                        className={`bet-btn ${betAmount === amount ? 'selected' : ''}`}
                        onClick={() => setBetAmount(amount)}
                      >
                        {amount}
                      </button>
                    ))}
                  </div>
                </div>

                <button
                  className="roll-button"
                  onClick={rollDice}
                  disabled={displayCredits < betAmount}
                >
                  ROLL THE DICE
                </button>
              </div>

              <div className="dice-rules">
                <h4>Rules</h4>
                <ul>
                  <li><strong>7 = The Void</strong> - House wins on any bet</li>
                  <li><strong>Double 6s = Supernova</strong> - Pays 35x regardless of bet type!</li>
                  <li>High/Low bets pay 2x your wager</li>
                </ul>
              </div>
            </div>
          </div>
        )}

        {currentGame === 'blackjack' && (
          <div className="game-view blackjack-game">
            <div className="blackjack-table">
              <div className="blackjack-header">
                <h3>STELLAR BLACKJACK</h3>
                <div className="blackjack-payout-info">
                  <span>Blackjack pays 3:2</span>
                  <span>Dealer stands on 17</span>
                </div>
              </div>

              {gamblingError && (
                <div className="gambling-error">{gamblingError}</div>
              )}

              {!blackjackGame ? (
                <div className="blackjack-start">
                  <div className="blackjack-rules">
                    <h4>How to Play</h4>
                    <ul>
                      <li>Get closer to 21 than the dealer without going over</li>
                      <li>Face cards (J, Q, K) are worth 10</li>
                      <li>Aces are worth 11 or 1</li>
                      <li>Blackjack (Ace + 10-card) pays 3:2</li>
                      <li>Double down doubles your bet and gives one more card</li>
                    </ul>
                  </div>

                  <div className="bet-selector blackjack-bet">
                    <label>Bet Amount:</label>
                    <div className="bet-buttons">
                      {[10, 50, 100, 500, 1000].map(amount => (
                        <button
                          key={amount}
                          className={`bet-btn ${betAmount === amount ? 'selected' : ''}`}
                          onClick={() => setBetAmount(amount)}
                          disabled={isBlackjackDealing}
                        >
                          {amount}
                        </button>
                      ))}
                    </div>
                  </div>

                  <button
                    className="deal-button"
                    onClick={dealBlackjack}
                    disabled={isBlackjackDealing || displayCredits < betAmount}
                  >
                    {isBlackjackDealing ? 'DEALING...' : 'DEAL CARDS'}
                  </button>
                </div>
              ) : (
                <div className="blackjack-game-area">
                  {/* Dealer's Hand */}
                  <div className="hand dealer-hand">
                    <div className="hand-label">
                      Dealer
                      {blackjackGame.gameOver && (
                        <span className="hand-total">({blackjackGame.dealerTotal})</span>
                      )}
                    </div>
                    <div className="cards">
                      {blackjackGame.dealerCards.map((card, idx) => renderCard(card, idx))}
                    </div>
                  </div>

                  {/* Result Display */}
                  {blackjackGame.gameOver && (
                    <div className={`blackjack-result ${blackjackGame.result}`}>
                      {blackjackGame.result === 'blackjack' && '🎰 BLACKJACK! 🎰'}
                      {blackjackGame.result === 'win' && '🎉 YOU WIN! 🎉'}
                      {blackjackGame.result === 'lose' && '😢 Dealer Wins'}
                      {blackjackGame.result === 'push' && '🤝 Push - Tie Game'}
                      {blackjackGame.result === 'bust' && '💥 BUST!'}
                      {lastWin !== null && (
                        <div className="result-amount">
                          {lastWin > 0 ? `+${lastWin.toLocaleString()}` : lastWin.toLocaleString()} credits
                        </div>
                      )}
                    </div>
                  )}

                  {/* Player's Hand */}
                  <div className="hand player-hand">
                    <div className="hand-label">
                      Your Hand
                      <span className="hand-total">({blackjackGame.playerTotal})</span>
                    </div>
                    <div className="cards">
                      {blackjackGame.playerCards.map((card, idx) => renderCard(card, idx))}
                    </div>
                  </div>

                  {/* Action Buttons */}
                  <div className="blackjack-controls">
                    {!blackjackGame.gameOver ? (
                      <>
                        <button
                          className="action-btn hit"
                          onClick={() => blackjackAction('hit')}
                        >
                          HIT
                        </button>
                        <button
                          className="action-btn stand"
                          onClick={() => blackjackAction('stand')}
                        >
                          STAND
                        </button>
                        {blackjackGame.canDouble && displayCredits >= betAmount && (
                          <button
                            className="action-btn double"
                            onClick={() => blackjackAction('double')}
                          >
                            DOUBLE DOWN
                          </button>
                        )}
                      </>
                    ) : (
                      <button
                        className="deal-button new-hand"
                        onClick={() => {
                          setBlackjackGame(null);
                          setLastWin(null);
                        }}
                      >
                        NEW HAND
                      </button>
                    )}
                  </div>

                  <div className="current-bet-display">
                    Current Bet: {betAmount.toLocaleString()} cr
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {currentGame === 'lottery' && (
          <div className="game-view lottery-game">
            <div className="lottery-booth">
              <div className="lottery-header">
                <h3>SECTOR SWEEP</h3>
                <div className="jackpot-banner">
                  <span className="jp-label">JACKPOT</span>
                  <span className="jp-amount">1000x BET</span>
                </div>
              </div>

              <div className="lottery-info">
                <p>Pick 4 sectors from the grid below. Match to win!</p>
                <div className="prize-table">
                  <span>1 Match: 1x</span>
                  <span>2 Match: 5x</span>
                  <span>3 Match: 50x</span>
                  <span>4 Match: 1000x!</span>
                </div>
              </div>

              {gamblingError && (
                <div className="gambling-error">{gamblingError}</div>
              )}

              <div className="lottery-selections">
                <p>Your Selections ({lotteryNumbers.length}/4):</p>
                <div className="selected-numbers">
                  {lotteryNumbers.length > 0 ? (
                    lotteryNumbers.map(n => (
                      <span key={n} className="selected-num">{n}</span>
                    ))
                  ) : (
                    <span className="no-selection">Pick 4 sectors below</span>
                  )}
                </div>
              </div>

              <div className="sector-grid">
                {Array.from({ length: 12 }, (_, i) => (
                  <button
                    key={i + 1}
                    className={`sector-pick ${lotteryNumbers.includes(i + 1) ? 'selected' : ''} ${winningNumbers.includes(i + 1) ? 'winning' : ''}`}
                    onClick={() => toggleLotteryNumber(i + 1)}
                    disabled={isLotteryPlaying}
                  >
                    {i + 1}
                  </button>
                ))}
              </div>

              {winningNumbers.length > 0 && (
                <div className="lottery-results">
                  <div className="winning-numbers-display">
                    <p>Winning Sectors:</p>
                    <div className="winning-nums">
                      {winningNumbers.map(n => (
                        <span
                          key={n}
                          className={`winning-num ${lotteryNumbers.includes(n) ? 'matched' : ''}`}
                        >
                          {n}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className={`lottery-result-text ${lotteryMatches && lotteryMatches > 0 ? 'winner' : 'loser'}`}>
                    {isJackpot ? (
                      <div className="jackpot-win">🎉 JACKPOT! 🎉</div>
                    ) : lotteryMatches && lotteryMatches > 0 ? (
                      `${lotteryMatches} Match${lotteryMatches > 1 ? 'es' : ''}! +${lastWin?.toLocaleString()} credits!`
                    ) : (
                      `No matches. Lost ${betAmount.toLocaleString()} credits`
                    )}
                  </div>
                </div>
              )}

              <div className="lottery-controls">
                <div className="bet-selector lottery-bet">
                  <label>Ticket Price:</label>
                  <div className="bet-buttons">
                    {[100, 250, 500, 1000, 2500].map(amount => (
                      <button
                        key={amount}
                        className={`bet-btn ${betAmount === amount ? 'selected' : ''}`}
                        onClick={() => setBetAmount(amount)}
                        disabled={isLotteryPlaying}
                      >
                        {amount}
                      </button>
                    ))}
                  </div>
                </div>
                <button
                  className="buy-ticket-btn"
                  onClick={playLottery}
                  disabled={displayCredits < betAmount || lotteryNumbers.length !== 4 || isLotteryPlaying}
                >
                  {isLotteryPlaying ? 'Drawing...' : 'Buy Ticket & Draw'}
                </button>
              </div>

              <button
                className="clear-selection-btn"
                onClick={() => {
                  setLotteryNumbers([]);
                  setWinningNumbers([]);
                  setLotteryMatches(null);
                  setLastWin(null);
                }}
                disabled={isLotteryPlaying}
              >
                Clear Selection
              </button>
            </div>
          </div>
        )}
      </div>
      <BlackMarketButton />
    </div>
  );

  const renderShipyard = () => {
    const currentShipType = normalizeShipType(shipData?.type);

    return (
      <div className="venue-container shipyard">
        <div className="venue-header">
          <button className="back-button" onClick={() => setActiveVenue('hub')}>
            ← Back to Hub
          </button>
          <h2>🛠️ Shipyard</h2>
        </div>
        <div className="venue-content-area">
          <div className="shipyard-sections">
            <div className="shipyard-section">
              <h3>🏗️ Construction Slips</h3>
              {tradedockTier ? (
                <>
                  <p className="section-description">
                    This Tier-{tradedockTier} TradeDock runs full construction slips. Ship orders and build tracking live in the Construction venue.
                  </p>
                  <button className="action-button" onClick={() => setActiveVenue('construction')}>
                    Open Construction Venue
                  </button>
                </>
              ) : (
                <>
                  <p className="section-description">
                    Slip construction — coming soon. Custom ship building is not yet available at this facility.
                  </p>
                  <button className="action-button" disabled>Reserve Dock Slip</button>
                </>
              )}
            </div>

            <div className="shipyard-section">
              <h3>🚀 Ship Catalog</h3>
              <p className="section-description">Browse and purchase pre-fabricated vessels</p>

              {shipPurchaseSuccess && (
                <div className="genesis-success-message">
                  <span className="success-icon">✅</span>
                  {shipPurchaseSuccess}
                </div>
              )}
              {shipPurchaseError && !confirmShip && (
                <div className="genesis-error-message">
                  <span className="error-icon">❌</span>
                  {shipPurchaseError}
                </div>
              )}

              {shipCatalogLoading && !shipCatalog && (
                <div className="catalog-loading">Accessing shipyard registry...</div>
              )}
              {shipCatalogError && !shipCatalogLoading && (
                <div className="genesis-error-message">
                  <span className="error-icon">❌</span>
                  {shipCatalogError}
                  <button className="action-button" onClick={fetchShipCatalog}>Retry</button>
                </div>
              )}
              {!shipCatalogError && shipCatalog && (
                <div className="ship-catalog">
                  {shipCatalog.map(ship => {
                    const isCurrent = currentShipType !== '' && normalizeShipType(ship.type) === currentShipType;
                    return (
                      <div
                        key={ship.type}
                        className={`ship-card${!ship.purchasable ? ' unavailable' : ''}${isCurrent ? ' current-ship' : ''}`}
                      >
                        <div className="ship-info">
                          <span className="ship-name">
                            {ship.name}
                            {isCurrent && <span className="current-ship-badge">YOUR SHIP</span>}
                          </span>
                          <div className="ship-stats">
                            <span title="Cargo holds">📦 {ship.max_cargo}</span>
                            <span title="Speed">⚡ {ship.speed}</span>
                            <span title="Drone capacity">🤖 {ship.max_drones}</span>
                            <span title="Shield capacity">🛡️ {ship.max_shields}</span>
                            <span title="Hull points">🔩 {ship.hull_points}</span>
                          </div>
                        </div>
                        {ship.purchasable ? (
                          <>
                            <div className="ship-price">{ship.base_cost.toLocaleString()} cr</div>
                            <button
                              className="buy-ship-btn"
                              onClick={() => {
                                setConfirmShip(ship);
                                setNewShipName('');
                                setShipPurchaseError(null);
                                setShipPurchaseSuccess(null);
                              }}
                              disabled={shipPurchasing || displayCredits < ship.base_cost}
                              title={displayCredits < ship.base_cost ? 'Insufficient credits' : undefined}
                            >
                              Purchase
                            </button>
                          </>
                        ) : (
                          <div className="ship-unavailable-reason">
                            {ship.reason || 'Not available for purchase'}
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {shipCatalog.length === 0 && (
                    <p className="section-description">No vessels currently listed at this shipyard.</p>
                  )}
                </div>
              )}
            </div>
          </div>

          {confirmShip && (
            <div
              className="ship-confirm-overlay"
              onClick={() => !shipPurchasing && setConfirmShip(null)}
            >
              <div className="ship-confirm-panel" onClick={e => e.stopPropagation()}>
                <h3>Confirm Purchase — {confirmShip.name}</h3>
                {confirmShip.description && (
                  <p className="section-description">{confirmShip.description}</p>
                )}
                <label className="ship-name-label">
                  Ship name (optional)
                  <input
                    type="text"
                    value={newShipName}
                    onChange={e => setNewShipName(e.target.value)}
                    placeholder={confirmShip.name}
                    maxLength={50}
                    disabled={shipPurchasing}
                  />
                </label>
                <div className="confirm-cost-rows">
                  <div className="confirm-cost-row">
                    <span>Cost</span>
                    <span>{confirmShip.base_cost.toLocaleString()} cr</span>
                  </div>
                  <div className="confirm-cost-row">
                    <span>Your credits</span>
                    <span>{displayCredits.toLocaleString()} cr</span>
                  </div>
                  <div className={`confirm-cost-row balance${displayCredits - confirmShip.base_cost < 0 ? ' negative' : ''}`}>
                    <span>After purchase</span>
                    <span>{(displayCredits - confirmShip.base_cost).toLocaleString()} cr</span>
                  </div>
                </div>
                {shipPurchaseError && (
                  <div className="genesis-error-message">
                    <span className="error-icon">❌</span>
                    {shipPurchaseError}
                  </div>
                )}
                <div className="confirm-actions">
                  <button
                    className="action-button"
                    onClick={() => setConfirmShip(null)}
                    disabled={shipPurchasing}
                  >
                    Cancel
                  </button>
                  <button
                    className="action-button primary"
                    onClick={() => purchaseShip(confirmShip, newShipName)}
                    disabled={shipPurchasing || displayCredits < confirmShip.base_cost}
                  >
                    {shipPurchasing ? 'Processing...' : 'Confirm Purchase'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderGenesisStore = () => {
    const canHoldGenesis = maxGenesisDevices > 0;
    const hasCapacity = currentGenesisDevices < maxGenesisDevices;

    return (
      <div className="venue-container genesis">
        <div className="venue-header">
          <button className="back-button" onClick={() => setActiveVenue('hub')}>
            ← Back to Hub
          </button>
          <h2>🌍 Genesis Store</h2>
        </div>
        <div className="venue-content-area">
          <div className="genesis-intro">
            <div className="genesis-banner">
              <div className="banner-icon">🌍</div>
              <div className="banner-text">
                <h3>Create New Worlds</h3>
                <p>Genesis Devices are advanced terraforming technology that allow you to create new planets in empty sectors.</p>
              </div>
            </div>
          </div>

          {/* Ship Genesis Capacity Display */}
          <div className={`genesis-ship-status ${canHoldGenesis ? 'capable' : 'incapable'}`}>
            <div className="ship-genesis-header">
              <span className="ship-icon">🚀</span>
              <div className="ship-genesis-info">
                <h4>Your Ship: {shipData?.name || 'Unknown'}</h4>
                <span className="ship-type">{shipData?.type || 'Unknown Type'}</span>
              </div>
            </div>
            {canHoldGenesis ? (
              <div className="genesis-capacity">
                <div className="capacity-display">
                  <div className="genesis-orbs">
                    {Array.from({ length: maxGenesisDevices }, (_, i) => (
                      <div
                        key={i}
                        className={`genesis-orb ${i < currentGenesisDevices ? 'filled' : 'empty'}`}
                        title={i < currentGenesisDevices ? 'Genesis Device Loaded' : 'Empty Slot'}
                      >
                        {i < currentGenesisDevices ? '🌍' : '⭕'}
                      </div>
                    ))}
                  </div>
                  <div className="capacity-text">
                    <span className="count">{currentGenesisDevices} / {maxGenesisDevices}</span>
                    <span className="label">Genesis Devices</span>
                  </div>
                </div>
                {currentGenesisDevices > 0 && (
                  <div className="genesis-power-indicator">
                    <span className="power-glow">✨</span>
                    <span className="power-text">World-Creating Power Ready</span>
                    <span className="power-glow">✨</span>
                  </div>
                )}
              </div>
            ) : (
              <div className="genesis-incapable-warning">
                <span className="warning-icon">⚠️</span>
                <span>This ship cannot carry Genesis Devices. You need a Cargo Hauler, Defender, Colony Ship, Carrier, or Warp Jumper.</span>
              </div>
            )}
          </div>

          {/* Success/Error Messages */}
          {genesisSuccess && (
            <div className="genesis-success-message">
              <span className="success-icon">✅</span>
              {genesisSuccess}
            </div>
          )}
          {genesisError && (
            <div className="genesis-error-message">
              <span className="error-icon">❌</span>
              {genesisError}
            </div>
          )}

          <div className="genesis-devices-grid">
            <div className="genesis-device-card standard">
              <div className="device-header">
                <span className="device-tier">Standard</span>
                <div className="device-icon">🌑</div>
              </div>
              <div className="device-details">
                <h3>Standard Genesis Device</h3>
                <ul className="device-specs">
                  <li>🎯 85% Success Rate</li>
                  <li>⏱️ 48 Hour Process</li>
                  <li>🪐 Basic Planet Types</li>
                  <li>📊 Higher chance of barren worlds</li>
                </ul>
              </div>
              <div className="device-footer">
                <div className="device-price">25,000 cr</div>
                <button
                  className="purchase-device-btn"
                  onClick={() => purchaseGenesisDevice('standard')}
                  disabled={genesisPurchasing || displayCredits < 25000 || !canHoldGenesis || !hasCapacity}
                >
                  {genesisPurchasing ? 'Purchasing...' : !canHoldGenesis ? 'Ship Incompatible' : !hasCapacity ? 'At Capacity' : 'Purchase'}
                </button>
              </div>
            </div>

            <div className="genesis-device-card advanced">
              <div className="device-header">
                <span className="device-tier">Advanced</span>
                <div className="device-icon">🌎</div>
              </div>
              <div className="device-details">
                <h3>Advanced Genesis Device</h3>
                <ul className="device-specs">
                  <li>🎯 92% Success Rate</li>
                  <li>⏱️ 36 Hour Process</li>
                  <li>🪐 Improved Planet Types</li>
                  <li>📊 Better resource distribution</li>
                </ul>
              </div>
              <div className="device-footer">
                <div className="device-price">50,000 cr</div>
                <button
                  className="purchase-device-btn"
                  onClick={() => purchaseGenesisDevice('advanced')}
                  disabled={genesisPurchasing || displayCredits < 50000 || !canHoldGenesis || !hasCapacity}
                >
                  {genesisPurchasing ? 'Purchasing...' : !canHoldGenesis ? 'Ship Incompatible' : !hasCapacity ? 'At Capacity' : 'Purchase'}
                </button>
              </div>
            </div>

            <div className="genesis-device-card experimental">
              <div className="device-header">
                <span className="device-tier">Experimental</span>
                <div className="device-icon">🌏</div>
              </div>
              <div className="device-details">
                <h3>Experimental Genesis Device</h3>
                <ul className="device-specs">
                  <li>🎯 95% Success Rate</li>
                  <li>⏱️ 24 Hour Process</li>
                  <li>🪐 Premium Planet Types</li>
                  <li>📊 Chance for unique features</li>
                </ul>
              </div>
              <div className="device-footer">
                <div className="device-price">100,000 cr</div>
                <button
                  className="purchase-device-btn"
                  onClick={() => purchaseGenesisDevice('experimental')}
                  disabled={genesisPurchasing || displayCredits < 100000 || !canHoldGenesis || !hasCapacity}
                >
                  {genesisPurchasing ? 'Purchasing...' : !canHoldGenesis ? 'Ship Incompatible' : !hasCapacity ? 'At Capacity' : 'Purchase'}
                </button>
              </div>
            </div>
          </div>

          <div className="genesis-info">
            <h4>📋 Requirements</h4>
            <ul>
              <li>Ship with Genesis Device capacity (Cargo Hauler, Colony Ship, Carrier, Defender, or Warp Jumper)</li>
              <li>Empty, non-protected sector for deployment</li>
              <li>Minimum Federation reputation level</li>
            </ul>
          </div>
        </div>
      </div>
    );
  };

  const renderArmoryItemCard = (item: ArmoryCatalogItem) => {
    const qty = armoryQuantities[item.item] ?? 1;
    const totalCost = item.price * qty;
    const loadoutKey = loadoutKeyForItem(item.item);
    // Gate on the station's services map via the item's service key —
    // the catalog doesn't send an 'available' flag
    const gated = item.available === false ||
      (item.service ? !stationServices[item.service] && !currentStation?.is_spacedock : false);

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
          <span className="eq-price">{item.price.toLocaleString()} cr</span>
          <div className="qty-controls">
            <input
              type="number"
              min={1}
              max={100}
              value={qty}
              onChange={e => {
                const next = Math.max(1, Math.min(100, parseInt(e.target.value, 10) || 1));
                setArmoryQuantities(prev => ({ ...prev, [item.item]: next }));
              }}
              disabled={gated || Boolean(armoryBuying)}
              aria-label={`${item.name} quantity`}
            />
            <button
              className="buy-btn"
              onClick={() => purchaseArmoryItem(item, qty)}
              disabled={Boolean(armoryBuying) || Boolean(blockReason)}
              title={blockReason ?? undefined}
            >
              {isBuying ? '...' : 'Buy'}
            </button>
          </div>
          {qty > 1 && !gated && (
            <span className="eq-total">Total: {totalCost.toLocaleString()} cr</span>
          )}
        </div>
      </div>
    );
  };

  const renderArmory = () => {
    const items = armoryCatalog ?? [];
    const droneItems = items.filter(i => i.item.includes('drone'));
    const mineItems = items.filter(i => !i.item.includes('drone') && i.item.includes('mine'));
    const otherItems = items.filter(i => !i.item.includes('drone') && !i.item.includes('mine'));

    return (
      <div className="venue-container armory">
        <div className="venue-header">
          <button className="back-button" onClick={() => setActiveVenue('hub')}>
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

          <div className="current-loadout">
            <h4>📊 Current Ship Loadout</h4>
            <div className="loadout-stats">
              <div className="loadout-item">
                <span className="item-label">Attack Drones</span>
                <span className="item-value">
                  {armoryLoadout
                    ? `${armoryLoadout.attack_drones} / ${armoryLoadout.caps.attack_drones}`
                    : (playerState?.attack_drones ?? 0)}
                </span>
              </div>
              <div className="loadout-item">
                <span className="item-label">Defense Drones</span>
                <span className="item-value">
                  {armoryLoadout
                    ? `${armoryLoadout.defense_drones} / ${armoryLoadout.caps.defense_drones}`
                    : (playerState?.defense_drones ?? 0)}
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
        </div>
        <BlackMarketButton />
      </div>
    );
  };

  const renderServices = () => {
    // Read real hull/shield condition off the current ship. The combat dict
    // mirrors the server's ShipResponse; values are plain numbers there.
    const combat = shipData?.combat ?? null;
    const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
    const hull = num(combat?.hull);
    const maxHull = num(combat?.max_hull);
    const shields = num(combat?.shields);
    const maxShields = num(combat?.max_shields);

    const hullPct = hull !== null && maxHull ? Math.max(0, Math.min(100, (hull / maxHull) * 100)) : null;
    const shieldPct = shields !== null && maxShields ? Math.max(0, Math.min(100, (shields / maxShields) * 100)) : null;

    // Mirror the server's canon pricing (player.py repair endpoint):
    // Basic repair = 5% of ship value per +10% combined hull+shield rating
    const totalMax = (maxHull ?? 0) + (maxShields ?? 0);
    const deficit = ((maxHull ?? 0) - (hull ?? 0)) + ((maxShields ?? 0) - (shields ?? 0));
    const deficitPct = totalMax > 0 ? Math.max(0, (deficit / totalMax) * 100) : 0;
    const repairCost = totalMax > 0
      ? Math.round((shipData?.current_value ?? 0) * 0.05 * (deficitPct / 10))
      : null;
    const atFullCondition = totalMax > 0 && deficitPct <= 0;

    // Cargo: "used" field when present, else sum commodity values while
    // excluding metadata keys (same convention as ShipSelector)
    const cargo = shipData?.cargo ?? {};
    const metadataKeys = ['capacity', 'used', 'contents'];
    const cargoUsed = typeof cargo.used === 'number'
      ? cargo.used
      : Object.entries(cargo)
          .filter(([key, val]) => !metadataKeys.includes(key) && typeof val === 'number')
          .reduce((sum, [, val]) => sum + val, 0);
    const cargoCapacity = shipData?.cargo_capacity ?? 0;
    const cargoPct = cargoCapacity > 0 ? Math.max(0, Math.min(100, (cargoUsed / cargoCapacity) * 100)) : 0;

    // The repair endpoint requires the docked station to offer ship_repair
    const repairOffered = Boolean(stationServices.ship_repair);

    let repairBlockReason: string | null = null;
    if (!repairOffered) {
      repairBlockReason = 'This station does not offer hull repair';
    } else if (!shipData) {
      repairBlockReason = 'Reading ship telemetry...';
    } else if (totalMax <= 0) {
      // Escape pods / malformed combat dicts have no repairable systems;
      // without this branch the button enables with a "—" cost and the
      // click can only ever earn the server's 400.
      repairBlockReason = 'Ship has no repairable systems';
    } else if (atFullCondition) {
      repairBlockReason = 'Ship is at full condition';
    } else if (repairCost !== null && displayCredits < repairCost) {
      repairBlockReason = 'Insufficient credits';
    }

    return (
      <div className="venue-container services">
        <div className="venue-header">
          <button className="back-button" onClick={() => setActiveVenue('hub')}>
            ← Back to Hub
          </button>
          <h2>🔧 Ship Services</h2>
        </div>
        <div className="venue-content-area">
          {repairSuccess && (
            <div className="genesis-success-message">
              <span className="success-icon">✅</span>
              {repairSuccess}
            </div>
          )}
          {repairError && (
            <div className="genesis-error-message">
              <span className="error-icon">❌</span>
              {repairError}
            </div>
          )}

          <div className="services-grid">
            <div className="service-card">
              <div className="service-icon">🔧</div>
              <h3>Ship Repair</h3>
              <p>{shipData ? `Restore ${shipData.name}'s hull and shield integrity` : 'Restore hull and shield integrity'}</p>
              <div className="service-status">
                <div className="status-bar">
                  <span className="bar-label">Hull</span>
                  <div className="bar-track">
                    <div className="bar-fill" style={{ width: `${hullPct ?? 0}%` }}></div>
                  </div>
                  <span className="bar-value">{hullPct !== null ? `${Math.round(hullPct)}%` : '—'}</span>
                </div>
                <div className="status-bar">
                  <span className="bar-label">Shields</span>
                  <div className="bar-track">
                    <div className="bar-fill shield" style={{ width: `${shieldPct ?? 0}%` }}></div>
                  </div>
                  <span className="bar-value">{shieldPct !== null ? `${Math.round(shieldPct)}%` : '—'}</span>
                </div>
              </div>
              <div className="service-action">
                <span className="repair-cost">
                  {repairCost === null
                    ? '—'
                    : atFullCondition
                      ? 'No repairs needed'
                      : `${repairCost.toLocaleString()} cr`}
                </span>
                <button
                  className="service-btn"
                  onClick={repairShip}
                  disabled={repairBusy || Boolean(repairBlockReason)}
                  title={repairBlockReason ?? undefined}
                >
                  {repairBusy ? 'Repairing...' : 'Full Repair'}
                </button>
              </div>
            </div>

            <div className="service-card">
              <div className="service-icon">🛠️</div>
              <h3>Maintenance</h3>
              <p>{shipData ? `${shipData.name}'s hull condition & servicing` : 'Hull condition & servicing'}</p>
              <div className="service-status">
                Ships degrade over time; low condition saps combat effectiveness. Service to restore it.
              </div>
              <div className="service-action">
                <button className="service-btn" onClick={() => setShowMaintenance(true)} disabled={!shipData}>
                  Manage Maintenance
                </button>
              </div>
            </div>

            <div className="service-card">
              <div className="service-icon">📦</div>
              <h3>Cargo Hold</h3>
              <p>Current hold loading for {shipData?.name ?? 'your ship'}</p>
              <div className="service-status">
                <div className="status-bar">
                  <span className="bar-label">Cargo</span>
                  <div className="bar-track">
                    <div className="bar-fill" style={{ width: `${cargoPct}%` }}></div>
                  </div>
                  <span className="bar-value">{cargoCapacity > 0 ? `${Math.round(cargoPct)}%` : '—'}</span>
                </div>
              </div>
              <div className="cargo-info">
                <span>{cargoUsed.toLocaleString()} / {cargoCapacity.toLocaleString()} units</span>
              </div>
            </div>

            <div className="service-card unavailable">
              <div className="service-icon">📈</div>
              <h3>Ship Upgrades</h3>
              <p>Hull, shield, and cargo refits</p>
              <div className="service-unavailable-note">
                Upgrade bays are not yet operational at this station. New hulls
                can be commissioned at the Shipyard.
              </div>
              <div className="service-action">
                <span className="service-unavailable-badge">NOT AVAILABLE</span>
              </div>
            </div>

            {stationServices.insurance ? (
              <div className="service-card">
                <div className="service-icon">📜</div>
                <h3>Hull Insurance</h3>
                <p>{shipData ? `Insure ${shipData.name} against destruction` : 'Insure your ship against destruction'}</p>
                <div className="service-status">
                  Pay a one-time premium; the registered owner is paid out if the hull is destroyed.
                </div>
                <div className="service-action">
                  <button
                    className="service-btn"
                    onClick={() => setShowInsurance(true)}
                    disabled={!shipData}
                  >
                    Manage Insurance
                  </button>
                </div>
              </div>
            ) : (
              <div className="service-card unavailable">
                <div className="service-icon">📜</div>
                <h3>Hull Insurance</h3>
                <p>Protection against ship destruction</p>
                <div className="service-unavailable-note">
                  No underwriter currently operates at this station.
                </div>
                <div className="service-action">
                  <span className="service-unavailable-badge">NOT AVAILABLE</span>
                </div>
              </div>
            )}
          </div>

          {showInsurance && shipData && (
            <div className="insurance-overlay" onClick={() => setShowInsurance(false)}>
              <div className="insurance-overlay-panel" onClick={(e) => e.stopPropagation()}>
                <InsuranceManager
                  shipId={shipData.id}
                  playerCredits={displayCredits}
                  onChanged={() => { refreshPlayerState(); fetchShipData(); }}
                  onClose={() => setShowInsurance(false)}
                />
              </div>
            </div>
          )}

          {showMaintenance && shipData && (
            <div className="maintenance-overlay" onClick={() => setShowMaintenance(false)}>
              <div className="maintenance-overlay-panel" onClick={(e) => e.stopPropagation()}>
                <MaintenanceManager
                  shipId={shipData.id}
                  playerCredits={displayCredits}
                  onChanged={() => { refreshPlayerState(); fetchShipData(); }}
                  onClose={() => setShowMaintenance(false)}
                />
              </div>
            </div>
          )}
        </div>
        <BlackMarketButton />
      </div>
    );
  };

  const renderTrading = () => (
    <div className="venue-container trading">
      <div className="venue-header">
        <button className="back-button" onClick={() => setActiveVenue('hub')}>
          ← Back to Hub
        </button>
        <h2>🏪 Trading Hub</h2>
      </div>
      <div className="venue-content-area trading-venue">
        <TradingInterface onClose={() => {}} />
      </div>
      <BlackMarketButton />
    </div>
  );

  // Render appropriate venue
  const renderActiveVenue = () => {
    switch (activeVenue) {
      case 'hub':
        return renderHub();
      case 'trading':
        return renderTrading();
      case 'shipyard':
        return renderShipyard();
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
      case 'genesis':
        return renderGenesisStore();
      case 'armory':
        return renderArmory();
      case 'services':
        return renderServices();
      case 'gambling':
        return renderGamblingHall();
      default:
        return renderHub();
    }
  };

  return (
    <div className="spacedock-interface">
      {renderActiveVenue()}
      {renderBlackMarketModal()}
    </div>
  );
};

export default SpaceDockInterface;

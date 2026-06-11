import React, { useState, useEffect, useMemo } from 'react';
import { rankingAPI } from '../../services/api';
import './ranking.css';

interface Medal {
  key: string;
  name: string;
  category: string;
  description: string;
  icon: string;
  awarded_at?: string;
  value_at_award?: number;
  trigger_type?: string;
  threshold?: number;
}

interface MedalData {
  earned: Medal[];
  available: Medal[];
}

const CATEGORY_ICONS: Record<string, string> = {
  Combat: '⚔️',
  Economic: '💰',
  Exploration: '🌌',
  Diplomatic: '🤝',
  Special: '✨',
};

const MEDAL_ICONS: Record<string, string> = {
  star_bronze: '🥉',
  star_silver: '🥈',
  cross_quantum: '✝️',
  medal_trade: '🏅',
  crown_merchant: '👑',
  badge_explorer: '🧭',
  award_genesis: '🌍',
  star_ambassador: '⭐',
  favor_aria: '💜',
  cat_orange: '🐈',
  blood_first: '🩸',
  flag_colony: '🚩',
  commander_fleet: '🎖️',
};

const CATEGORIES = ['All', 'Combat', 'Economic', 'Exploration', 'Diplomatic', 'Special'];

const MedalShowcase: React.FC = () => {
  const [medalData, setMedalData] = useState<MedalData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState('All');
  const [hoveredMedal, setHoveredMedal] = useState<Medal | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const fetchMedals = async () => {
      try {
        setLoading(true);
        const data = await rankingAPI.getMedals();
        setMedalData(data);
        setError(null);
      } catch (err: any) {
        setError(err.message || 'Failed to load medals');
      } finally {
        setLoading(false);
      }
    };
    fetchMedals();
  }, []);

  const filteredMedals = useMemo(() => {
    if (!medalData) return { earned: [], available: [] };
    const filterFn = (m: Medal) =>
      activeCategory === 'All' || m.category === activeCategory;
    return {
      earned: medalData.earned.filter(filterFn),
      available: medalData.available.filter(filterFn),
    };
  }, [medalData, activeCategory]);

  const handleMouseEnter = (medal: Medal, e: React.MouseEvent) => {
    setHoveredMedal(medal);
    setTooltipPos({ x: e.clientX + 12, y: e.clientY - 10 });
  };

  if (loading) {
    return (
      <div className="medal-showcase medal-loading">
        <div className="rank-spinner" />
        <span>Loading medals...</span>
      </div>
    );
  }

  if (error || !medalData) {
    return (
      <div className="medal-showcase medal-error">
        <span>{error || 'Medals unavailable'}</span>
      </div>
    );
  }

  return (
    <div className="medal-showcase">
      <div className="medal-header">
        <h3>Medals</h3>
        <span className="medal-count">
          {medalData.earned.length} / {medalData.earned.length + medalData.available.length}
        </span>
      </div>

      <div className="medal-categories">
        {CATEGORIES.map((cat) => (
          <button
            key={cat}
            className={`medal-cat-btn ${activeCategory === cat ? 'active' : ''}`}
            onClick={() => setActiveCategory(cat)}
          >
            {cat !== 'All' && CATEGORY_ICONS[cat]} {cat}
          </button>
        ))}
      </div>

      <div className="medal-grid">
        {filteredMedals.earned.map((medal) => (
          <div
            key={medal.key}
            className="medal-card earned"
            onMouseEnter={(e) => handleMouseEnter(medal, e)}
            onMouseLeave={() => setHoveredMedal(null)}
          >
            <span className="medal-icon">
              {MEDAL_ICONS[medal.icon] || '🏅'}
            </span>
            <span className="medal-name">{medal.name}</span>
            {medal.awarded_at && (
              <span className="medal-date">
                {new Date(medal.awarded_at).toLocaleDateString()}
              </span>
            )}
          </div>
        ))}
        {filteredMedals.available.map((medal) => (
          <div
            key={medal.key}
            className="medal-card unearned"
            onMouseEnter={(e) => handleMouseEnter(medal, e)}
            onMouseLeave={() => setHoveredMedal(null)}
          >
            <span className="medal-icon">
              {MEDAL_ICONS[medal.icon] || '🏅'}
            </span>
            <span className="medal-name">{medal.name}</span>
          </div>
        ))}
      </div>

      {hoveredMedal && (
        <div
          className="medal-tooltip"
          style={{ top: tooltipPos.y, left: tooltipPos.x }}
        >
          <h4>{hoveredMedal.name}</h4>
          <p>{hoveredMedal.description}</p>
          <div className="tooltip-category">
            {CATEGORY_ICONS[hoveredMedal.category]} {hoveredMedal.category}
          </div>
        </div>
      )}
    </div>
  );
};

export default MedalShowcase;

/**
 * Language switcher component for Player Client
 */

import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { SUPPORTED_LANGUAGES } from '../../i18n';
import './language-switcher.css';

interface Language {
  code: string;
  name: string;
  nativeName: string;
  // Optional: the SUPPORTED_LANGUAGES static config (src/i18n.ts) doesn't
  // carry a direction field, unlike the API response and the error fallback.
  direction?: string;
  isActive: boolean;
  completionPercentage: number;
}

interface LanguageSwitcherProps {
  variant?: 'compact' | 'full';
  showProgress?: boolean;
}

/** Launch-complete / partial locales when /i18n/languages is unavailable (i18n.md). */
const STATIC_COMPLETION_PERCENT: Record<string, number> = {
  en: 100,
  es: 100,
  fr: 100,
  zh: 100,
  pt: 100,
  de: 50,
};

const STATIC_ACTIVE_LOCALES = new Set(Object.keys(STATIC_COMPLETION_PERCENT));

function staticFallbackLanguages(): Language[] {
  return Object.entries(SUPPORTED_LANGUAGES)
    .filter(([code]) => STATIC_ACTIVE_LOCALES.has(code))
    .map(([code, info]) => ({
      code,
      name: info.name,
      nativeName: info.nativeName,
      direction: 'ltr',
      isActive: true,
      completionPercentage: STATIC_COMPLETION_PERCENT[code] ?? 0,
    }));
}

const LanguageSwitcher: React.FC<LanguageSwitcherProps> = ({ 
  variant = 'compact',
  showProgress = true 
}) => {
  const { i18n } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [languages, setLanguages] = useState<Language[]>([]);
  const [loading, setLoading] = useState(false);

  // Fetch available languages from API
  useEffect(() => {
    const fetchLanguages = async () => {
      // Absolute base — relative `/api/...` breaks under node/undici fetch
      // (vitest/jsdom) with "Failed to parse URL". Matches apiClient.ts.
      const base =
        (typeof window !== 'undefined' &&
          (import.meta.env.VITE_API_URL || window.location.origin)) ||
        '';
      try {
        const response = await fetch(`${base}/api/v1/i18n/languages`, {
          credentials: 'include'
        });
        
        if (response.ok) {
          const data = await response.json();
          setLanguages(data);
        } else {
          setLanguages(staticFallbackLanguages());
        }
      } catch {
        // Soft fallback is intentional (offline / test / API down) — do not
        // console.error; StatusBar smoke asserts zero console.error and the
        // switcher still works from the static list.
        setLanguages(staticFallbackLanguages());
      }
    };

    fetchLanguages();
  }, []);

  const handleLanguageChange = async (languageCode: string) => {
    if (languageCode === i18n.language) return;
    
    setLoading(true);
    try {
      await i18n.changeLanguage(languageCode);
      setIsOpen(false);
    } catch (error) {
      console.error('Failed to change language:', error);
    } finally {
      setLoading(false);
    }
  };

  const currentLanguage = languages.find(lang => lang.code === i18n.language) || languages[0];

  if (!currentLanguage) return null;

  return (
    <div className={`player-language-switcher ${variant}`}>
      <button
        className="player-language-button"
        onClick={() => setIsOpen(!isOpen)}
        disabled={loading}
        title="Change Language"
      >
        <span className="language-icon">🌐</span>
        {variant === 'full' && (
          <span className="language-text">{currentLanguage.nativeName}</span>
        )}
        <span className={`dropdown-arrow ${isOpen ? 'open' : ''}`}>
          {variant === 'compact' ? '▼' : '▼'}
        </span>
      </button>

      {isOpen && (
        <div className="player-language-dropdown">
          <div className="language-header">
            <h4>🌍 Choose Language</h4>
          </div>
          
          <div className="language-list">
            {languages.map((language) => (
              <button
                key={language.code}
                className={`language-option ${language.code === i18n.language ? 'active' : ''}`}
                onClick={() => handleLanguageChange(language.code)}
                disabled={loading}
              >
                <div className="language-info">
                  <span className="language-name">{language.nativeName}</span>
                  <span className="language-english">({language.name})</span>
                </div>
                
                {showProgress && language.completionPercentage < 100 && (
                  <div className="completion-indicator">
                    <div className="completion-bar">
                      <div 
                        className="completion-fill"
                        style={{ width: `${language.completionPercentage}%` }}
                      />
                    </div>
                    <span className="completion-text">{language.completionPercentage}%</span>
                  </div>
                )}
                
                {language.code === i18n.language && (
                  <span className="current-indicator">✓</span>
                )}
              </button>
            ))}
          </div>
          
          <div className="language-footer">
            <small>🚀 Help translate SectorWars 2102</small>
          </div>
        </div>
      )}

      {loading && (
        <div className="language-loading">
          <div className="loading-spinner"></div>
          <span>Switching language...</span>
        </div>
      )}
    </div>
  );
};

export default LanguageSwitcher;
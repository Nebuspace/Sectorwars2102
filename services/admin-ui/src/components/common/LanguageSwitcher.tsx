/**
 * Language switcher component for Admin UI
 */

import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { SUPPORTED_LANGUAGES } from '../../i18n';
import './language-switcher.css';

interface Language {
  code: string;
  name: string;
  nativeName: string;
  direction: string;
  isActive: boolean;
  completionPercentage: number;
}

const LanguageSwitcher: React.FC = () => {
  const { i18n } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [languages, setLanguages] = useState<Language[]>([]);
  const [loading, setLoading] = useState(false);

  // Initialize languages from static configuration
  useEffect(() => {
    // Use static configuration directly - no API call needed
    const staticLanguages = Object.entries(SUPPORTED_LANGUAGES).map(([code, info]) => ({
      code,
      name: info.name,
      nativeName: info.nativeName,
      direction: (info as { direction?: string }).direction ?? 'ltr',
      isActive: code === 'en' || ['es', 'fr', 'zh', 'pt'].includes(code),
      completionPercentage: code === 'en' ? 100 : 0
    }));
    setLanguages(staticLanguages.filter(lang => lang.isActive));
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
    <div className="language-switcher">
      <button
        className="language-button"
        onClick={() => setIsOpen(!isOpen)}
        disabled={loading}
        title="Change Language"
      >
        <span className="language-icon">🌐</span>
        <span className="language-text">{currentLanguage.nativeName}</span>
        <span className={`dropdown-arrow ${isOpen ? 'open' : ''}`}>▼</span>
      </button>

      {isOpen && (
        <div className="language-dropdown">
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
                {language.completionPercentage < 100 && (
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
            <small>Help translate SectorWars 2102</small>
          </div>
        </div>
      )}

      {loading && (
        <div className="language-loading">
          <span>Switching language...</span>
        </div>
      )}
    </div>
  );
};

export default LanguageSwitcher;
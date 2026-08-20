/**
 * Language switcher component for Admin UI
 */

import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { SUPPORTED_LANGUAGES } from '../../i18n';
import { api } from '../../utils/auth';
import './language-switcher.css';

interface Language {
  code: string;
  name: string;
  nativeName: string;
  direction: string;
  isActive: boolean;
  completionPercentage: number;
}

interface TranslationProgress {
  overallCompletion?: number;
}

/** Canon OPERATIONS/i18n.md launch-complete rows — never invent key counts. */
const LAUNCH_COMPLETE_CODES = new Set(['en', 'es', 'fr', 'zh', 'pt']);

const staticCompletion = (code: string): number =>
  LAUNCH_COMPLETE_CODES.has(code) ? 100 : 0;

const parseApiPercent = (data: TranslationProgress | undefined): number | null => {
  const pct = data?.overallCompletion;
  return typeof pct === 'number' && Number.isFinite(pct) ? pct : null;
};

const LanguageSwitcher: React.FC = () => {
  const { i18n } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [languages, setLanguages] = useState<Language[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const staticLanguages = Object.entries(SUPPORTED_LANGUAGES)
      .map(([code, info]) => ({
        code,
        name: info.name,
        nativeName: info.nativeName,
        direction: (info as { direction?: string }).direction ?? 'ltr',
        isActive: LAUNCH_COMPLETE_CODES.has(code),
        completionPercentage: staticCompletion(code),
      }))
      .filter((lang) => lang.isActive);

    setLanguages(staticLanguages);

    (async () => {
      const withProgress = await Promise.all(
        staticLanguages.map(async (lang) => {
          try {
            const response = await api.get<TranslationProgress>(
              `/api/v1/i18n/admin/progress/${lang.code}`
            );
            const pct = parseApiPercent(response.data);
            if (pct !== null) {
              return { ...lang, completionPercentage: pct };
            }
          } catch {
            // Static fallback: launch-complete locales stay 100%, never a 0% bar.
          }
          return lang;
        })
      );
      if (!cancelled) setLanguages(withProgress);
    })();

    return () => {
      cancelled = true;
    };
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

  const currentLanguage = languages.find((lang) => lang.code === i18n.language) || languages[0];

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

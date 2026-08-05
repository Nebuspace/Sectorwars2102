/**
 * Internationalization setup for Player Client
 */

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import Backend from 'i18next-http-backend';
import LanguageDetector from 'i18next-browser-languagedetector';

// Detect API base URL for different environments
const getApiBaseUrl = (): string => {
  // In development, use relative URL which will be proxied by Vite
  if (import.meta.env.DEV) {
    return '/api/v1';
  }
  
  // In production, construct URL based on current host
  const protocol = window.location.protocol;
  const hostname = window.location.hostname;
  
  // For GitHub Codespaces, detect the gameserver port
  if (hostname.includes('app.github.dev')) {
    // Extract codespace name and construct gameserver URL
    const codespaceName = hostname.split('-')[0];
    return `${protocol}//${codespaceName}-8080.app.github.dev/api/v1`;
  }
  
  // For local development
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return `${protocol}//${hostname}:8080/api/v1`;
  }
  
  // For production deployment
  return `${protocol}//api.${hostname}/api/v1`;
};

// Supported languages
const SUPPORTED_LANGUAGES = {
  'en': { name: 'English', nativeName: 'English' },
  'es': { name: 'Spanish', nativeName: 'Español' },
  'zh': { name: 'Chinese (Simplified)', nativeName: '中文(简体)' },
  'fr': { name: 'French', nativeName: 'Français' },
  'pt': { name: 'Portuguese', nativeName: 'Português' },
  'de': { name: 'German', nativeName: 'Deutsch' }
};

// Initialize i18n for Player Client
i18n
  .use(Backend)
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    fallbackLng: 'en',
    debug: import.meta.env.DEV,
    
    defaultNS: 'common',
    ns: ['common', 'game', 'auth', 'ai', 'marketing', 'errors', 'validation'],
    
    interpolation: {
      escapeValue: false, // React already escapes values
    },
    
    backend: {
      loadPath: `${getApiBaseUrl()}/i18n/{{lng}}/{{ns}}`,
      requestOptions: {
        mode: 'cors',
        credentials: 'include',
        cache: 'default'
      },
      request: (options: any, url: string, payload: any, callback: Function) => {
        fetch(url, {
          method: options.method || 'GET',
          body: payload,
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            ...options.headers
          },
          credentials: 'include'
        })
        .then(response => {
          if (!response.ok) {
            throw new Error(`Translation fetch failed: ${response.status}`);
          }
          return response.json();
        })
        .then(data => callback(null, { status: 200, data }))
        .catch(error => {
          console.warn('Translation loading error, using fallbacks:', error);
          // Return basic fallback translations for game
          const fallbackTranslations = {
            welcome: 'Welcome to SectorWars 2102',
            dashboard: 'Dashboard',
            login: 'Login',
            logout: 'Logout',
            loading: 'Loading...',
            error: 'Error',
            ship: 'Ship',
            sector: 'Sector',
            credits: 'Credits',
            cargo: 'Cargo',
            trade: 'Trade',
            combat: 'Combat',
            planets: 'Planets',
            galaxy: 'Galaxy'
          };
          callback(null, { status: 200, data: fallbackTranslations });
        });
      }
    },
    
    detection: {
      order: ['localStorage', 'navigator', 'htmlTag'],
      caches: ['localStorage'],
      lookupLocalStorage: 'i18nextLng',
    },
    
    react: {
      useSuspense: false, // Disable suspense to prevent loading issues
      bindI18n: 'languageChanged',
      transEmptyNodeValue: '',
    }
  });

export default i18n;
export { SUPPORTED_LANGUAGES };
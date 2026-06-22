import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { GameTheme, ThemeName } from './types';
import { cockpitTheme } from './themes/cockpit';

interface ThemeContextType {
  currentTheme: GameTheme;
  themeName: ThemeName;
  setTheme: (themeName: ThemeName) => void;
  availableThemes: GameTheme[];
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

// Available themes registry (WO-C5: only cockpit is implemented; the
// default/military/civilian stubs all aliased cockpitTheme and had no picker UI).
const themes: Record<ThemeName, GameTheme> = {
  cockpit: cockpitTheme,
};

interface ThemeProviderProps {
  children: ReactNode;
  defaultTheme?: ThemeName;
}

export const ThemeProvider: React.FC<ThemeProviderProps> = ({ 
  children, 
  defaultTheme = 'cockpit' 
}) => {
  const [themeName, setThemeName] = useState<ThemeName>(() => {
    // Try to load saved theme from localStorage
    const savedTheme = localStorage.getItem('gameTheme') as ThemeName;
    return savedTheme && themes[savedTheme] ? savedTheme : defaultTheme;
  });

  const currentTheme = themes[themeName];

  // Apply CSS variables when theme changes
  useEffect(() => {
    const root = document.documentElement;
    
    // Apply all CSS variables from the theme
    Object.entries(currentTheme.cssVariables).forEach(([property, value]) => {
      root.style.setProperty(property, value);
    });
    
    // Add theme class to body for theme-specific styling
    document.body.className = document.body.className
      .replace(/theme-\w+/g, '') // Remove existing theme classes
      .trim();
    document.body.classList.add(`theme-${themeName}`);
    
    // Save theme preference
    localStorage.setItem('gameTheme', themeName);
  }, [themeName, currentTheme]);

  const setTheme = (newThemeName: ThemeName) => {
    if (themes[newThemeName]) {
      setThemeName(newThemeName);
    }
  };

  const value: ThemeContextType = {
    currentTheme,
    themeName,
    setTheme,
    availableThemes: Object.values(themes),
  };

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
};

export const useTheme = (): ThemeContextType => {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
};

// Hook for accessing theme colors directly
export const useThemeColors = () => {
  const { currentTheme } = useTheme();
  return currentTheme.colors;
};

// Hook for accessing theme fonts directly
export const useThemeFonts = () => {
  const { currentTheme } = useTheme();
  return currentTheme.fonts;
};
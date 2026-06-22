export interface ThemeColors {
  // Primary brand colors
  primary: string;
  primaryHover: string;
  primaryLight: string;
  primaryDark: string;
  
  // Secondary colors
  secondary: string;
  secondaryHover: string;
  
  // Background colors
  background: string;
  backgroundSecondary: string;
  surface: string;
  surfaceHover: string;
  
  // Text colors
  text: string;
  textSecondary: string;
  textMuted: string;
  
  // Status colors
  success: string;
  warning: string;
  error: string;
  info: string;
  
  // UI element colors
  border: string;
  borderHover: string;
  shadow: string;
  overlay: string;
  
  // Game-specific colors
  credits: string;
  turns: string;
  hazard: string;
  radiation: string;
  energy: string;
}

export interface ThemeFonts {
  primary: string;
  secondary: string;
  monospace: string;
  heading: string;
}

export interface ThemeSpacing {
  xs: string;
  sm: string;
  md: string;
  lg: string;
  xl: string;
  '2xl': string;
  '3xl': string;
}

export interface ThemeBreakpoints {
  mobile: string;
  tablet: string;
  desktop: string;
  widescreen: string;
}

export interface ThemeAnimations {
  fast: string;
  normal: string;
  slow: string;
  pulse: string;
  glow: string;
  scan: string;
}

export interface GameTheme {
  name: string;
  displayName: string;
  description: string;
  colors: ThemeColors;
  fonts: ThemeFonts;
  spacing: ThemeSpacing;
  breakpoints: ThemeBreakpoints;
  animations: ThemeAnimations;
  cssVariables: Record<string, string>;
}

// WO-C5: only the cockpit theme is implemented; default/military/civilian were
// unimplemented stubs (all aliased cockpitTheme) with no picker UI — removed.
export type ThemeName = 'cockpit';
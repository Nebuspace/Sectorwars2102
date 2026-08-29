import { describe, expect, it } from 'vitest';
import {
  deriveNebulaTypeFromStrength,
  formatNebulaHoverLabel,
  isNebulaSectorType,
} from '../nebulaChartDisplay';

describe('nebulaChartDisplay', () => {
  describe('isNebulaSectorType', () => {
    it('matches nebula case-insensitively', () => {
      expect(isNebulaSectorType('nebula')).toBe(true);
      expect(isNebulaSectorType('NEBULA')).toBe(true);
      expect(isNebulaSectorType('normal')).toBe(false);
    });
  });

  describe('deriveNebulaTypeFromStrength', () => {
    it('maps strength to canon color keys using nebula_color boundaries', () => {
      expect(deriveNebulaTypeFromStrength(85)).toBe('crimson');
      expect(deriveNebulaTypeFromStrength(65)).toBe('azure');
      expect(deriveNebulaTypeFromStrength(55)).toBe('emerald');
      expect(deriveNebulaTypeFromStrength(45)).toBe('violet');
      expect(deriveNebulaTypeFromStrength(25)).toBe('amber');
      expect(deriveNebulaTypeFromStrength(10)).toBe('obsidian');
    });
  });

  describe('formatNebulaHoverLabel', () => {
    it('formats Crimson and Azure with canon strength ranges', () => {
      expect(formatNebulaHoverLabel('crimson', 85)).toBe('CRIMSON · 80–100');
      expect(formatNebulaHoverLabel('azure', 65)).toBe('AZURE · 60–80');
    });

    it('derives type from strength when nebula_type is absent', () => {
      expect(formatNebulaHoverLabel(null, 90)).toBe('CRIMSON · 80–100');
    });

    it('returns null when no nebula metadata', () => {
      expect(formatNebulaHoverLabel(null, null)).toBeNull();
      expect(formatNebulaHoverLabel(undefined, undefined)).toBeNull();
    });
  });
});

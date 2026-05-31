/**
 * Input Validation Utilities
 * 
 * Provides comprehensive input validation and sanitization to prevent
 * XSS attacks and ensure data integrity according to OWASP guidelines.
 */

import DOMPurify from 'isomorphic-dompurify';

/**
 * Validation rules for different input types
 */
export const ValidationRules = {
  // Alphanumeric with limited special characters
  PLAYER_NAME: /^[a-zA-Z0-9_-]{3,20}$/,
  SHIP_NAME: /^[a-zA-Z0-9 _-]{1,30}$/,
  TEAM_NAME: /^[a-zA-Z0-9 _-]{3,25}$/,
  
  // Numeric validations
  POSITIVE_INTEGER: /^[0-9]+$/,
  PERCENTAGE: /^([0-9]|[1-9][0-9]|100)$/,
  
  // IDs (UUIDs and numeric)
  UUID: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
  NUMERIC_ID: /^[0-9]+$/,
  
  // Combat specific
  TARGET_TYPE: /^(ship|planet|port)$/,
  COMBAT_ACTION: /^(fire|drone_deploy|retreat_attempt)$/,
  
  // Message content (allows more characters but still restricted)
  MESSAGE_CONTENT: /^[\w\s.,!?'"()-]{1,500}$/
};

/**
 * Input validation class with static methods
 */
export class InputValidator {
  /**
   * Sanitize user-generated text content to prevent XSS
   */
  static sanitizeText(input: string): string {
    if (typeof input !== 'string') {
      return '';
    }
    
    // First, trim and limit length
    const trimmed = input.trim().substring(0, 1000);
    
    // Use DOMPurify to clean any HTML/script content
    const cleaned = DOMPurify.sanitize(trimmed, {
      ALLOWED_TAGS: [],
      ALLOWED_ATTR: []
    });
    
    // Additional cleanup for common XSS patterns. The event-handler strip
    // loops until stable because a naive single-pass regex misses cases where
    // removing one `on…=` exposes another (js/incomplete-multi-character-sanitization).
    // The URL-scheme strip covers data: and vbscript: in addition to javascript:
    // (js/incomplete-url-scheme-check).
    let stripped = cleaned
      .replace(/[<>]/g, '')
      .replace(/(?:javascript|data|vbscript):/gi, '');
    let prev = '';
    while (prev !== stripped) {
      prev = stripped;
      stripped = stripped.replace(/on\w+\s*=/gi, '');
    }
    return stripped;
  }

  /**
   * Validate and sanitize player input (names, etc.)
   */
  static validatePlayerInput(input: string, type: keyof typeof ValidationRules): boolean {
    if (typeof input !== 'string') {
      return false;
    }
    
    const rule = ValidationRules[type];
    if (!rule) {
      console.error(`No validation rule found for type: ${type}`);
      return false;
    }
    
    return rule.test(input);
  }

  /**
   * Validate numeric input with range checking
   */
  static validateNumeric(
    input: string | number,
    min: number = 0,
    max: number = Number.MAX_SAFE_INTEGER
  ): { valid: boolean; value?: number } {
    const num = typeof input === 'string' ? parseInt(input, 10) : input;
    
    if (isNaN(num) || !isFinite(num)) {
      return { valid: false };
    }
    
    if (num < min || num > max) {
      return { valid: false };
    }
    
    return { valid: true, value: num };
  }

  /**
   * Validate combat-specific parameters
   */
  static validateCombatParams(params: {
    targetType?: string;
    targetId?: string;
    droneCount?: number;
  }): { valid: boolean; errors: string[] } {
    const errors: string[] = [];
    
    // Validate target type
    if (params.targetType && !ValidationRules.TARGET_TYPE.test(params.targetType)) {
      errors.push('Invalid target type');
    }
    
    // Validate target ID (UUID or numeric)
    if (params.targetId) {
      const isValidUUID = ValidationRules.UUID.test(params.targetId);
      const isValidNumeric = ValidationRules.NUMERIC_ID.test(params.targetId);
      
      if (!isValidUUID && !isValidNumeric) {
        errors.push('Invalid target ID format');
      }
    }
    
    // Validate drone count
    if (params.droneCount !== undefined) {
      const validation = this.validateNumeric(params.droneCount, 0, 9999);
      if (!validation.valid) {
        errors.push('Invalid drone count');
      }
    }
    
    return {
      valid: errors.length === 0,
      errors
    };
  }

  /**
   * Validate trade parameters
   */
  static validateTradeParams(params: {
    resourceType?: string;
    quantity?: number;
    price?: number;
  }): { valid: boolean; errors: string[] } {
    const errors: string[] = [];
    const validResources = ['fuel', 'organics', 'equipment', 'luxury_goods', 'medical_supplies', 'technology'];
    
    if (params.resourceType && !validResources.includes(params.resourceType)) {
      errors.push('Invalid resource type');
    }
    
    if (params.quantity !== undefined) {
      const validation = this.validateNumeric(params.quantity, 1, 999999);
      if (!validation.valid) {
        errors.push('Invalid quantity');
      }
    }
    
    if (params.price !== undefined) {
      const validation = this.validateNumeric(params.price, 0, 999999999);
      if (!validation.valid) {
        errors.push('Invalid price');
      }
    }
    
    return {
      valid: errors.length === 0,
      errors
    };
  }

  /**
   * Sanitize message content for team/player communication
   */
  static sanitizeMessage(message: string): string {
    const sanitized = this.sanitizeText(message);
    
    // Additional message-specific sanitization
    return sanitized
      .substring(0, 500) // Limit message length
      .replace(/\s+/g, ' '); // Normalize whitespace
  }

  /**
   * Validate ship name input
   */
  static validateShipName(name: string): { valid: boolean; sanitized?: string } {
    const sanitized = this.sanitizeText(name);
    
    if (!this.validatePlayerInput(sanitized, 'SHIP_NAME')) {
      return { valid: false };
    }
    
    return { valid: true, sanitized };
  }

  /**
   * Validate and sanitize search queries
   */
  static sanitizeSearchQuery(query: string): string {
    return this.sanitizeText(query)
      .substring(0, 100) // Limit search query length
      .replace(/[^\w\s-]/g, ''); // Allow only word characters, spaces, and hyphens
  }

  /**
   * Rate limiting helper - returns true if action should be allowed
   */
  static checkRateLimit(
    actionKey: string,
    maxAttempts: number = 10,
    windowMs: number = 60000
  ): boolean {
    const now = Date.now();
    const storageKey = `rate_limit_${actionKey}`;
    
    try {
      const storedData = localStorage.getItem(storageKey);
      const attempts: number[] = storedData ? JSON.parse(storedData) : [];
      
      // Remove old attempts outside the window
      const recentAttempts = attempts.filter(time => now - time < windowMs);
      
      if (recentAttempts.length >= maxAttempts) {
        return false; // Rate limit exceeded
      }
      
      // Add current attempt
      recentAttempts.push(now);
      localStorage.setItem(storageKey, JSON.stringify(recentAttempts));
      
      return true;
    } catch (error) {
      console.error('Rate limiting error:', error);
      return true; // Allow on error to prevent blocking legitimate users
    }
  }

  /**
   * Clear rate limit for a specific action
   */
  static clearRateLimit(actionKey: string): void {
    try {
      localStorage.removeItem(`rate_limit_${actionKey}`);
    } catch (error) {
      console.error('Error clearing rate limit:', error);
    }
  }
}

/**
 * XSS Prevention utilities
 */
export class XSSPrevention {
  /**
   * Escape HTML entities in a string
   */
  static escapeHtml(str: string): string {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  /**
   * Create safe HTML from user content
   */
  static createSafeHtml(content: string): { __html: string } {
    const sanitized = DOMPurify.sanitize(content, {
      ALLOWED_TAGS: ['b', 'i', 'em', 'strong', 'br'],
      ALLOWED_ATTR: []
    });
    
    return { __html: sanitized };
  }

  /**
   * Validate URL to prevent javascript: and data: protocols
   */
  static validateUrl(url: string): boolean {
    try {
      const parsed = new URL(url);
      const allowedProtocols = ['http:', 'https:'];
      return allowedProtocols.includes(parsed.protocol);
    } catch {
      return false;
    }
  }
}

/**
 * Security audit logger
 */
export class SecurityAudit {
  static log(event: {
    type: 'validation_failure' | 'xss_attempt' | 'rate_limit_exceeded';
    details: any;
    userId?: string;
  }): void {
    // In production, this would send to the server
    console.warn('[Security Audit]', event);
    
    // Store locally for debugging
    try {
      const auditLog = JSON.parse(localStorage.getItem('security_audit') || '[]');
      auditLog.push({
        ...event,
        timestamp: new Date().toISOString()
      });
      
      // Keep only last 100 entries
      if (auditLog.length > 100) {
        auditLog.splice(0, auditLog.length - 100);
      }
      
      localStorage.setItem('security_audit', JSON.stringify(auditLog));
    } catch (error) {
      console.error('Failed to log security event:', error);
    }
  }
}
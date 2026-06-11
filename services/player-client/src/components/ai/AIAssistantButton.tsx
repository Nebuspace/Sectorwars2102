import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { MessageCircle, Bell, TrendingUp } from 'lucide-react';
import './ai-assistant-button.css';

interface AIAssistantButtonProps {
  onClick: () => void;
  hasNewRecommendations?: boolean;
  recommendationCount?: number;
}

const AIAssistantButton: React.FC<AIAssistantButtonProps> = ({ 
  onClick, 
  hasNewRecommendations = false,
  recommendationCount = 0 
}) => {
  const [isAnimating, setIsAnimating] = useState(false);

  useEffect(() => {
    if (hasNewRecommendations) {
      setIsAnimating(true);
      const timer = setTimeout(() => setIsAnimating(false), 3000);
      return () => clearTimeout(timer);
    }
  }, [hasNewRecommendations]);

  return (
    <motion.button
      className={`ai-assistant-button ${isAnimating ? 'ai-assistant-button--pulsing' : ''}`}
      onClick={onClick}
      whileHover={{ scale: 1.05 }}
      whileTap={{ scale: 0.95 }}
      initial={{ opacity: 0, x: 50 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3 }}
      aria-label="Open ARIA AI Assistant"
    >
      <div className="ai-assistant-button-icon">
        <MessageCircle className="w-6 h-6" />
        
        {/* Notification Badge */}
        {hasNewRecommendations && recommendationCount > 0 && (
          <motion.div
            className="ai-notification-badge"
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ type: "spring", stiffness: 500, damping: 30 }}
          >
            {recommendationCount > 9 ? '9+' : recommendationCount}
          </motion.div>
        )}
        
        {/* Activity Indicator */}
        {hasNewRecommendations && (
          <motion.div
            className="ai-activity-indicator"
            animate={{ 
              scale: [1, 1.2, 1],
              opacity: [0.7, 1, 0.7]
            }}
            transition={{ 
              duration: 2,
              repeat: Infinity,
              ease: "easeInOut"
            }}
          >
            <TrendingUp className="w-3 h-3" />
          </motion.div>
        )}
      </div>
      
      <div className="ai-assistant-button-content">
        <span className="ai-assistant-button-title">AI Assistant</span>
        <span className="ai-assistant-button-subtitle">
          {hasNewRecommendations 
            ? `${recommendationCount} new recommendation${recommendationCount !== 1 ? 's' : ''}`
            : 'Get trading insights'
          }
        </span>
      </div>
      
      {/* Pulse Animation Ring */}
      {isAnimating && (
        <motion.div
          className="ai-pulse-ring"
          animate={{
            scale: [1, 2, 1],
            opacity: [0.5, 0, 0.5]
          }}
          transition={{
            duration: 2,
            repeat: Infinity,
            ease: "easeOut"
          }}
        />
      )}
    </motion.button>
  );
};

export default AIAssistantButton;
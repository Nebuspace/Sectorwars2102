/**
 * Market Intelligence Dashboard - Revolutionary Real-Time Trading Interface
 * Part of Foundation Sprint: Predictive Market Intelligence with ARIA Integration
 * OWASP Compliant • Real-Time WebSocket • AI-Powered Predictions
 */

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { realtimeWebSocket, MarketPrediction } from '../../services/realtimeWebSocket';
import { GameState, MarketData } from '../../types/game';
import './market-intelligence-dashboard.css';

// Security-first interfaces
interface TradingCommand {
  commodity: string;
  action: 'buy' | 'sell';
  amount: number;
  maxPrice?: number;
  minPrice?: number;
  aiAssisted: boolean;
}

interface MarketChartData {
  timestamp: string;
  price: number;
  volume: number;
  prediction?: number;
  confidence?: number;
}

interface ARIAAnalysis {
  summary: string;
  recommendation: 'strong_buy' | 'buy' | 'hold' | 'sell' | 'strong_sell';
  confidence: number;
  reasoning: string[];
  riskFactors: string[];
  timeHorizon: string;
}

interface SecurityValidation {
  isValid: boolean;
  errors: string[];
  sanitizedData: any;
}

// OWASP A03: Input validation utility
const validateTradingCommand = (command: TradingCommand): SecurityValidation => {
  const errors: string[] = [];
  const sanitized = { ...command };

  // Commodity validation
  if (!sanitized.commodity || typeof sanitized.commodity !== 'string') {
    errors.push('Invalid commodity');
  } else {
    // XSS and injection prevention
    sanitized.commodity = sanitized.commodity
      .replace(/[<>"'&]/g, '')
      // Cover the full dangerous-scheme set, not just javascript: — CodeQL flags
      // partial coverage as js/incomplete-url-scheme-check.
      .replace(/(?:javascript|data|vbscript):/gi, '')
      .slice(0, 50);
    
    if (!/^[a-zA-Z0-9_-]+$/.test(sanitized.commodity)) {
      errors.push('Commodity contains invalid characters');
    }
  }

  // Amount validation
  if (!sanitized.amount || typeof sanitized.amount !== 'number' || sanitized.amount <= 0) {
    errors.push('Invalid amount');
  } else if (sanitized.amount > 1000000) {
    errors.push('Amount exceeds maximum limit');
  }

  // Price validation
  if (sanitized.maxPrice && (typeof sanitized.maxPrice !== 'number' || sanitized.maxPrice <= 0)) {
    errors.push('Invalid max price');
  }
  if (sanitized.minPrice && (typeof sanitized.minPrice !== 'number' || sanitized.minPrice <= 0)) {
    errors.push('Invalid min price');
  }

  return {
    isValid: errors.length === 0,
    errors,
    sanitizedData: sanitized
  };
};

// XSS protection for AI responses
const sanitizeAIResponse = (response: string): string => {
  return response
    .replace(/[<>"'&]/g, '')
    .replace(/(?:javascript|data|vbscript):/gi, '')
    .slice(0, 2000);
};

export const MarketIntelligenceDashboard: React.FC = () => {
  // State management
  const [selectedCommodity, setSelectedCommodity] = useState<string>('organics');
  const [marketData, setMarketData] = useState<Map<string, MarketChartData[]>>(new Map());
  const [predictions, setPredictions] = useState<Map<string, MarketPrediction>>(new Map());
  const [ariaAnalysis, setAriaAnalysis] = useState<ARIAAnalysis | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState(realtimeWebSocket.getConnectionStatus());
  const [tradingCommand, setTradingCommand] = useState<Partial<TradingCommand>>({});
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [showAdvancedControls, setShowAdvancedControls] = useState(false);
  const [riskTolerance, setRiskTolerance] = useState<'low' | 'medium' | 'high'>('medium');

  // Refs for performance optimization
  const chartCanvasRef = useRef<HTMLCanvasElement>(null);
  const animationFrameRef = useRef<number>();
  const lastRenderTime = useRef<number>(0);

  // Available commodities for trading
  const availableCommodities = useMemo(() => [
    'organics', 'equipment', 'energy', 'technology', 'luxury', 'minerals'
  ], []);

  // WebSocket connection and subscription management
  useEffect(() => {
    const initializeConnection = async () => {
      try {
        setIsLoading(true);
        
        // Connect if not already connected
        if (!connectionStatus.connected) {
          await realtimeWebSocket.connect();
        }

        // Subscribe to market data for all commodities
        await realtimeWebSocket.subscribeToMarketData(availableCommodities);

        // Subscribe to trading signals
        await realtimeWebSocket.subscribeToTradingSignals();

        setConnectionStatus(realtimeWebSocket.getConnectionStatus());
      } catch (error) {
        console.error('❌ Failed to initialize WebSocket connection:', error);
      } finally {
        setIsLoading(false);
      }
    };

    initializeConnection();

    // Cleanup on unmount
    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [availableCommodities]);

  // Real-time data handlers
  useEffect(() => {
    const handleMarketUpdate = (message: any) => {
      const marketUpdate: MarketData = message.data;
      
      if (marketUpdate.commodity && marketUpdate.price) {
        setMarketData(prev => {
          const newData = new Map(prev);
          const commodityData = newData.get(marketUpdate.commodity) || [];
          
          // Add new data point with timestamp
          const newPoint: MarketChartData = {
            timestamp: new Date().toISOString(),
            price: marketUpdate.price,
            volume: marketUpdate.volume || 0,
            prediction: marketUpdate.predicted_price,
            confidence: marketUpdate.confidence
          };

          // Keep last 100 data points for performance
          const updatedData = [...commodityData, newPoint].slice(-100);
          newData.set(marketUpdate.commodity, updatedData);
          
          return newData;
        });

        setLastUpdate(new Date());
      }
    };

    const handleTradingSignal = (message: any) => {
      const prediction: MarketPrediction = message.data;
      
      if (prediction.commodity) {
        setPredictions(prev => {
          const newPredictions = new Map(prev);
          newPredictions.set(prediction.commodity, prediction);
          return newPredictions;
        });

        // Update ARIA analysis if it's for the selected commodity
        if (prediction.commodity === selectedCommodity) {
          updateARIAAnalysis(prediction);
        }
      }
    };

    // Subscribe to WebSocket events
    realtimeWebSocket.on('market_update', handleMarketUpdate);
    realtimeWebSocket.on('trading_signal', handleTradingSignal);

    // Cleanup listeners
    return () => {
      realtimeWebSocket.off('market_update', handleMarketUpdate);
      realtimeWebSocket.off('trading_signal', handleTradingSignal);
    };
  }, [selectedCommodity]);

  // ARIA Analysis generation
  const updateARIAAnalysis = useCallback((prediction: MarketPrediction) => {
    const analysis: ARIAAnalysis = {
      summary: sanitizeAIResponse(prediction.aiExplanation),
      recommendation: prediction.predictedPrice > prediction.currentPrice ? 'buy' : 'sell',
      confidence: prediction.confidence,
      reasoning: [
        `Current price: $${prediction.currentPrice.toFixed(2)}`,
        `Predicted price: $${prediction.predictedPrice.toFixed(2)}`,
        `Confidence level: ${(prediction.confidence * 100).toFixed(1)}%`,
        `Time horizon: ${prediction.timeHorizon}`
      ],
      riskFactors: [
        `Risk level: ${prediction.riskLevel}`,
        'Market volatility may affect predictions',
        'External events may impact commodity prices'
      ],
      timeHorizon: prediction.timeHorizon
    };

    setAriaAnalysis(analysis);
  }, []);

  // Chart rendering with hardware acceleration
  const renderChart = useCallback(() => {
    const canvas = chartCanvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const commodityData = marketData.get(selectedCommodity) || [];
    if (commodityData.length === 0) return;

    // Throttle rendering to 60fps
    const now = Date.now();
    if (now - lastRenderTime.current < 16) return;
    lastRenderTime.current = now;

    const { width, height } = canvas;
    ctx.clearRect(0, 0, width, height);

    // Chart styling
    const padding = 40;
    const chartWidth = width - 2 * padding;
    const chartHeight = height - 2 * padding;

    // Calculate price range
    const prices = commodityData.map(d => d.price);
    const minPrice = Math.min(...prices);
    const maxPrice = Math.max(...prices);
    const priceRange = maxPrice - minPrice || 1;

    // Draw price line
    ctx.strokeStyle = '#3b82f6';
    ctx.lineWidth = 2;
    ctx.beginPath();

    commodityData.forEach((data, index) => {
      const x = padding + (index / (commodityData.length - 1)) * chartWidth;
      const y = padding + ((maxPrice - data.price) / priceRange) * chartHeight;
      
      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });

    ctx.stroke();

    // Draw prediction line if available
    const prediction = predictions.get(selectedCommodity);
    if (prediction) {
      ctx.strokeStyle = '#10b981';
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 5]);
      
      const lastDataPoint = commodityData[commodityData.length - 1];
      const lastX = padding + chartWidth;
      const lastY = padding + ((maxPrice - lastDataPoint.price) / priceRange) * chartHeight;
      const predY = padding + ((maxPrice - prediction.predictedPrice) / priceRange) * chartHeight;
      
      ctx.beginPath();
      ctx.moveTo(lastX, lastY);
      ctx.lineTo(lastX + 50, predY);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Draw grid lines
    ctx.strokeStyle = 'rgba(75, 85, 99, 0.3)';
    ctx.lineWidth = 1;
    
    for (let i = 0; i <= 5; i++) {
      const y = padding + (i / 5) * chartHeight;
      ctx.beginPath();
      ctx.moveTo(padding, y);
      ctx.lineTo(padding + chartWidth, y);
      ctx.stroke();
    }

    // Draw price labels
    ctx.fillStyle = '#6b7280';
    ctx.font = '12px Inter';
    ctx.textAlign = 'right';
    
    for (let i = 0; i <= 5; i++) {
      const price = maxPrice - (i / 5) * priceRange;
      const y = padding + (i / 5) * chartHeight;
      ctx.fillText(`$${price.toFixed(2)}`, padding - 10, y + 4);
    }
  }, [marketData, predictions, selectedCommodity]);

  // Auto-refresh chart
  useEffect(() => {
    if (autoRefresh) {
      const animate = () => {
        renderChart();
        animationFrameRef.current = requestAnimationFrame(animate);
      };
      animationFrameRef.current = requestAnimationFrame(animate);
    }

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [autoRefresh, renderChart]);

  // Trading command handler
  const handleTradingCommand = useCallback(async () => {
    const command: TradingCommand = {
      commodity: selectedCommodity,
      action: tradingCommand.action || 'buy',
      amount: tradingCommand.amount || 0,
      maxPrice: tradingCommand.maxPrice,
      minPrice: tradingCommand.minPrice,
      aiAssisted: true
    };

    // OWASP A03: Validate all inputs
    const validation = validateTradingCommand(command);
    if (!validation.isValid) {
      alert('❌ Trading command validation failed:\n' + validation.errors.join('\n'));
      return;
    }

    try {
      setIsLoading(true);
      
      // Send trading command through WebSocket
      await realtimeWebSocket.sendChannelMessage('trading_signal', {
        action: 'execute_trade',
        command: validation.sanitizedData,
        timestamp: new Date().toISOString()
      });

      // Clear form
      setTradingCommand({});
      
      alert('✅ Trading command submitted successfully!');
    } catch (error) {
      console.error('❌ Failed to execute trading command:', error);
      alert('❌ Failed to execute trading command. Please try again.');
    } finally {
      setIsLoading(false);
    }
  }, [selectedCommodity, tradingCommand]);

  // Request AI predictions
  const requestPredictions = useCallback(async () => {
    try {
      setIsLoading(true);
      const predictions = await realtimeWebSocket.requestMarketPredictions([selectedCommodity]);
      
      if (predictions.length > 0) {
        updateARIAAnalysis(predictions[0]);
      }
    } catch (error) {
      console.error('❌ Failed to request predictions:', error);
    } finally {
      setIsLoading(false);
    }
  }, [selectedCommodity, updateARIAAnalysis]);

  // Get current price and trend
  const getCurrentMarketInfo = useMemo(() => {
    const commodityData = marketData.get(selectedCommodity);
    if (!commodityData || commodityData.length === 0) {
      return { price: 0, trend: 'neutral', change: 0 };
    }

    const current = commodityData[commodityData.length - 1];
    const previous = commodityData.length > 1 ? commodityData[commodityData.length - 2] : current;
    const change = current.price - previous.price;
    const trend = change > 0 ? 'up' : change < 0 ? 'down' : 'neutral';

    return { price: current.price, trend, change };
  }, [marketData, selectedCommodity]);

  return (
    <div className="market-intelligence-dashboard">
      {/* Header */}
      <div className="dashboard-header">
        <div className="header-title">
          <h2>📈 Market Intelligence Dashboard</h2>
          <div className="connection-status">
            <span className={`status-indicator ${connectionStatus.connected ? 'connected' : 'disconnected'}`}>
              {connectionStatus.connected ? '🟢 Live' : '🔴 Offline'}
            </span>
            <span className="last-update">
              Last update: {lastUpdate.toLocaleTimeString()}
            </span>
          </div>
        </div>
        
        <div className="header-controls">
          <label className="auto-refresh-toggle">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh
          </label>
          
          <button
            className="refresh-btn"
            onClick={requestPredictions}
            disabled={isLoading}
          >
            {isLoading ? '⏳' : '🔄'} Get ARIA Analysis
          </button>

          <button
            className="advanced-toggle"
            onClick={() => setShowAdvancedControls(!showAdvancedControls)}
          >
            ⚙️ Advanced
          </button>
        </div>
      </div>

      {/* Main Content */}
      <div className="dashboard-content">
        {/* Left Panel - Chart and Controls */}
        <div className="chart-panel">
          {/* Commodity Selector */}
          <div className="commodity-selector">
            {availableCommodities.map(commodity => (
              <button
                key={commodity}
                className={`commodity-btn ${selectedCommodity === commodity ? 'active' : ''}`}
                onClick={() => setSelectedCommodity(commodity)}
              >
                {commodity.charAt(0).toUpperCase() + commodity.slice(1)}
              </button>
            ))}
          </div>

          {/* Price Information */}
          <div className="price-info">
            <div className="current-price">
              <span className="price-label">Current Price</span>
              <span className={`price-value ${getCurrentMarketInfo.trend}`}>
                ${getCurrentMarketInfo.price.toFixed(2)}
                <span className="price-change">
                  {getCurrentMarketInfo.change >= 0 ? '+' : ''}
                  {getCurrentMarketInfo.change.toFixed(2)}
                </span>
              </span>
            </div>

            {predictions.has(selectedCommodity) && (
              <div className="prediction-info">
                <span className="prediction-label">AI Prediction</span>
                <span className="prediction-value">
                  ${predictions.get(selectedCommodity)!.predictedPrice.toFixed(2)}
                  <span className="confidence">
                    {(predictions.get(selectedCommodity)!.confidence * 100).toFixed(1)}% confidence
                  </span>
                </span>
              </div>
            )}
          </div>

          {/* Chart */}
          <div className="chart-container">
            <canvas
              ref={chartCanvasRef}
              width={600}
              height={300}
              className="market-chart"
            />
          </div>

          {/* Trading Controls */}
          <div className="trading-controls">
            <h3>🤖 ARIA-Assisted Trading</h3>
            
            <div className="trading-form">
              <div className="form-row">
                <label>
                  Action:
                  <select
                    value={tradingCommand.action || 'buy'}
                    onChange={(e) => setTradingCommand(prev => ({
                      ...prev,
                      action: e.target.value as 'buy' | 'sell'
                    }))}
                  >
                    <option value="buy">Buy</option>
                    <option value="sell">Sell</option>
                  </select>
                </label>

                <label>
                  Amount:
                  <input
                    type="number"
                    min="1"
                    max="1000000"
                    value={tradingCommand.amount || ''}
                    onChange={(e) => setTradingCommand(prev => ({
                      ...prev,
                      amount: parseInt(e.target.value) || 0
                    }))}
                    placeholder="Enter amount"
                  />
                </label>
              </div>

              {showAdvancedControls && (
                <div className="advanced-controls">
                  <div className="form-row">
                    <label>
                      Max Price:
                      <input
                        type="number"
                        step="0.01"
                        value={tradingCommand.maxPrice || ''}
                        onChange={(e) => setTradingCommand(prev => ({
                          ...prev,
                          maxPrice: parseFloat(e.target.value) || undefined
                        }))}
                        placeholder="Optional"
                      />
                    </label>

                    <label>
                      Min Price:
                      <input
                        type="number"
                        step="0.01"
                        value={tradingCommand.minPrice || ''}
                        onChange={(e) => setTradingCommand(prev => ({
                          ...prev,
                          minPrice: parseFloat(e.target.value) || undefined
                        }))}
                        placeholder="Optional"
                      />
                    </label>
                  </div>

                  <label>
                    Risk Tolerance:
                    <select
                      value={riskTolerance}
                      onChange={(e) => setRiskTolerance(e.target.value as 'low' | 'medium' | 'high')}
                    >
                      <option value="low">Conservative</option>
                      <option value="medium">Moderate</option>
                      <option value="high">Aggressive</option>
                    </select>
                  </label>
                </div>
              )}

              <button
                className="execute-trade-btn"
                onClick={handleTradingCommand}
                disabled={isLoading || !tradingCommand.amount}
              >
                {isLoading ? '⏳ Processing...' : '🚀 Execute Trade'}
              </button>
            </div>
          </div>
        </div>

        {/* Right Panel - ARIA Analysis */}
        <div className="analysis-panel">
          <h3>🤖 ARIA Market Analysis</h3>
          
          {ariaAnalysis ? (
            <div className="aria-analysis">
              <div className="analysis-summary">
                <h4>Summary</h4>
                <p>{ariaAnalysis.summary}</p>
              </div>

              <div className="recommendation">
                <h4>Recommendation</h4>
                <span className={`recommendation-badge ${ariaAnalysis.recommendation}`}>
                  {ariaAnalysis.recommendation.replace('_', ' ').toUpperCase()}
                </span>
                <span className="confidence-badge">
                  {(ariaAnalysis.confidence * 100).toFixed(1)}% confidence
                </span>
              </div>

              <div className="reasoning">
                <h4>Analysis</h4>
                <ul>
                  {ariaAnalysis.reasoning.map((reason, index) => (
                    <li key={index}>{reason}</li>
                  ))}
                </ul>
              </div>

              <div className="risk-factors">
                <h4>Risk Factors</h4>
                <ul>
                  {ariaAnalysis.riskFactors.map((risk, index) => (
                    <li key={index}>{risk}</li>
                  ))}
                </ul>
              </div>

              <div className="time-horizon">
                <span className="label">Time Horizon:</span>
                <span className="value">{ariaAnalysis.timeHorizon}</span>
              </div>
            </div>
          ) : (
            <div className="no-analysis">
              <p>Click "Get ARIA Analysis" to receive AI-powered market insights for {selectedCommodity}.</p>
              <button
                className="request-analysis-btn"
                onClick={requestPredictions}
                disabled={isLoading}
              >
                {isLoading ? '⏳ Analyzing...' : '🤖 Request Analysis'}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default MarketIntelligenceDashboard;
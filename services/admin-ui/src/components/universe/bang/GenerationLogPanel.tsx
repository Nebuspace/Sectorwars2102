import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useBangGenerationStream } from '../../../hooks/useBangGenerationStream';
import { classForWarningCategory } from './errorCodeMap';
import './generation-log-panel.css';

interface GenerationLogPanelProps {
  /**
   * Job id whose log we're tailing. Null = panel renders an empty placeholder
   * (no stream connected). The hook closes the stream automatically when
   * jobId changes or the component unmounts.
   */
  jobId: string | null;
  /** Optional title override for embedded use. */
  title?: string;
}

/** Heuristic category classifier for incoming log lines. */
function inferCategory(line: string): string {
  // Bang's stderr is JSON-line per spec; older lines may be plain text.
  // We probe for {"code":"B-NNN"...} and category keywords.
  const upper = line.toUpperCase();
  if (upper.includes('TOPOLOGY_RESCUE') || upper.includes('B-500')) return 'TOPOLOGY_RESCUE';
  if (upper.includes('EMISSION_UNDERTARGET') || upper.includes('B-400')) return 'EMISSION_UNDERTARGET';
  if (upper.includes('EMISSION_OVERTARGET') || upper.includes('B-410')) return 'EMISSION_OVERTARGET';
  if (upper.includes('HEURISTIC_FALLBACK') || upper.includes('B-420')) return 'HEURISTIC_FALLBACK';
  if (upper.includes('COMMODITY_COVERAGE') || upper.includes('B-200')) return 'COMMODITY_COVERAGE';
  if (upper.includes('BUBBLE_FALLBACK') || upper.includes('B-510')) return 'BUBBLE_FALLBACK';
  if (upper.includes('VALIDATOR') || upper.includes('FAIL')) return 'VALIDATOR_FAILURE';
  return '';
}

const GenerationLogPanel: React.FC<GenerationLogPanelProps> = ({
  jobId,
  title,
}) => {
  const { t } = useTranslation('admin');
  const { lines, status, isStreaming, error } = useBangGenerationStream(jobId);
  const [autoScroll, setAutoScroll] = useState(true);
  const [copied, setCopied] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll the log to the bottom on each new line, unless paused.
  useEffect(() => {
    if (!autoScroll || !logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines, autoScroll]);

  const classifiedLines = useMemo(
    () =>
      lines.map((line, idx) => ({
        idx,
        line,
        category: inferCategory(line),
      })),
    [lines],
  );

  const handleCopy = async () => {
    const blob = lines.join('\n');
    try {
      await navigator.clipboard.writeText(blob);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API can fail in non-secure contexts; fall back silently.
      setCopied(false);
    }
  };

  const statusLabel = t(`bang.log.status.${status}`);

  return (
    <div className="generation-log-panel">
      <div className="log-header">
        <div className="log-title">
          <h3>{title ?? t('bang.log.title')}</h3>
          <p className="log-subtitle">{t('bang.log.subtitle')}</p>
        </div>
        <div className="log-toolbar">
          <span className={`log-status log-status-${status.toLowerCase()}`}>
            {isStreaming ? t('bang.log.streaming') : statusLabel}
          </span>
          <button
            type="button"
            className="log-btn"
            onClick={() => setAutoScroll((v) => !v)}
            aria-pressed={!autoScroll}
          >
            {autoScroll ? t('bang.log.pause') : t('bang.log.resume')}
          </button>
          <button
            type="button"
            className="log-btn"
            onClick={handleCopy}
            disabled={lines.length === 0}
          >
            {copied ? t('bang.log.copied') : t('bang.log.copy')}
          </button>
        </div>
      </div>

      {error && (
        <p className="log-error">
          {t('bang.log.error', { error })}
        </p>
      )}

      <div ref={logRef} className="log-body" role="log" aria-live="polite">
        {classifiedLines.length === 0 ? (
          <p className="log-empty">
            {jobId ? t('bang.log.connecting') : t('bang.log.empty')}
          </p>
        ) : (
          classifiedLines.map(({ idx, line, category }) => (
            <div
              key={idx}
              className={`log-line ${
                category ? `log-line-${classForWarningCategory(category)}` : ''
              }`}
            >
              {category && (
                <span className="log-category">{category}</span>
              )}
              <span className="log-text">{line}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default GenerationLogPanel;

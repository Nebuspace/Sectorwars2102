import React, { useState, useEffect, useCallback } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import './translation-management.css';

interface Language {
  code: string;
  name: string;
  nativeName: string;
  direction: string;
  isActive: boolean;
  completionPercentage: number;
}

interface NamespaceProgress {
  totalKeys: number;
  translatedKeys: number;
  verifiedKeys: number;
  completionPercentage: number;
  lastUpdated: string;
}

interface TranslationProgress {
  language: string;
  overallCompletion: number;
  totalKeys: number;
  translatedKeys: number;
  namespaces: Record<string, NamespaceProgress>;
}

const completionClass = (pct: number): string => {
  if (pct >= 90) return 'tm-good';
  if (pct >= 50) return 'tm-warning';
  return 'tm-critical';
};

const formatTimestamp = (iso: string): string => {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '—';
  return parsed.toLocaleString();
};

interface TranslationKeyRow {
  key: string;
  value: string;
}

// GET /api/v1/i18n/{lang}/{namespace} returns a nested object (dot-notation
// keys rebuilt as sub-objects, e.g. "buttons.save" -> {buttons: {save: "..."}}).
// Flatten it back to dot-notation rows for editing — set_translation's `key`
// param expects the original dotted form.
const flattenTranslations = (
  obj: Record<string, unknown>,
  prefix = ''
): TranslationKeyRow[] => {
  const rows: TranslationKeyRow[] = [];
  Object.entries(obj).forEach(([segment, value]) => {
    const fullKey = prefix ? `${prefix}.${segment}` : segment;
    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      rows.push(...flattenTranslations(value as Record<string, unknown>, fullKey));
    } else {
      rows.push({ key: fullKey, value: value == null ? '' : String(value) });
    }
  });
  return rows;
};

const TranslationManagement: React.FC = () => {
  const toast = useToast();
  const confirm = useConfirm();

  const [languages, setLanguages] = useState<Language[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [progress, setProgress] = useState<TranslationProgress | null>(null);
  const [progressLoading, setProgressLoading] = useState<boolean>(false);
  const [progressError, setProgressError] = useState<string | null>(null);

  // Namespace key browser (per-namespace expandable panel within Progress)
  const [browsingNamespace, setBrowsingNamespace] = useState<string | null>(null);
  const [namespaceKeys, setNamespaceKeys] = useState<TranslationKeyRow[]>([]);
  const [namespaceKeysLoading, setNamespaceKeysLoading] = useState(false);
  const [namespaceKeysError, setNamespaceKeysError] = useState<string | null>(null);

  // Single-key edit/add modal
  const [editingKey, setEditingKey] = useState<{ namespace: string; isNew: boolean } | null>(null);
  const [keyForm, setKeyForm] = useState({ key: '', value: '', context: '' });
  const [savingKey, setSavingKey] = useState(false);

  const fetchLanguages = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await api.get<Language[]>('/api/v1/i18n/admin/languages/all');
      setLanguages(response.data ?? []);
    } catch (err) {
      console.error('Error fetching languages:', err);
      setError('Failed to load languages. The translation service may be unavailable.');
      setLanguages([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchProgress = useCallback(async (code: string) => {
    try {
      setProgressLoading(true);
      setProgressError(null);
      setProgress(null);
      const response = await api.get<TranslationProgress>(`/api/v1/i18n/admin/progress/${code}`);
      setProgress(response.data ?? null);
    } catch (err) {
      console.error('Error fetching translation progress:', err);
      setProgressError(`Failed to load progress for "${code}".`);
      setProgress(null);
    } finally {
      setProgressLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLanguages();
  }, [fetchLanguages]);

  useEffect(() => {
    if (selectedCode) {
      fetchProgress(selectedCode);
    }
  }, [selectedCode, fetchProgress]);

  const fetchNamespaceKeys = useCallback(async (code: string, namespace: string) => {
    try {
      setNamespaceKeysLoading(true);
      setNamespaceKeysError(null);
      const response = await api.get<Record<string, unknown>>(`/api/v1/i18n/${code}/${namespace}`);
      const rows = flattenTranslations(response.data ?? {}).sort((a, b) =>
        a.key.localeCompare(b.key)
      );
      setNamespaceKeys(rows);
    } catch (err: any) {
      console.error('Error fetching namespace translations:', err);
      setNamespaceKeysError(err.response?.data?.detail || `Failed to load keys for "${namespace}".`);
      setNamespaceKeys([]);
    } finally {
      setNamespaceKeysLoading(false);
    }
  }, []);

  const handleSelect = (code: string): void => {
    setSelectedCode((prev) => (prev === code ? null : code));
    setBrowsingNamespace(null);
    setNamespaceKeys([]);
  };

  const handleBrowseNamespace = (namespace: string): void => {
    if (browsingNamespace === namespace) {
      setBrowsingNamespace(null);
      return;
    }
    setBrowsingNamespace(namespace);
    if (selectedCode) fetchNamespaceKeys(selectedCode, namespace);
  };

  const openKeyEditor = (namespace: string, row: TranslationKeyRow): void => {
    setEditingKey({ namespace, isNew: false });
    setKeyForm({ key: row.key, value: row.value, context: '' });
  };

  const openAddKey = (namespace: string): void => {
    setEditingKey({ namespace, isNew: true });
    setKeyForm({ key: '', value: '', context: '' });
  };

  const handleKeySubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (!editingKey || !selectedCode) return;

    const trimmedKey = keyForm.key.trim();
    if (!trimmedKey) return;

    const ok = await confirm({
      title: editingKey.isNew ? 'Add Translation Key' : 'Update Translation Key',
      message: `Save "${trimmedKey}" for ${selectedCode}/${editingKey.namespace}?`,
      confirmLabel: 'Save',
    });
    if (!ok) return;

    setSavingKey(true);
    try {
      await api.post(
        `/api/v1/i18n/admin/translation/${selectedCode}/${editingKey.namespace}`,
        {
          key: trimmedKey,
          value: keyForm.value,
          context: keyForm.context.trim() || null,
        }
      );
      toast.success(`Saved "${trimmedKey}".`);
      const { namespace } = editingKey;
      setEditingKey(null);
      await fetchNamespaceKeys(selectedCode, namespace);
      await fetchProgress(selectedCode);
    } catch (err: any) {
      console.error('Error saving translation key:', err);
      toast.error(err.response?.data?.detail || 'Failed to save translation key.');
    } finally {
      setSavingKey(false);
    }
  };

  const activeCount = languages.filter((lang) => lang.isActive).length;
  const fullyTranslated = languages.filter((lang) => lang.completionPercentage >= 100).length;
  const averageCompletion = languages.length > 0
    ? Math.round(languages.reduce((sum, lang) => sum + lang.completionPercentage, 0) / languages.length)
    : 0;

  const namespaceEntries: Array<[string, NamespaceProgress]> = progress
    ? Object.entries(progress.namespaces)
    : [];

  return (
    <div className="page-container">
      <PageHeader
        title="Translation Management"
        subtitle="Monitor internationalization coverage and per-language translation progress"
      />

      <div className="page-content translation-management">
        {error && (
          <div className="tm-banner tm-banner-error">
            <span className="flex-1">{error}</span>
            <button onClick={fetchLanguages} className="tm-btn">Retry</button>
          </div>
        )}

        {!error && !loading && languages.length > 0 && (
          <section className="tm-stats-grid">
            <div className="tm-stat-card">
              <span className="tm-stat-label">Languages</span>
              <span className="tm-stat-value">{languages.length}</span>
              <span className="tm-stat-sub">{activeCount} active</span>
            </div>
            <div className="tm-stat-card">
              <span className="tm-stat-label">Avg Completion</span>
              <span className={`tm-stat-value ${completionClass(averageCompletion)}`}>{averageCompletion}%</span>
              <span className="tm-stat-sub">across all languages</span>
            </div>
            <div className="tm-stat-card">
              <span className="tm-stat-label">Fully Translated</span>
              <span className="tm-stat-value tm-good">{fullyTranslated}</span>
              <span className="tm-stat-sub">at 100% coverage</span>
            </div>
          </section>
        )}

        <section className="tm-section">
          <div className="tm-section-header">
            <div>
              <h3 className="tm-section-title">Languages Overview</h3>
              <p className="tm-section-subtitle">Completion percentage and missing keys per language</p>
            </div>
            <button onClick={fetchLanguages} className="tm-btn" disabled={loading}>
              {loading ? 'Loading…' : 'Refresh'}
            </button>
          </div>

          {loading ? (
            <div className="tm-empty">
              <div className="tm-spinner" />
              <span>Loading languages…</span>
            </div>
          ) : languages.length === 0 && !error ? (
            <div className="tm-empty">
              <span className="tm-empty-icon">🌐</span>
              <span>No languages configured yet.</span>
            </div>
          ) : languages.length > 0 ? (
            <div className="tm-table-container">
              <table className="tm-table">
                <thead>
                  <tr>
                    <th>Language</th>
                    <th>Code</th>
                    <th>Direction</th>
                    <th>Completion</th>
                    <th>Status</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {languages.map((lang) => {
                    const isSelected = lang.code === selectedCode;
                    return (
                      <tr key={lang.code} className={isSelected ? 'tm-row-selected' : ''}>
                        <td>
                          <div className="tm-lang-name">{lang.name}</div>
                          <div className="tm-lang-native">{lang.nativeName}</div>
                        </td>
                        <td className="tm-mono">{lang.code}</td>
                        <td className="tm-mono tm-uppercase">{lang.direction}</td>
                        <td>
                          <div className="tm-progress-cell">
                            <div className="tm-progress-track">
                              <div
                                className={`tm-progress-fill ${completionClass(lang.completionPercentage)}`}
                                style={{ width: `${Math.min(100, Math.max(0, lang.completionPercentage))}%` }}
                              />
                            </div>
                            <span className={`tm-progress-pct ${completionClass(lang.completionPercentage)}`}>
                              {lang.completionPercentage}%
                            </span>
                          </div>
                        </td>
                        <td>
                          <span className={`tm-badge ${lang.isActive ? 'tm-badge-active' : 'tm-badge-inactive'}`}>
                            {lang.isActive ? 'Active' : 'Inactive'}
                          </span>
                        </td>
                        <td>
                          <button
                            onClick={() => handleSelect(lang.code)}
                            className="tm-btn tm-btn-small"
                          >
                            {isSelected ? 'Hide' : 'View progress'}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>

        {selectedCode && (
          <section className="tm-section">
            <div className="tm-section-header">
              <div>
                <h3 className="tm-section-title">Progress: {selectedCode}</h3>
                <p className="tm-section-subtitle">Per-namespace translation breakdown</p>
              </div>
              <button
                onClick={() => fetchProgress(selectedCode)}
                className="tm-btn"
                disabled={progressLoading}
              >
                {progressLoading ? 'Loading…' : 'Refresh'}
              </button>
            </div>

            {progressLoading ? (
              <div className="tm-empty">
                <div className="tm-spinner" />
                <span>Loading progress…</span>
              </div>
            ) : progressError ? (
              <div className="tm-banner tm-banner-error">
                <span className="flex-1">{progressError}</span>
                <button onClick={() => fetchProgress(selectedCode)} className="tm-btn">Retry</button>
              </div>
            ) : progress ? (
              <>
                <div className="tm-stats-grid">
                  <div className="tm-stat-card">
                    <span className="tm-stat-label">Overall</span>
                    <span className={`tm-stat-value ${completionClass(progress.overallCompletion)}`}>
                      {progress.overallCompletion}%
                    </span>
                    <span className="tm-stat-sub">completion</span>
                  </div>
                  <div className="tm-stat-card">
                    <span className="tm-stat-label">Translated Keys</span>
                    <span className="tm-stat-value">{progress.translatedKeys.toLocaleString()}</span>
                    <span className="tm-stat-sub">of {progress.totalKeys.toLocaleString()}</span>
                  </div>
                  <div className="tm-stat-card">
                    <span className="tm-stat-label">Missing Keys</span>
                    <span className="tm-stat-value tm-warning">
                      {Math.max(0, progress.totalKeys - progress.translatedKeys).toLocaleString()}
                    </span>
                    <span className="tm-stat-sub">untranslated</span>
                  </div>
                </div>

                {namespaceEntries.length === 0 ? (
                  <div className="tm-empty">
                    <span className="tm-empty-icon">📭</span>
                    <span>No namespace data recorded for this language.</span>
                  </div>
                ) : (
                  <div className="tm-table-container">
                    <table className="tm-table">
                      <thead>
                        <tr>
                          <th>Namespace</th>
                          <th>Translated</th>
                          <th>Verified</th>
                          <th>Missing</th>
                          <th>Completion</th>
                          <th>Last Updated</th>
                          <th>Keys</th>
                        </tr>
                      </thead>
                      <tbody>
                        {namespaceEntries.map(([name, ns]) => (
                          <React.Fragment key={name}>
                            <tr>
                              <td className="tm-mono">{name}</td>
                              <td>{ns.translatedKeys.toLocaleString()} / {ns.totalKeys.toLocaleString()}</td>
                              <td>{ns.verifiedKeys.toLocaleString()}</td>
                              <td className="tm-warning">
                                {Math.max(0, ns.totalKeys - ns.translatedKeys).toLocaleString()}
                              </td>
                              <td>
                                <div className="tm-progress-cell">
                                  <div className="tm-progress-track">
                                    <div
                                      className={`tm-progress-fill ${completionClass(ns.completionPercentage)}`}
                                      style={{ width: `${Math.min(100, Math.max(0, ns.completionPercentage))}%` }}
                                    />
                                  </div>
                                  <span className={`tm-progress-pct ${completionClass(ns.completionPercentage)}`}>
                                    {ns.completionPercentage}%
                                  </span>
                                </div>
                              </td>
                              <td className="tm-mono tm-muted">{formatTimestamp(ns.lastUpdated)}</td>
                              <td>
                                <button
                                  onClick={() => handleBrowseNamespace(name)}
                                  className="tm-btn tm-btn-small"
                                >
                                  {browsingNamespace === name ? 'Hide' : 'Browse'}
                                </button>
                              </td>
                            </tr>
                            {browsingNamespace === name && (
                              <tr className="tm-namespace-keys-row">
                                <td colSpan={7}>
                                  <div className="tm-namespace-keys">
                                    <div className="tm-namespace-keys-header">
                                      <span>Keys in {selectedCode}/{name}</span>
                                      <button
                                        onClick={() => openAddKey(name)}
                                        className="tm-btn tm-btn-small"
                                      >
                                        + Add Key
                                      </button>
                                    </div>
                                    {namespaceKeysLoading ? (
                                      <div className="tm-empty">
                                        <div className="tm-spinner" />
                                        <span>Loading keys…</span>
                                      </div>
                                    ) : namespaceKeysError ? (
                                      <div className="tm-banner tm-banner-error">
                                        <span className="flex-1">{namespaceKeysError}</span>
                                        <button
                                          onClick={() => fetchNamespaceKeys(selectedCode!, name)}
                                          className="tm-btn"
                                        >
                                          Retry
                                        </button>
                                      </div>
                                    ) : namespaceKeys.length === 0 ? (
                                      <div className="tm-empty">
                                        <span>No keys recorded in this namespace yet.</span>
                                      </div>
                                    ) : (
                                      <table className="tm-table tm-keys-table">
                                        <thead>
                                          <tr>
                                            <th>Key</th>
                                            <th>Value</th>
                                            <th>Action</th>
                                          </tr>
                                        </thead>
                                        <tbody>
                                          {namespaceKeys.map((row) => (
                                            <tr key={row.key}>
                                              <td className="tm-mono">{row.key}</td>
                                              <td className="tm-key-value" title={row.value}>
                                                {row.value || <span className="tm-muted">—</span>}
                                              </td>
                                              <td>
                                                <button
                                                  onClick={() => openKeyEditor(name, row)}
                                                  className="tm-btn tm-btn-small"
                                                >
                                                  Edit
                                                </button>
                                              </td>
                                            </tr>
                                          ))}
                                        </tbody>
                                      </table>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            ) : null}
          </section>
        )}
      </div>

      {editingKey && (
        <div className="modal-overlay" onClick={() => !savingKey && setEditingKey(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">
                {editingKey.isNew ? 'Add Translation Key' : 'Edit Translation Key'}
              </h3>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => setEditingKey(null)}
                disabled={savingKey}
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <form onSubmit={handleKeySubmit}>
                <div className="form-group">
                  <label className="form-label">
                    {selectedCode}/{editingKey.namespace} Key
                  </label>
                  <input
                    type="text"
                    className="form-input"
                    value={keyForm.key}
                    onChange={(e) => setKeyForm({ ...keyForm, key: e.target.value })}
                    placeholder="buttons.save"
                    readOnly={!editingKey.isNew}
                    disabled={!editingKey.isNew || savingKey}
                    required
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Value</label>
                  <textarea
                    className="form-textarea"
                    value={keyForm.value}
                    onChange={(e) => setKeyForm({ ...keyForm, value: e.target.value })}
                    rows={4}
                    disabled={savingKey}
                    required
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Context (optional)</label>
                  <input
                    type="text"
                    className="form-input"
                    value={keyForm.context}
                    onChange={(e) => setKeyForm({ ...keyForm, context: e.target.value })}
                    placeholder="Note for translators"
                    disabled={savingKey}
                  />
                </div>
                <div className="modal-footer">
                  <button
                    type="button"
                    className="btn btn-outline"
                    onClick={() => setEditingKey(null)}
                    disabled={savingKey}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-primary" disabled={savingKey}>
                    {savingKey ? 'Saving…' : 'Save'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default TranslationManagement;

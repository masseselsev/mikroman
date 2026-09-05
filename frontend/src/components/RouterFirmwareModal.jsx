import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  X,
  RefreshCw,
  Cpu,
  Package,
  ShieldCheck,
  AlertTriangle,
  CheckCircle2,
  Search,
  ArrowRight,
  Terminal,
} from 'lucide-react';
import { api } from '../api/client';
import { useI18n } from '../context/I18nContext';

export default function RouterFirmwareModal({
  isOpen,
  onClose,
  routerId,
  routerName,
  onUpgradeSuccess,
}) {
  const { t } = useI18n ? useI18n() : { t: (k) => k };

  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [channelUpdating, setChannelUpdating] = useState(false);
  const [error, setError] = useState(null);

  // Changelog
  const [changelog, setChangelog] = useState(null);
  const [changelogLoading, setChangelogLoading] = useState(false);
  const [changelogSearch, setChangelogSearch] = useState('');

  // Confirmation & Staging
  const [confirmName, setConfirmName] = useState('');
  const [stageBootloader, setStageBootloader] = useState(true);

  // Reconnection State Machine: 'idle' | 'backing_up' | 'issuing' | 'rebooting' | 'online'
  const [rebootStage, setRebootStage] = useState('idle');
  const [rebootMsg, setRebootMsg] = useState('');
  const [targetVersion, setTargetVersion] = useState('');

  // Bootloader standalone upgrade
  const [bootloaderConfirmName, setBootloaderConfirmName] = useState('');
  const [bootloaderUpgrading, setBootloaderUpgrading] = useState(false);
  const [showBootloaderPrompt, setShowBootloaderPrompt] = useState(false);

  const pollIntervalRef = useRef(null);

  const clearRebootPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    if (!routerId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.getFirmwareStatus(routerId);
      setStatus(data);
    } catch (err) {
      setError(err.message || 'Failed to load firmware status');
    } finally {
      setLoading(false);
    }
  }, [routerId]);

  useEffect(() => {
    if (isOpen && routerId) {
      setRebootStage('idle');
      setConfirmName('');
      setBootloaderConfirmName('');
      setShowBootloaderPrompt(false);
      clearRebootPolling();
      fetchStatus();
    }
    return () => {
      clearRebootPolling();
    };
  }, [isOpen, routerId, fetchStatus, clearRebootPolling]);

  // Load changelog when target version is detected
  useEffect(() => {
    const latest = status?.packages?.latest_version;
    if (isOpen && routerId && latest) {
      let active = true;
      setChangelogLoading(true);
      api
        .getChangelog(routerId, latest)
        .then((res) => {
          if (active) setChangelog(res.notes);
        })
        .catch(() => {
          if (active) setChangelog(null);
        })
        .finally(() => {
          if (active) setChangelogLoading(false);
        });
      return () => {
        active = false;
      };
    }
  }, [isOpen, routerId, status?.packages?.latest_version]);

  const handleCheckUpdates = async () => {
    if (!routerId || refreshing) return;
    setRefreshing(true);
    setError(null);
    try {
      const data = await api.checkFirmwareUpdates(routerId);
      setStatus(data);
    } catch (err) {
      setError(err.message || 'Check for updates failed');
    } finally {
      setRefreshing(false);
    }
  };

  const handleChannelChange = async (newChannel) => {
    if (!routerId || channelUpdating) return;
    setChannelUpdating(true);
    setError(null);
    try {
      const data = await api.setFirmwareChannel(routerId, newChannel);
      setStatus(data);
    } catch (err) {
      setError(err.message || 'Failed to switch update channel');
    } finally {
      setChannelUpdating(false);
    }
  };

  const startReconnectionPolling = useCallback(() => {
    clearRebootPolling();
    let attempts = 0;
    pollIntervalRef.current = setInterval(async () => {
      attempts++;
      try {
        const fresh = await api.getFirmwareStatus(routerId);
        if (fresh && fresh.packages) {
          clearRebootPolling();
          setStatus(fresh);
          setRebootStage('online');
          if (onUpgradeSuccess) onUpgradeSuccess();
        }
      } catch {
        // Router is rebooting, still offline - normal behavior
      }
      if (attempts > 60) {
        // 3 minutes timeout
        clearRebootPolling();
        setError('Reconnection timed out. Please refresh the page.');
      }
    }, 3000);
  }, [routerId, clearRebootPolling, onUpgradeSuccess]);

  const handleExecuteUpgrade = async () => {
    if (!routerId || confirmName.trim() !== (routerName || '').trim()) return;
    setRebootStage('backing_up');
    setRebootMsg('Creating automated pinned disaster-recovery backup...');
    setError(null);

    const targetVer = status?.packages?.latest_version || '';
    setTargetVersion(targetVer);

    try {
      setRebootStage('issuing');
      setRebootMsg('Dispatching firmware installation and reboot trigger...');

      await api.upgradeRouterFirmware(routerId, {
        confirm_name: confirmName.trim(),
        stage_bootloader: stageBootloader,
      });

      setRebootStage('rebooting');
      setRebootMsg(`Router is rebooting into v${targetVer}. Waiting for reconnection...`);
      startReconnectionPolling();
    } catch (err) {
      setRebootStage('idle');
      setError(err.message || 'Upgrade failed');
    }
  };

  const handleUpgradeBootloaderOnly = async () => {
    if (!routerId || bootloaderConfirmName.trim() !== (routerName || '').trim()) return;
    setBootloaderUpgrading(true);
    setError(null);
    try {
      const res = await api.upgradeBootloader(routerId, {
        confirm_name: bootloaderConfirmName.trim(),
        reboot: true,
      });
      setShowBootloaderPrompt(false);
      if (res.status === 'rebooting') {
        setRebootStage('rebooting');
        setRebootMsg('RouterBOOT upgraded. Router is rebooting...');
        startReconnectionPolling();
      } else {
        await fetchStatus();
      }
    } catch (err) {
      setError(err.message || 'Bootloader upgrade failed');
    } finally {
      setBootloaderUpgrading(false);
    }
  };

  // Filter changelog lines
  const filteredChangelogLines = useMemo(() => {
    if (!changelog) return [];
    const lines = changelog.split('\n');
    if (!changelogSearch.trim()) return lines;
    const q = changelogSearch.toLowerCase().trim();
    return lines.filter((l) => l.toLowerCase().includes(q));
  }, [changelog, changelogSearch]);

  if (!isOpen) return null;

  const pkg = status?.packages;
  const rb = status?.routerboard;
  const updateAvailable = Boolean(pkg?.update_available);
  const bootloaderAvailable = Boolean(rb?.firmware_available);
  const isNameConfirmed = confirmName.trim() === (routerName || '').trim();
  const isBootloaderNameConfirmed = bootloaderConfirmName.trim() === (routerName || '').trim();

  return (
    <div className="modal-backdrop" onClick={rebootStage === 'idle' ? onClose : undefined}>
      <div
        className="modal-card"
        onClick={e => e.stopPropagation()}
        style={{ maxWidth: 860, width: '95vw', maxHeight: '92vh', display: 'flex', flexDirection: 'column' }}
      >
        <div className="modal-header">
          <div className="modal-title-group">
            <div className="modal-icon"><Package size={20} /></div>
            <div style={{ minWidth: 0 }}>
              <h3>{t('firmware_modal_title')}</h3>
              <div className="modal-subtitle truncate">
                {routerName}{rb?.model ? ` · ${rb.model}` : ''}{rb?.serial_number ? ` · ${rb.serial_number}` : ''}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleCheckUpdates}
              disabled={refreshing || rebootStage !== 'idle'}
              style={{ display: 'flex', alignItems: 'center', gap: 5 }}
            >
              <RefreshCw size={13} className={refreshing ? 'spin' : ''} />
              {t('check_for_updates')}
            </button>
            <button
              className="btn-icon"
              onClick={onClose}
              disabled={rebootStage !== 'idle' && rebootStage !== 'online'}
              aria-label={t('log_close')}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {error && (
            <div className="alert alert-danger" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <AlertTriangle size={14} />{error}
            </div>
          )}

          {rebootStage !== 'idle' ? (
            /* Reconnect state machine: backing_up -> issuing -> rebooting -> online */
            <div style={{ padding: '36px 20px', textAlign: 'center' }}>
              {rebootStage === 'online' ? (
                <CheckCircle2 size={46} style={{ color: 'var(--color-success)' }} />
              ) : (
                <RefreshCw size={46} className="spin" style={{ color: 'var(--color-primary)' }} />
              )}
              <h3 style={{ marginTop: 14, fontSize: 'var(--fs-lg)', fontWeight: 700 }}>
                {rebootStage === 'online' ? t('router_back_online') : t('reboot_in_progress_title')}
              </h3>
              <p style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', marginTop: 6 }}>
                {rebootStage === 'online'
                  ? `${t('firmware_installed_version')}: v${pkg?.installed_version || '?'}`
                  : rebootMsg}
              </p>
              {rebootStage === 'online' && (
                <button type="button" className="btn btn-primary btn-sm" style={{ marginTop: 14 }} onClick={onClose}>
                  {t('done')}
                </button>
              )}
            </div>
          ) : loading && !status ? (
            <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-muted)' }}>
              {t('loading_history')}…
            </div>
          ) : (
            <>
              {/* Two cards: RouterOS packages and the RouterBOOT bootloader */}
              <div style={{ display: 'grid', gridTemplateColumns: rb?.is_routerboard ? '1fr 1fr' : '1fr', gap: 12 }}>
                <div style={{
                  border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)',
                  padding: 12, background: 'var(--bg-secondary)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, fontSize: 'var(--fs-sm)' }}>
                      <Package size={14} />RouterOS
                    </span>
                    <span style={{
                      padding: '1px 8px', borderRadius: 999, fontSize: 'var(--fs-2xs)', fontWeight: 700,
                      background: updateAvailable ? 'rgba(245,158,11,0.15)' : 'rgba(16,185,129,0.12)',
                      color: updateAvailable ? 'var(--color-warning)' : 'var(--color-success)',
                      border: `1px solid ${updateAvailable ? 'rgba(245,158,11,0.3)' : 'rgba(16,185,129,0.3)'}`,
                    }}>
                      {updateAvailable ? t('firmware_update_available') : t('firmware_up_to_date')}
                    </span>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 10, fontFamily: 'var(--font-mono, monospace)' }}>
                    <span style={{ fontSize: 'var(--fs-lg)', fontWeight: 700 }}>{pkg?.installed_version || '—'}</span>
                    {updateAvailable && (
                      <>
                        <ArrowRight size={13} style={{ color: 'var(--text-muted)' }} />
                        <span style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, color: 'var(--color-warning)' }}>
                          {pkg?.latest_version}
                        </span>
                      </>
                    )}
                  </div>

                  <div className="form-group" style={{ marginTop: 10, marginBottom: 0 }}>
                    <label className="form-label">{t('firmware_channel')}</label>
                    <select
                      className="form-select"
                      value={pkg?.channel || 'stable'}
                      disabled={channelUpdating}
                      onChange={e => handleChannelChange(e.target.value)}
                      // `.form-select`'s own `padding: 10px 14px` needs ~38px
                      // of height to fit its text without clipping; shrinking
                      // the box to 30px without also shrinking the padding cut
                      // the option text off at the top. `padding-right` stays
                      // wide enough for the class's dropdown-arrow icon.
                      style={{ height: 30, fontSize: 'var(--fs-sm)', padding: '0 36px 0 10px', lineHeight: '28px' }}
                    >
                      {['stable', 'long-term', 'testing', 'development'].map(c => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                  </div>
                </div>

                {rb?.is_routerboard && (
                  <div style={{
                    border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)',
                    padding: 12, background: 'var(--bg-secondary)',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, fontSize: 'var(--fs-sm)' }}>
                        <Cpu size={14} />RouterBOOT
                      </span>
                      <span style={{
                        padding: '1px 8px', borderRadius: 999, fontSize: 'var(--fs-2xs)', fontWeight: 700,
                        background: bootloaderAvailable ? 'rgba(245,158,11,0.15)' : 'rgba(16,185,129,0.12)',
                        color: bootloaderAvailable ? 'var(--color-warning)' : 'var(--color-success)',
                        border: `1px solid ${bootloaderAvailable ? 'rgba(245,158,11,0.3)' : 'rgba(16,185,129,0.3)'}`,
                      }}>
                        {bootloaderAvailable ? t('firmware_bootloader_upgrade') : t('firmware_bootloader_current')}
                      </span>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 10, fontFamily: 'var(--font-mono, monospace)' }}>
                      <span style={{ fontSize: 'var(--fs-lg)', fontWeight: 700 }}>{rb?.current_firmware || '—'}</span>
                      {bootloaderAvailable && (
                        <>
                          <ArrowRight size={13} style={{ color: 'var(--text-muted)' }} />
                          <span style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, color: 'var(--color-warning)' }}>
                            {rb?.upgrade_firmware}
                          </span>
                        </>
                      )}
                    </div>

                    {/* Standalone bootloader upgrade, for when RouterOS is already current */}
                    {bootloaderAvailable && !updateAvailable && (
                      showBootloaderPrompt ? (
                        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
                          <input
                            type="text"
                            className="form-input"
                            value={bootloaderConfirmName}
                            onChange={e => setBootloaderConfirmName(e.target.value)}
                            placeholder={t('confirm_name_ph', { name: routerName })}
                            style={{ height: 28, fontSize: 'var(--fs-sm)' }}
                          />
                          <div style={{ display: 'flex', gap: 6 }}>
                            <button
                              type="button"
                              className="btn btn-danger btn-sm"
                              style={{ flex: 1 }}
                              disabled={!isBootloaderNameConfirmed || bootloaderUpgrading}
                              onClick={handleUpgradeBootloaderOnly}
                            >
                              {t('firmware_upgrade_bootloader')}
                            </button>
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              onClick={() => setShowBootloaderPrompt(false)}
                            >
                              {t('cancel')}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          style={{ marginTop: 10, width: '100%' }}
                          onClick={() => setShowBootloaderPrompt(true)}
                        >
                          {t('firmware_upgrade_bootloader')}
                        </button>
                      )
                    )}
                  </div>
                )}
              </div>

              {/* Upstream release notes */}
              {updateAvailable && (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 6 }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, fontSize: 'var(--fs-sm)' }}>
                      <Terminal size={14} />{t('firmware_changelog')} v{pkg?.latest_version}
                    </span>
                    <div style={{ position: 'relative', width: 200 }}>
                      <Search size={12} style={{
                        position: 'absolute', left: 7, top: '50%',
                        transform: 'translateY(-50%)', color: 'var(--text-muted)',
                      }} />
                      <input
                        type="text"
                        className="form-input"
                        value={changelogSearch}
                        onChange={e => setChangelogSearch(e.target.value)}
                        placeholder={t('firmware_changelog_search')}
                        style={{ paddingLeft: 24, height: 26, fontSize: 'var(--fs-xs)', width: '100%' }}
                      />
                    </div>
                  </div>
                  <div style={{
                    maxHeight: 190, overflowY: 'auto',
                    border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)',
                    padding: '8px 10px', background: 'var(--bg-secondary)',
                    fontFamily: 'var(--font-mono, ui-monospace, Menlo, monospace)',
                    fontSize: 'var(--fs-2xs)', lineHeight: 1.6, whiteSpace: 'pre-wrap',
                  }}>
                    {changelogLoading
                      ? `${t('loading_history')}…`
                      : filteredChangelogLines.length === 0
                        ? t('firmware_changelog_none')
                        : filteredChangelogLines.join('\n')}
                  </div>
                </div>
              )}

              {/* Confirmation gate */}
              {updateAvailable && (
                <div style={{
                  border: '1px solid var(--color-danger)', borderRadius: 'var(--radius-sm)',
                  padding: 12, display: 'flex', flexDirection: 'column', gap: 8,
                }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 'var(--fs-sm)' }}>
                    <input
                      type="checkbox"
                      checked={stageBootloader}
                      onChange={e => setStageBootloader(e.target.checked)}
                      style={{ width: 16, height: 16, accentColor: 'var(--color-primary)', cursor: 'pointer' }}
                    />
                    {t('stage_bootloader_checkbox')}
                  </label>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
                    <ShieldCheck size={13} style={{ color: 'var(--color-success)', flexShrink: 0 }} />
                    {t('firmware_backup_notice')}
                  </div>

                  <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                    <div className="form-group" style={{ flex: 1, marginBottom: 0 }}>
                      <label className="form-label">{t('confirm_name_label', { name: routerName })}</label>
                      <input
                        type="text"
                        className="form-input"
                        value={confirmName}
                        onChange={e => setConfirmName(e.target.value)}
                        placeholder={t('confirm_name_ph', { name: routerName })}
                        style={{ height: 30, fontSize: 'var(--fs-sm)' }}
                      />
                    </div>
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      disabled={!isNameConfirmed}
                      onClick={handleExecuteUpgrade}
                      style={{ height: 30 }}
                    >
                      {t('upgrade_and_reboot')}
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { X, Network, Plus, Trash2, RotateCcw, Check, AlertCircle } from 'lucide-react';

const DEFAULT_RFC1918 = ['192.168.0.0/16', '10.0.0.0/8', '172.16.0.0/12'];

export function isValidCidrOrIp(str) {
  if (!str) return false;
  const trimmed = str.trim();
  const cidrRegex = /^(\d{1,3}\.){3}\d{1,3}(\/([0-9]|[1-2][0-9]|3[0-2]))?$/;
  if (!cidrRegex.test(trimmed)) return false;
  const [ipPart] = trimmed.split('/');
  const octets = ipPart.split('.').map(Number);
  return octets.every(o => Number.isInteger(o) && o >= 0 && o <= 255);
}

export function parseNetworksList(rawString) {
  if (!rawString) return DEFAULT_RFC1918;
  const parts = rawString.replace(/\n/g, ',').split(',');
  const unique = [];
  for (const p of parts) {
    const trimmed = p.trim();
    if (trimmed && !unique.includes(trimmed)) {
      unique.push(trimmed);
    }
  }
  return unique.length > 0 ? unique : DEFAULT_RFC1918;
}

export function PauseNetworksModal({ isOpen, onClose, currentNetworks = '', onSave }) {
  const { t } = useI18n();
  const [networks, setNetworks] = useState([]);
  const [newSubnet, setNewSubnet] = useState('');
  const [error, setError] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setNetworks(parseNetworksList(currentNetworks));
      setNewSubnet('');
      setError('');
    }
  }, [isOpen, currentNetworks]);

  if (!isOpen) return null;

  const handleAdd = (e) => {
    e?.preventDefault();
    setError('');
    const trimmed = newSubnet.trim();
    if (!trimmed) return;

    if (!isValidCidrOrIp(trimmed)) {
      setError(t('invalid_network_cidr'));
      return;
    }

    if (networks.includes(trimmed)) {
      setError(t('network_already_added'));
      return;
    }

    setNetworks([...networks, trimmed]);
    setNewSubnet('');
  };

  const handleRemove = (netToRemove) => {
    setNetworks(networks.filter(n => n !== netToRemove));
    setError('');
  };

  const handleReset = () => {
    setNetworks([...DEFAULT_RFC1918]);
    setError('');
  };

  const handleSave = async () => {
    setIsSaving(true);
    setError('');
    try {
      const formatted = networks.join(', ');
      await onSave(formatted);
      onClose();
    } catch (err) {
      setError(err?.message || 'Failed to save allowed networks');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content"
        onClick={e => e.stopPropagation()}
        style={{ maxWidth: 560, width: '100%', borderRadius: 'var(--radius-lg, 12px)' }}
      >
        <div className="modal-header" style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-color)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              width: 36,
              height: 36,
              borderRadius: 'var(--radius-md)',
              background: 'var(--color-primary-light, rgba(99, 102, 241, 0.12))',
              color: 'var(--color-primary)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0
            }}>
              <Network size={20} />
            </div>
            <div>
              <h2 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0 }}>
                {t('pause_networks_modal_title')}
              </h2>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginTop: 2 }}>
                {t('pause_networks_modal_desc')}
              </div>
            </div>
          </div>
          <button className="btn-icon" onClick={onClose} title={t('close')}>
            <X size={16} />
          </button>
        </div>

        <div className="modal-body" style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* List of configured networks */}
          <div>
            <div style={{
              fontSize: 'var(--fs-xs)',
              fontWeight: 600,
              marginBottom: 8,
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center'
            }}>
              <span style={{ color: 'var(--text-secondary)' }}>
                {t('pause_networks_title')} ({networks.length})
              </span>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={handleReset}
                style={{ fontSize: 'var(--fs-2xs)', padding: '2px 8px', height: 24, display: 'inline-flex', alignItems: 'center' }}
                title={t('reset_to_defaults')}
              >
                <RotateCcw size={11} style={{ marginRight: 4 }} />
                {t('reset_to_defaults')}
              </button>
            </div>

            {networks.length === 0 ? (
              <div className="alert alert-warning" style={{ fontSize: 'var(--fs-xs)', padding: '10px 14px' }}>
                <AlertCircle size={14} style={{ marginRight: 6, flexShrink: 0 }} />
                <span>{t('no_networks_configured')}</span>
              </div>
            ) : (
              <div
                className="list-box"
                style={{
                  maxHeight: 220,
                  overflowY: 'auto',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--bg-secondary)'
                }}
              >
                {networks.map((net, idx) => {
                  const parts = net.split('/');
                  const mask = parts.length > 1 ? `/${parts[1]}` : '/32';
                  return (
                    <div
                      key={net}
                      className="list-row"
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        padding: '8px 12px',
                        borderBottom: idx < networks.length - 1 ? '1px solid var(--border-color)' : 'none'
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Network size={13} style={{ color: 'var(--color-primary)', opacity: 0.8 }} />
                        <span className="font-mono" style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
                          {net}
                        </span>
                        <span
                          style={{
                            fontSize: '10px',
                            fontWeight: 600,
                            padding: '1px 5px',
                            borderRadius: 'var(--radius-xs, 4px)',
                            background: 'var(--bg-tertiary, rgba(255,255,255,0.06))',
                            color: 'var(--text-muted)'
                          }}
                        >
                          {mask}
                        </span>
                      </div>
                      <button
                        type="button"
                        className="btn-icon"
                        onClick={() => handleRemove(net)}
                        style={{ width: 26, height: 26, color: 'var(--color-danger)', borderRadius: 'var(--radius-xs)' }}
                        title={t('delete')}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Add new subnet input */}
          <form onSubmit={handleAdd}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                type="text"
                className="form-input font-mono"
                placeholder={t('add_network_placeholder')}
                value={newSubnet}
                onChange={e => setNewSubnet(e.target.value)}
                style={{ fontSize: 'var(--fs-sm)', flex: 1, height: 36 }}
              />
              <button
                type="submit"
                className="btn btn-secondary btn-sm"
                disabled={!newSubnet.trim()}
                style={{ flexShrink: 0, height: 36, padding: '0 14px', display: 'inline-flex', alignItems: 'center' }}
              >
                <Plus size={14} style={{ marginRight: 4 }} />
                {t('add')}
              </button>
            </div>
          </form>

          {error && (
            <div className="alert alert-danger" style={{ fontSize: 'var(--fs-xs)', padding: '8px 12px' }}>
              <AlertCircle size={14} style={{ marginRight: 6, flexShrink: 0 }} />
              <span>{error}</span>
            </div>
          )}
        </div>

        <div className="modal-footer" style={{ padding: '14px 20px', borderTop: '1px solid var(--border-color)', display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={isSaving}>
            {t('cancel')}
          </button>
          <button type="button" className="btn btn-primary" onClick={handleSave} disabled={isSaving}>
            <Check size={14} style={{ marginRight: 4 }} />
            {t('save')}
          </button>
        </div>
      </div>
    </div>
  );
}


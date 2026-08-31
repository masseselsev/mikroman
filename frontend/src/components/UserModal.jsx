import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { X, User as UserIcon, ChevronRight, ChevronDown, Trash2, Scissors, Eraser } from 'lucide-react';
import { RateLimitInputs, LimitModeToggle } from './RateLimitInputs';
import { api } from '../api/client';

const SPEED_PRESETS = [
  { label: '⚡ Unlimited (Max speed)', value: 'unlimited' },
  { label: '↓ 15 Mbps (Down) / ↑ 5 Mbps (Up) — Light', value: '5M/15M' },
  { label: '↓ 30 Mbps (Down) / ↑ 10 Mbps (Up) — Standard', value: '10M/30M' },
  { label: '↓ 50 Mbps (Down) / ↑ 25 Mbps (Up) — Fast', value: '25M/50M' },
  { label: '↓ 50 Mbps / ↑ 50 Mbps — Symmetric', value: '50M/50M' },
  { label: '↓ 100 Mbps (Down) / ↑ 50 Mbps (Up) — Super', value: '50M/100M' },
  { label: '↓ 100 Mbps / ↑ 100 Mbps — Symmetric', value: '100M/100M' },
  { label: '↓ 200 Mbps (Down) / ↑ 100 Mbps (Up) — Ultra', value: '100M/200M' },
  { label: '✏️ Custom manual limit...', value: 'custom' },
];

/**
 * One assigned device inside the profile editor, with an expandable panel for
 * the maintenance actions the list itself cannot express:
 *   - clear a stale IP (a lease that is no longer valid);
 *   - split a wrongly-merged MAC back into its own record (two identical phones
 *     that discovery folded together) - past coalesced traffic can't be
 *     divided, only future traffic on the address is tracked separately;
 *   - delete the record for good. The profile keeps its traffic totals; only
 *     the device row and its own history / rollups go.
 *
 * These act immediately (not on Save) and call `onChanged` so the surrounding
 * lists refresh.
 */
function DeviceEditRow({ dev, checked, onToggle, onChanged, onLocalPatch, onLocalRemove }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  // Addresses this device has worn before, from its history - the candidates to
  // split off. The current address is never a candidate.
  const priorMacs = [...new Set(
    (dev.history || [])
      .filter(h => ['mac_rotated', 'merged', 'split'].includes(h.event_type) && h.mac_address)
      .map(h => h.mac_address)
      .filter(m => m.toUpperCase() !== (dev.mac_address || '').toUpperCase())
  )];

  const run = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      if (onChanged) onChanged();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const clearIp = () => run(async () => {
    await api.updateDevice(dev.id, { ip_address: null });
    onLocalPatch(dev.id, { ip_address: null });
  });

  const splitMac = (mac) => run(async () => {
    await api.splitDevice(dev.id, mac);
  });

  const deleteDevice = () => {
    if (!window.confirm(t('dev_delete_confirm'))) return;
    run(async () => {
      await api.deleteDevice(dev.id);
      onLocalRemove(dev.id);
    });
  };

  const label = dev.custom_name || dev.hostname || dev.vendor || 'Device';

  return (
    <div className={`list-row-wrap${checked ? ' is-selected' : ''}`}>
      <div className="list-row" style={{ justifyContent: 'flex-start' }}>
        <input type="checkbox" checked={checked} onChange={() => onToggle(dev.mac_address)} />
        <div className="truncate" style={{ flex: 1, minWidth: 0 }}>
          <span style={{ fontWeight: 600 }}>{label}</span>
          <span className="font-mono" style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginLeft: 8 }}>
            {dev.ip_address || dev.mac_address}
          </span>
        </div>
        <button
          type="button"
          className="btn-icon"
          style={{ width: 24, height: 24, flexShrink: 0 }}
          onClick={() => setOpen(o => !o)}
          title={t('dev_edit_toggle')}
        >
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </button>
      </div>

      {open && (
        <div className="dev-edit-panel">
          <div className="dev-edit-line">
            <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>
              {dev.mac_address}{dev.ip_address ? ` · ${dev.ip_address}` : ''}
            </span>
            {dev.ip_address && (
              <button type="button" className="btn btn-ghost btn-xs" disabled={busy} onClick={clearIp}>
                <Eraser size={11} /> {t('dev_clear_ip')}
              </button>
            )}
          </div>

          {priorMacs.length > 0 && (
            <div className="dev-edit-block">
              <div className="dev-edit-block-title">{t('dev_split_title')}</div>
              {priorMacs.map(m => (
                <div key={m} className="dev-edit-line">
                  <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{m}</span>
                  <button type="button" className="btn btn-ghost btn-xs" disabled={busy} onClick={() => splitMac(m)}>
                    <Scissors size={11} /> {t('dev_split_btn')}
                  </button>
                </div>
              ))}
              <div className="dev-edit-hint">{t('dev_split_hint')}</div>
            </div>
          )}

          <div className="dev-edit-line" style={{ justifyContent: 'space-between' }}>
            <span className="dev-edit-hint">{t('dev_delete_hint')}</span>
            <button type="button" className="btn btn-danger btn-xs" disabled={busy} onClick={deleteDevice}>
              <Trash2 size={11} /> {t('dev_delete_btn')}
            </button>
          </div>

          {error && <div className="dev-edit-error">{error}</div>}
        </div>
      )}
    </div>
  );
}

export function UserModal({ user, unassignedDevices = [], isOpen, onClose, onSave, onDeviceChanged }) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [speedLimit, setSpeedLimit] = useState('unlimited');
  const [isCustomMode, setIsCustomMode] = useState(false);
  const [customDown, setCustomDown] = useState('');
  const [customUp, setCustomUp] = useState('');
  const [nameError, setNameError] = useState('');
  const [selectedMacs, setSelectedMacs] = useState([]);
  const [ownDevices, setOwnDevices] = useState([]);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (user) {
      setName(user.name || '');
      setNameError('');
      const limit = user.speed_limit || 'unlimited';
      setSpeedLimit(limit);
      const isKnown = SPEED_PRESETS.some(p => p.value === limit);
      if (!isKnown && limit !== 'unlimited') {
        setIsCustomMode(true);
        if (limit.includes('/')) {
          const [up, down] = limit.split('/');
          setCustomUp(up);
          setCustomDown(down);
        } else {
          setCustomUp(limit);
          setCustomDown(limit);
        }
      } else {
        setIsCustomMode(false);
      }
      setSelectedMacs((user.devices || []).map(d => d.mac_address));
      setOwnDevices(user.devices || []);
    } else {
      setName('');
      setNameError('');
      setSpeedLimit('unlimited');
      setIsCustomMode(false);
      setCustomDown('50M');
      setCustomUp('20M');
      setSelectedMacs([]);
      setOwnDevices([]);
    }
  }, [user, isOpen]);

  if (!isOpen) return null;

  const handleNameChange = (val) => {
    setName(val);
    if (val && !/^[a-zA-Z0-9_\-\. ]+$/.test(val)) {
      setNameError('Only English letters, numbers, spaces, hyphens, or underscores are allowed.');
    } else {
      setNameError('');
    }
  };

  const handleToggleMac = (mac) => {
    setSelectedMacs(prev =>
      prev.includes(mac) ? prev.filter(m => m !== mac) : [...prev, mac]
    );
  };

  const handleSpeedSelect = (val) => {
    if (val === 'custom') {
      setIsCustomMode(true);
      if (!customDown && !customUp) {
        setCustomDown('50M');
        setCustomUp('20M');
      }
    } else {
      setIsCustomMode(false);
      setSpeedLimit(val);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim() || nameError) return;

    let finalLimit = speedLimit;
    if (isCustomMode) {
      let down = customDown.trim();
      let up = customUp.trim();
      if (!down && !up) {
        finalLimit = 'unlimited';
      } else {
        if (!down) down = up;
        if (!up) up = down;
        if (/^\d+$/.test(down)) down += 'M';
        if (/^\d+$/.test(up)) up += 'M';
        finalLimit = `${up}/${down}`; // RouterOS format: upload/download
      }
    }

    setIsSaving(true);
    try {
      await onSave({
        name: name.trim(),
        speed_limit: finalLimit,
        device_macs: selectedMacs
      });
      onClose();
    } finally {
      setIsSaving(false);
    }
  };

  // Local edits to this profile's own devices take effect immediately (clear
  // IP, split, delete) and are mirrored here so the list stays right without a
  // full reload of the modal.
  const patchLocalDevice = (id, patch) =>
    setOwnDevices(list => list.map(d => (d.id === id ? { ...d, ...patch } : d)));
  const removeLocalDevice = (id) =>
    setOwnDevices(list => {
      const gone = list.find(d => d.id === id);
      if (gone) setSelectedMacs(macs => macs.filter(m => m !== gone.mac_address));
      return list.filter(d => d.id !== id);
    });

  const assignedIds = new Set(ownDevices.map(d => d.id));
  const extraUnassigned = unassignedDevices.filter(d => !assignedIds.has(d.id));
  const hasAnyDevice = ownDevices.length > 0 || extraUnassigned.length > 0;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="panel-title">
            <UserIcon size={18} />
            {user ? t('edit_user') : t('add_user')}
          </div>
          <button className="btn-icon" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <div className="form-group">
              <label className="form-label">{t('user_name')}</label>
              <input
                type="text"
                className="form-input"
                style={{ borderColor: nameError ? 'var(--color-danger)' : undefined }}
                value={name}
                onChange={e => handleNameChange(e.target.value)}
                placeholder="e.g. Alex, Kids, Smart Home"
                pattern="^[a-zA-Z0-9_\-\. ]+$"
                required
                autoFocus
              />
              {nameError && (
                <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--color-danger)', marginTop: 4 }}>
                  {nameError}
                </div>
              )}
            </div>

            <div className="form-group">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <label className="form-label" style={{ marginBottom: 0, fontWeight: 600 }}>{t('speed_limit')}</label>
                <LimitModeToggle
                  isCustom={isCustomMode}
                  onPresets={() => setIsCustomMode(false)}
                  onCustom={() => {
                    setIsCustomMode(true);
                    if (!customDown && !customUp) {
                      setCustomDown('50M');
                      setCustomUp('20M');
                    }
                  }}
                />
              </div>

              {!isCustomMode ? (
                <select
                  className="form-select"
                  value={speedLimit}
                  onChange={e => handleSpeedSelect(e.target.value)}
                >
                  {SPEED_PRESETS.map(p => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              ) : (
                <RateLimitInputs
                  down={customDown}
                  up={customUp}
                  onChangeDown={setCustomDown}
                  onChangeUp={setCustomUp}
                  downPlaceholder="e.g. 50M or 100M"
                  upPlaceholder="e.g. 20M or 50M"
                  hint={t('rate_units_hint')}
                />
              )}
            </div>

            <div className="form-group">
              <label className="form-label">{t('assigned_devices')}</label>
              <div className="list-box" style={{ maxHeight: 240 }}>
                {!hasAnyDevice ? (
                  <div className="empty-note">{t('no_devices_detected')}</div>
                ) : (
                  <>
                    {ownDevices.map(dev => (
                      <DeviceEditRow
                        key={dev.id || dev.mac_address}
                        dev={dev}
                        checked={selectedMacs.includes(dev.mac_address)}
                        onToggle={handleToggleMac}
                        onChanged={onDeviceChanged}
                        onLocalPatch={patchLocalDevice}
                        onLocalRemove={removeLocalDevice}
                      />
                    ))}
                    {extraUnassigned.map(dev => {
                      const isChecked = selectedMacs.includes(dev.mac_address);
                      return (
                        <label
                          key={dev.id || dev.mac_address}
                          className={`list-row${isChecked ? ' is-selected' : ''}`}
                          style={{ justifyContent: 'flex-start' }}
                        >
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() => handleToggleMac(dev.mac_address)}
                          />
                          <div className="truncate" style={{ flex: 1, minWidth: 0 }}>
                            <span style={{ fontWeight: 600 }}>{dev.custom_name || dev.hostname || dev.vendor || 'Device'}</span>
                            <span className="font-mono" style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginLeft: 8 }}>
                              {dev.ip_address || dev.mac_address}
                            </span>
                          </div>
                        </label>
                      );
                    })}
                  </>
                )}
              </div>
              <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginTop: 5 }}>
                {t('assigned_devices_hint')}
              </div>
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
              {t('cancel')}
            </button>
            <button type="submit" className="btn btn-primary btn-sm" disabled={isSaving || !name.trim()}>
              {t('save')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

import React, { useState, useEffect } from 'react';
import { useI18n } from './context/I18nContext';
import { useWebSocketTelemetry } from './hooks/useWebSocketTelemetry';
import { api } from './api/client';
import { Navbar } from './components/Navbar';
import { TelemetryBar } from './components/TelemetryBar';
import { UserCard } from './components/UserCard';
import { UserModal } from './components/UserModal';
import { DeviceInbox } from './components/DeviceInbox';
import { MetricCharts } from './components/MetricCharts';
import { TrafficAnalytics } from './components/TrafficAnalytics';
import { SettingsModal } from './components/SettingsModal';
import { formatBytes } from './utils/formatters';
import { SetupWizard } from './components/SetupWizard';
import { Users, Laptop, Activity, BarChart2, Plus, AlertCircle, EyeOff, ChevronDown, ChevronRight, AlertTriangle } from 'lucide-react';

export function App() {
  const { t } = useI18n();

  const [routers, setRouters] = useState([]);
  const [activeRouter, setActiveRouter] = useState(null);
  const [activeTab, setActiveTab] = useState('users');
  const [users, setUsers] = useState([]);
  const [unassignedDevices, setUnassignedDevices] = useState([]);
  const [interfaces, setInterfaces] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isScanning, setIsScanning] = useState(false);
  const [showHiddenDevices, setShowHiddenDevices] = useState(false);

  // Modals state
  const [userModalOpen, setUserModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState(null);
  const [settingsModalOpen, setSettingsModalOpen] = useState(false);

  // Hook up WebSocket telemetry with active router ID
  const { telemetry, isConnected } = useWebSocketTelemetry(activeRouter?.id);

  const loadData = async () => {
    try {
      const [routersRes, usersRes, devsRes, ifacesRes, alertsRes] = await Promise.all([
        api.getRouters().catch(() => ({ data: [] })),
        api.getUsers().catch(() => ({ data: [] })),
        api.getDevices(true).catch(() => ({ data: [] })),
        api.getInterfaces().catch(() => ({ data: [] })),
        api.getAlerts().catch(() => ({ data: [] }))
      ]);

      const routerList = routersRes.data || [];
      setRouters(routerList);

      if (!activeRouter && routerList.length > 0) {
        const currentDefault = routerList.find(r => r.is_default) || routerList[0] || null;
        setActiveRouter(currentDefault);
      }

      setUsers(usersRes.data || []);
      setUnassignedDevices(devsRes.data || []);
      setInterfaces(ifacesRes.data || []);
      setAlerts(alertsRes.data || []);
    } catch (err) {
      console.error('Failed to load initial data', err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    // Poll data in background every 6 seconds to catch new active devices & IP shifts
    const pollInterval = setInterval(() => {
      loadData();
    }, 6000);
    return () => clearInterval(pollInterval);
  }, []);

  const [interfacesOpen, setInterfacesOpen] = useState(false);
  const [draggedUserId, setDraggedUserId] = useState(null);

  /** Move the dragged card in front of the card it was dropped on. */
  const handleDropOnUser = async (targetUserId) => {
    if (!draggedUserId || draggedUserId === targetUserId) return;

    const ordered = [...users];
    const from = ordered.findIndex(u => u.id === draggedUserId);
    const to = ordered.findIndex(u => u.id === targetUserId);
    if (from < 0 || to < 0) return;

    const [moved] = ordered.splice(from, 1);
    ordered.splice(to, 0, moved);
    // Applied locally first so the card follows the cursor without waiting for
    // the round trip; the reload afterwards confirms the persisted order.
    setUsers(ordered);
    setDraggedUserId(null);
    try {
      await api.reorderUsers(ordered.map(u => u.id));
    } catch (err) {
      console.error('Failed to persist card order:', err);
      await loadData();
    }
  };


  // Link faults drive the collapsed header, so a failing cable is visible
  // without expanding the section.
  const interfaceSummary = {
    running: interfaces.filter(i => i.running).length,
    faulty: interfaces
      .map(i => ({
        name: i.name,
        errors: (i.rx_error || 0) + (i.tx_error || 0),
        drops: (i.rx_drop || 0) + (i.tx_drop || 0),
      }))
      .filter(i => i.errors > 0 || i.drops > 0)
      .sort((a, b) => (b.errors - a.errors) || (b.drops - a.drops)),
  };

  // Sum of all profiles' traffic today, used to show each profile's share.
  // Derived from the profiles themselves so the denominator always matches the
  // numerators shown on the cards.
  const gatewayTodayTotal = users.reduce(
    (sum, u) => sum + (u.bytes_today_in || 0) + (u.bytes_today_out || 0),
    0
  );

  // Merge live WebSocket telemetry into users state for smooth animation
  useEffect(() => {
    if (telemetry?.users && users.length > 0) {
      const telemetryMap = new Map(telemetry.users.map(u => [u.user_id, u]));
      setUsers(prevUsers =>
        prevUsers.map(u => {
          const live = telemetryMap.get(u.id);
          if (live) {
            // Per-device live figures ride along in the same telemetry frame,
            // so device rows animate at the same cadence as the user totals.
            const perDevice = live.devices || {};
            return {
              ...u,
              current_rate_in: live.current_rate_in,
              current_rate_out: live.current_rate_out,
              bytes_today_in: live.bytes_in,
              bytes_today_out: live.bytes_out,
              is_paused: live.is_paused,
              devices: (u.devices || []).map(d => {
                const dm = perDevice[d.id];
                return dm ? { ...d, ...dm } : d;
              })
            };
          }
          return u;
        })
      );
    }
  }, [telemetry]);

  const handleSelectRouter = (router) => {
    setActiveRouter(router);
  };

  const handleScan = async () => {
    setIsScanning(true);
    try {
      await api.scanNetwork();
      await loadData();
    } catch (err) {
      console.error('Scan error:', err);
    } finally {
      setIsScanning(false);
    }
  };

  const handleCreateOrUpdateUser = async (userData) => {
    if (editingUser) {
      await api.updateUser(editingUser.id, userData);
    } else {
      await api.createUser(userData);
    }
    await loadData();
  };

  const handleDeleteUser = async (userId) => {
    if (window.confirm(t('confirm_delete_user'))) {
      await api.deleteUser(userId);
      await loadData();
    }
  };

  const handleLimitChange = async (userId, newLimit) => {
    await api.setUserLimit(userId, newLimit);
    setUsers(prev => prev.map(u => u.id === userId ? { ...u, speed_limit: newLimit } : u));
  };

  const handlePauseToggle = async (userId, isPaused) => {
    await api.toggleUserPause(userId, isPaused);
    setUsers(prev => prev.map(u => u.id === userId ? { ...u, is_paused: isPaused } : u));
  };

  const handleAssignDevice = async (deviceId, userId) => {
    await api.updateDevice(deviceId, { user_id: userId });
    await loadData();
  };

  const handleReboot = async () => {
    if (window.confirm(t('confirm_reboot'))) {
      await api.rebootRouter();
      setSettingsModalOpen(false);
    }
  };

  // If no routers exist and not loading, show First-Run Setup Wizard!
  if (!isLoading && routers.length === 0) {
    return <SetupWizard onComplete={loadData} />;
  }

  return (
    <div className="app-container">
      <Navbar
        isConnected={isConnected}
        routerInfo={telemetry?.router}
        routers={routers}
        activeRouter={activeRouter}
        onSelectRouter={handleSelectRouter}
        onOpenSettings={() => setSettingsModalOpen(true)}
        onAddRouter={() => setSettingsModalOpen(true)}
      />

      <main className="main-content">
        {/* Router Live Telemetry Bar */}
        <TelemetryBar
          router={telemetry?.router}
          activeRouter={activeRouter}
          interfaces={interfaces}
        />

        {/* Tab Navigation */}
        <div className="nav-tabs">
          <button
            className={`nav-tab ${activeTab === 'users' ? 'active' : ''}`}
            onClick={() => setActiveTab('users')}
          >
            <Users size={18} />
            {t('tab_users')}
            <span className="badge badge-neutral" style={{ padding: '1px 6px', fontSize: '0.7rem' }}>
              {users.length}
            </span>
          </button>

          <button
            className={`nav-tab ${activeTab === 'analytics' ? 'active' : ''}`}
            onClick={() => setActiveTab('analytics')}
          >
            <BarChart2 size={18} />
            {t('tab_analytics')}
          </button>

          <button
            className={`nav-tab ${activeTab === 'devices' ? 'active' : ''}`}
            onClick={() => setActiveTab('devices')}
          >
            <Laptop size={18} />
            {t('tab_devices')}
            {unassignedDevices.length > 0 && (
              <span className="badge badge-warning" style={{ padding: '1px 6px', fontSize: '0.7rem' }}>
                {unassignedDevices.length}
              </span>
            )}
          </button>

          <button
            className={`nav-tab ${activeTab === 'health' ? 'active' : ''}`}
            onClick={() => setActiveTab('health')}
          >
            <Activity size={18} />
            {t('tab_health')}
          </button>
        </div>

        {/* Tab Content: Users */}
        {activeTab === 'users' && (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20, flexWrap: 'wrap', gap: 10 }}>
              <div>
                <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }}>{t('tab_users')}</h2>
                <p style={{ fontSize: '0.825rem', color: 'var(--text-muted)' }}>
                  Organize network clients into users with per-user bandwidth limiting and instant pause controls.
                </p>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {/* Show Hidden Devices Checkbox */}
                <label style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: '0.775rem',
                  color: 'var(--text-muted)',
                  cursor: 'pointer',
                  userSelect: 'none',
                  background: 'var(--bg-card)',
                  padding: '6px 12px',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-color)',
                  height: 34
                }}>
                  <input
                    type="checkbox"
                    checked={showHiddenDevices}
                    onChange={e => setShowHiddenDevices(e.target.checked)}
                    style={{ width: 14, height: 14, cursor: 'pointer', accentColor: 'var(--color-primary)' }}
                  />
                  <EyeOff size={13} style={{ color: showHiddenDevices ? 'var(--color-primary)' : 'var(--text-muted)' }} />
                  {t('show_hidden_devices')}
                </label>

                <button
                  className="btn btn-primary"
                  onClick={() => {
                    setEditingUser(null);
                    setUserModalOpen(true);
                  }}
                >
                  <Plus size={16} />
                  {t('add_user')}
                </button>
              </div>
            </div>

            {users.length === 0 ? (
              <div className="card" style={{ textAlign: 'center', padding: '50px 20px', color: 'var(--text-muted)' }}>
                <Users size={40} style={{ margin: '0 auto 12px auto', opacity: 0.5 }} />
                <div style={{ fontSize: '1.1rem', fontWeight: 600, marginBottom: 6 }}>{t('no_users_found')}</div>
                <button
                  className="btn btn-primary btn-sm"
                  style={{ marginTop: 14 }}
                  onClick={() => {
                    setEditingUser(null);
                    setUserModalOpen(true);
                  }}
                >
                  <Plus size={14} />
                  {t('add_user')}
                </button>
              </div>
            ) : (
              <div className="grid-users">
                {users.map((user, index) => (
                  <div
                    key={user.id}
                    draggable
                    onDragStart={() => setDraggedUserId(user.id)}
                    onDragEnd={() => setDraggedUserId(null)}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={() => handleDropOnUser(user.id)}
                    style={{
                      // The dragged card fades so the drop target stays readable.
                      opacity: draggedUserId === user.id ? 0.4 : 1,
                      transition: 'opacity 0.15s ease'
                    }}
                  >
                  <UserCard
                    user={user}
                    dragIndex={index}
                    showHidden={showHiddenDevices}
                    onEdit={(u) => {
                      setEditingUser(u);
                      setUserModalOpen(true);
                    }}
                    onDelete={handleDeleteUser}
                    onLimitChange={handleLimitChange}
                    onPauseToggle={handlePauseToggle}
                    onUpdate={loadData}
                    gatewayTotal={gatewayTodayTotal}
                  />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Tab Content: Historical Traffic Analytics */}
        {activeTab === 'analytics' && (
          <TrafficAnalytics activeRouter={activeRouter} />
        )}

        {/* Tab Content: Unassigned Devices */}
        {activeTab === 'devices' && (
          <DeviceInbox
            devices={unassignedDevices}
            users={users}
            onAssign={handleAssignDevice}
            onScan={handleScan}
            isScanning={isScanning}
          />
        )}

        {/* Tab Content: Router Health & Interfaces */}
        {activeTab === 'health' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            {/* Interactive Hardware & Bandwidth Charts */}
            <MetricCharts activeRouterId={activeRouter?.id} />

            <div>
              {/* Collapsed by default: on a router with a dozen ports this is
                  mostly empty cards. Faults are summarised on the header so a
                  problem is still visible without expanding. */}
              <button
                type="button"
                onClick={() => setInterfacesOpen(o => !o)}
                style={{
                  width: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  background: 'transparent',
                  border: 'none',
                  padding: 0,
                  marginBottom: interfacesOpen ? 14 : 0,
                  cursor: 'pointer',
                  color: 'inherit',
                  textAlign: 'left'
                }}
              >
                {interfacesOpen ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }}>{t('network_interfaces')}</h2>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  {interfaceSummary.running}/{interfaces.length} {t('status_running').toLowerCase()}
                </span>
                <span style={{ flex: 1 }} />
                {interfaceSummary.faulty.length > 0 ? (
                  <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    {interfaceSummary.faulty.slice(0, 4).map(f => (
                      <span
                        key={f.name}
                        className="badge badge-chip badge-chip-warn"
                        style={{
                          color: f.errors > 0 ? 'var(--color-danger)' : 'var(--color-warning)',
                          borderColor: f.errors > 0 ? 'rgba(239,68,68,0.35)' : 'rgba(234,179,8,0.35)'
                        }}
                        title={`${f.name}: ${f.errors} ${t('err_label')}, ${f.drops} ${t('drops_label')}`}
                      >
                        <AlertTriangle size={10} style={{ marginRight: 3 }} />
                        {f.name} {f.errors > 0 ? `${t('err_label')} ${f.errors}` : `${t('drops_label')} ${f.drops}`}
                      </span>
                    ))}
                    {interfaceSummary.faulty.length > 4 && (
                      <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                        +{interfaceSummary.faulty.length - 4}
                      </span>
                    )}
                  </span>
                ) : (
                  <span className="badge badge-success" style={{ fontSize: '0.65rem' }}>
                    {t('no_link_faults')}
                  </span>
                )}
              </button>

              {interfacesOpen && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
                {interfaces.map(iface => {
                  // Errors and drops are the earliest warning of a failing link,
                  // so they are called out rather than buried.
                  const errors = (iface.rx_error || 0) + (iface.tx_error || 0);
                  const drops = (iface.rx_drop || 0) + (iface.tx_drop || 0);
                  return (
                    <div key={iface.id || iface.name} className="card" style={{ padding: '11px 13px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 7 }}>
                        <span style={{ fontWeight: 700, fontSize: '0.9rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {iface.name}
                        </span>
                        <span className={`badge ${iface.running ? 'badge-success' : 'badge-neutral'}`}>
                          {iface.running ? t('status_running') : t('status_down')}
                        </span>
                      </div>

                      <div style={{ display: 'flex', gap: 14, marginBottom: 6 }}>
                        <div>
                          <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontWeight: 600 }}>RX</div>
                          <div className="font-mono" style={{ fontSize: '0.85rem', fontWeight: 700, color: 'var(--color-success)' }}>
                            {formatBytes(iface.rx_byte || 0)}
                          </div>
                        </div>
                        <div>
                          <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontWeight: 600 }}>TX</div>
                          <div className="font-mono" style={{ fontSize: '0.85rem', fontWeight: 700, color: 'var(--color-primary)' }}>
                            {formatBytes(iface.tx_byte || 0)}
                          </div>
                        </div>
                      </div>

                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        fontSize: '0.66rem',
                        color: 'var(--text-muted)',
                        borderTop: '1px solid var(--border-color)',
                        paddingTop: 6,
                        flexWrap: 'wrap'
                      }}>
                        <span>{iface.type || 'ethernet'}</span>
                        {iface.mtu && <span className="font-mono">MTU {iface.mtu}</span>}
                        <span style={{ flex: 1 }} />
                        <span className="font-mono" style={{ color: errors > 0 ? 'var(--color-danger)' : 'var(--text-muted)' }}>
                          {t('err_label')} {errors.toLocaleString()}
                        </span>
                        <span className="font-mono" style={{ color: drops > 0 ? 'var(--color-warning)' : 'var(--text-muted)' }}>
                          {t('drops_label')} {drops.toLocaleString()}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
              )}
            </div>

            {/* Alert Event Stream */}
            <div>
              <h2 style={{ fontSize: '1.25rem', fontWeight: 700, marginBottom: 14 }}>{t('system_events_alerts')}</h2>
              <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                {alerts.length === 0 ? (
                  <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
                    {t('no_events_recorded')}
                  </div>
                ) : (
                  <div style={{ maxHeight: 300, overflowY: 'auto' }}>
                    {alerts.map(a => (
                      <div
                        key={a.id}
                        style={{
                          padding: '10px 16px',
                          borderBottom: '1px solid var(--border-color)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 12,
                          fontSize: '0.85rem'
                        }}
                      >
                        <AlertCircle size={16} style={{ color: 'var(--color-primary)' }} />
                        <div style={{ flex: 1 }}>
                          <div>{a.message}</div>
                          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                            {new Date(a.created_at).toLocaleString()}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </main>

      {/* User Edit / Add Modal */}
      <UserModal
        user={editingUser}
        unassignedDevices={unassignedDevices}
        isOpen={userModalOpen}
        onClose={() => setUserModalOpen(false)}
        onSave={handleCreateOrUpdateUser}
      />

      {/* Settings Modal */}
      <SettingsModal
        isOpen={settingsModalOpen}
        onClose={() => setSettingsModalOpen(false)}
        onReboot={handleReboot}
        onRoutersChanged={loadData}
      />
    </div>
  );
}

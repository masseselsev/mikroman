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
import { TrafficHistoryModal } from './components/TrafficHistoryModal';
import { formatBytes } from './utils/formatters';
import { mergeTelemetryIntoUsers } from './utils/telemetryMerge';
import { SetupWizard } from './components/SetupWizard';
import { AppFooter } from './components/AppFooter';
import { QuotaStrip } from './components/QuotaStrip';
import { ContainersPage } from './components/ContainersPage';
import { Users, Laptop, Activity, BarChart2, Plus, AlertCircle, EyeOff, ChevronDown, ChevronRight, AlertTriangle, Container, ArrowUpDown } from 'lucide-react';
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
  const [autoSortActivity, setAutoSortActivity] = useState(() => {
    return localStorage.getItem('mikroman_auto_sort_activity') === 'true';
  });

  useEffect(() => {
    localStorage.setItem('mikroman_auto_sort_activity', autoSortActivity ? 'true' : 'false');
  }, [autoSortActivity]);

  const [userModalOpen, setUserModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState(null);
  const [settingsModalOpen, setSettingsModalOpen] = useState(false);
  const [settingsInitialTab, setSettingsInitialTab] = useState('general');
  const [settingsAutoAddRouter, setSettingsAutoAddRouter] = useState(false);
  const [trafficHistoryTarget, setTrafficHistoryTarget] = useState(null);

  const handleOpenSettings = (tab = 'general', autoAdd = false) => {
    setSettingsInitialTab(tab);
    setSettingsAutoAddRouter(autoAdd);
    setSettingsModalOpen(true);
  };

  // Hook up WebSocket telemetry with active router ID
  const { telemetry, isConnected } = useWebSocketTelemetry(activeRouter?.id);

  const loadData = async (routerIdOverride = null) => {
    try {
      const routersRes = await api.getRouters().catch(() => ({ data: [] }));
      const routerList = routersRes.data || [];
      setRouters(routerList);

      let current = activeRouter;
      if (routerIdOverride != null) {
        current = routerList.find(r => r.id === routerIdOverride) || current;
      } else if (!current && routerList.length > 0) {
        current = routerList.find(r => r.is_default) || routerList[0] || null;
      } else if (current && routerList.length > 0) {
        // Refresh activeRouter properties from the updated list
        current = routerList.find(r => r.id === current.id) || current;
      }

      if (current && (!activeRouter || activeRouter.id !== current.id || activeRouter.is_online !== current.is_online)) {
        setActiveRouter(current);
      }

      const targetRouterId = routerIdOverride ?? current?.id ?? null;

      const [usersRes, devsRes, ifacesRes, alertsRes] = await Promise.all([
        api.getUsers(targetRouterId).catch(() => ({ data: [] })),
        api.getDevices(true, showHiddenDevices, 'client', targetRouterId).catch(() => ({ data: [] })),
        api.getInterfaces(targetRouterId).catch(() => ({ data: [] })),
        api.getAlerts(targetRouterId).catch(() => ({ data: [] }))
      ]);

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
  }, [activeRouter?.id]);

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

  // All-time traffic across every assigned device, the denominator for each
  // device row's "share" figure. Derived from the same device objects the rows
  // render, so the percentages always add up.
  const deviceGrandTotal = users.reduce(
    (sum, u) => sum + (u.devices || []).reduce(
      (ds, d) => ds + (d.bytes_total_in || 0) + (d.bytes_total_out || 0),
      0
    ),
    0
  );

  // Unassigned devices split by whether they are actually asking for attention.
  // A hidden device was explicitly parked and should not inflate the "needs
  // sorting" badge; it gets its own quiet counter instead.
  const hiddenUnassignedCount = unassignedDevices.filter((d) => d.is_hidden).length;
  const visibleUnassignedCount = unassignedDevices.length - hiddenUnassignedCount;

  // Merge live WebSocket telemetry into users state for smooth animation.
  // The merge itself lives in utils/telemetryMerge so its identity rules - which
  // decide how often the dashboard repaints - can be tested directly.
  useEffect(() => {
    if (!telemetry?.users || users.length === 0) return;
    setUsers(prevUsers => mergeTelemetryIntoUsers(prevUsers, telemetry.users));
  }, [telemetry]);

  const handleSelectRouter = async (routerOrId) => {
    const routerObj = typeof routerOrId === 'object' && routerOrId !== null
      ? routerOrId
      : (routers.find(r => r.id === routerOrId) || null);
    if (!routerObj) return;
    setActiveRouter(routerObj);
    try {
      await api.activateRouter(routerObj.id);
    } catch (err) {
      console.error('Failed to activate router on backend:', err);
    }
    await loadData(routerObj.id);
  };

  const handleScan = async () => {
    setIsScanning(true);
    try {
      await api.scanNetwork(activeRouter?.id);
      await loadData(activeRouter?.id);
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
      await api.createUser({ ...userData, router_id: activeRouter?.id });
    }
    await loadData(activeRouter?.id);
  };

  const handleDeleteUser = async (userId) => {
    if (window.confirm(t('confirm_delete_user'))) {
      await api.deleteUser(userId);
      await loadData(activeRouter?.id);
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
    await loadData(activeRouter?.id);
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
        onSelectRouter={handleSelectRouter}
        onOpenSettings={() => handleOpenSettings('general', false)}
        onAddRouter={() => handleOpenSettings('routers', true)}
        onRouterCommentSaved={(comment) => {
          setActiveRouter(prev => (prev ? { ...prev, comment } : prev));
          setRouters(prev => prev.map(r => (r.id === activeRouter?.id ? { ...r, comment } : r)));
        }}
      />

      <main className="main-content">
        {/* Router Live Telemetry Bar */}
        {/* Tiles double as navigation: the hardware readings open Router
            Health, and the client count opens the users and their traffic. */}
        <TelemetryBar
          router={telemetry?.router}
          activeRouter={activeRouter}
          interfaces={interfaces}
          onNavigate={setActiveTab}
        />

        {/* ISP billing-cycle allowance - shows on every tab, only when set. */}
        <QuotaStrip
          activeRouterId={activeRouter?.id}
          onOpenSettings={() => handleOpenSettings('general', false)}
        />

        {/* Tab Navigation */}
        <div className="nav-tabs">
          <button
            className={`nav-tab ${activeTab === 'users' ? 'active' : ''}`}
            onClick={() => setActiveTab('users')}
          >
            <Users size={18} />
            {t('tab_users')}
            <span className="badge badge-neutral" style={{ padding: '1px 6px', fontSize: 'var(--fs-2xs)' }}>
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
            {/* Hidden devices are deliberately parked, so they must not sit in
                the same badge as devices actually waiting to be assigned - a
                permanent "2" that turns out to be two ignored records trains
                the eye to stop reading the badge at all. */}
            {visibleUnassignedCount > 0 && (
              <span className="badge badge-warning" style={{ padding: '1px 6px', fontSize: 'var(--fs-2xs)' }}>
                {visibleUnassignedCount}
              </span>
            )}
            {hiddenUnassignedCount > 0 && (
              <span
                className="badge badge-neutral"
                style={{ padding: '1px 5px', fontSize: 'var(--fs-2xs)', display: 'inline-flex', alignItems: 'center', gap: 3, opacity: 0.75 }}
                title={t('hidden_devices_badge_hint', { count: hiddenUnassignedCount })}
              >
                <EyeOff size={10} />
                {hiddenUnassignedCount}
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

          <button
            className={`nav-tab ${activeTab === 'containers' ? 'active' : ''}`}
            onClick={() => setActiveTab('containers')}
          >
            <Container size={18} />
            {t('tab_containers')}
          </button>
        </div>

        {/* Tab Content: Users */}
        {activeTab === 'users' && (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20, flexWrap: 'wrap', gap: 10 }}>
              <div>
                <h2 style={{ fontSize: 'var(--fs-xl)', fontWeight: 700 }}>{t('tab_users')}</h2>
                <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)' }}>
                  Organize network clients into users with per-user bandwidth limiting and instant pause controls.
                </p>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                {/* Auto-Sort by Activity Toggle */}
                <label className="toggle-pill" style={{ cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-xs)' }}>
                  <input
                    type="checkbox"
                    checked={autoSortActivity}
                    onChange={e => setAutoSortActivity(e.target.checked)}
                  />
                  <ArrowUpDown size={13} style={{ color: autoSortActivity ? 'var(--color-primary)' : 'var(--text-muted)' }} />
                  {t('sort_by_activity')}
                </label>

                {/* Show Hidden Devices Toggle */}
                <label className="toggle-pill" style={{ cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-xs)' }}>
                  <input
                    type="checkbox"
                    checked={showHiddenDevices}
                    onChange={e => setShowHiddenDevices(e.target.checked)}
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
              <div className="card empty-state">
                <Users size={40} style={{ margin: '0 auto 12px auto', opacity: 0.5 }} />
                <div className="empty-state-title">{t('no_users_found')}</div>
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
                    users={users}
                    dragIndex={index}
                    showHidden={showHiddenDevices}
                    autoSortActivity={autoSortActivity}
                    onEdit={(u) => {
                      setEditingUser(u);
                      setUserModalOpen(true);
                    }}
                    onDelete={handleDeleteUser}
                    onLimitChange={handleLimitChange}
                    onPauseToggle={handlePauseToggle}
                    onUpdate={loadData}
                    onViewTrafficHistory={setTrafficHistoryTarget}
                    gatewayTotal={gatewayTodayTotal}
                    deviceGrandTotal={deviceGrandTotal}
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

        {/* Tab Content: RouterOS Containers */}
        {activeTab === 'containers' && (
          <ContainersPage activeRouter={activeRouter} />
        )}

        {/* Tab Content: Unassigned Devices */}
        {activeTab === 'devices' && (
          <DeviceInbox
            devices={unassignedDevices}
            users={users}
            onAssign={handleAssignDevice}
            onScan={handleScan}
            isScanning={isScanning}
            onViewTrafficHistory={setTrafficHistoryTarget}
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
                <h2 style={{ fontSize: 'var(--fs-xl)', fontWeight: 700 }}>{t('network_interfaces')}</h2>
                <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
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
                      <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
                        +{interfaceSummary.faulty.length - 4}
                      </span>
                    )}
                  </span>
                ) : (
                  <span className="badge badge-success" style={{ fontSize: 'var(--fs-3xs)' }}>
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
                        <span style={{ fontWeight: 700, fontSize: 'var(--fs-md)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {iface.name}
                        </span>
                        <span className={`badge ${iface.running ? 'badge-success' : 'badge-neutral'}`}>
                          {iface.running ? t('status_running') : t('status_down')}
                        </span>
                      </div>

                      <div style={{ display: 'flex', gap: 14, marginBottom: 6 }}>
                        <div>
                          <div style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)', fontWeight: 600 }}>RX</div>
                          <div className="font-mono" style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, color: 'var(--color-success)' }}>
                            {formatBytes(iface.rx_byte || 0)}
                          </div>
                        </div>
                        <div>
                          <div style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)', fontWeight: 600 }}>TX</div>
                          <div className="font-mono" style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, color: 'var(--color-primary)' }}>
                            {formatBytes(iface.tx_byte || 0)}
                          </div>
                        </div>
                      </div>

                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        fontSize: 'var(--fs-2xs)',
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
              <h2 style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, marginBottom: 14 }}>{t('system_events_alerts')}</h2>
              <div className="card panel-flush">
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
                          fontSize: 'var(--fs-sm)'
                        }}
                      >
                        <AlertCircle size={16} style={{ color: 'var(--color-primary)' }} />
                        <div style={{ flex: 1 }}>
                          <div>{a.message}</div>
                          <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
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
        onDeviceChanged={loadData}
      />

      {/* Settings Modal */}
      <SettingsModal
        isOpen={settingsModalOpen}
        initialTab={settingsInitialTab}
        autoOpenAddRouter={settingsAutoAddRouter}
        onClose={() => setSettingsModalOpen(false)}
        onReboot={handleReboot}
        onRoutersChanged={loadData}
      />

      {/* Traffic History Modal (User & Device) */}
      <TrafficHistoryModal
        isOpen={!!trafficHistoryTarget}
        target={trafficHistoryTarget}
        onClose={() => setTrafficHistoryTarget(null)}
        onSelectTarget={setTrafficHistoryTarget}
      />

      <AppFooter />
    </div>
  );
}

import React, { useState, useEffect } from 'react';
import { useI18n } from './context/I18nContext';
import { useWebSocketTelemetry } from './hooks/useWebSocketTelemetry';
import { api } from './api/client';
import { Navbar } from './components/Navbar';
import { TelemetryBar } from './components/TelemetryBar';
import { UserCard } from './components/UserCard';
import { UserModal } from './components/UserModal';
import { DeviceInbox } from './components/DeviceInbox';
import { SettingsModal } from './components/SettingsModal';
import { SetupWizard } from './components/SetupWizard';
import { Users, Laptop, Activity, Plus, AlertCircle } from 'lucide-react';

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

      const currentDefault = routerList.find(r => r.is_default) || routerList[0] || null;
      setActiveRouter(currentDefault);

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
  }, []);

  // Merge live WebSocket telemetry into users state for smooth animation
  useEffect(() => {
    if (telemetry?.users && users.length > 0) {
      const telemetryMap = new Map(telemetry.users.map(u => [u.user_id, u]));
      setUsers(prevUsers =>
        prevUsers.map(u => {
          const live = telemetryMap.get(u.id);
          if (live) {
            return {
              ...u,
              current_rate_in: live.current_rate_in,
              current_rate_out: live.current_rate_out,
              bytes_today_in: live.bytes_in,
              bytes_today_out: live.bytes_out,
              is_paused: live.is_paused
            };
          }
          return u;
        })
      );
    }
  }, [telemetry]);

  const handleSelectRouter = async (routerId) => {
    try {
      await api.activateRouter(routerId);
      await loadData();
    } catch (err) {
      console.error('Failed to switch router', err);
    }
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
        <TelemetryBar router={telemetry?.router} />

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
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
              <div>
                <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }}>{t('tab_users')}</h2>
                <p style={{ fontSize: '0.825rem', color: 'var(--text-muted)' }}>
                  Organize network clients into users with per-user bandwidth limiting and instant pause controls.
                </p>
              </div>
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
                {users.map(user => (
                  <UserCard
                    key={user.id}
                    user={user}
                    onEdit={(u) => {
                      setEditingUser(u);
                      setUserModalOpen(true);
                    }}
                    onDelete={handleDeleteUser}
                    onLimitChange={handleLimitChange}
                    onPauseToggle={handlePauseToggle}
                  />
                ))}
              </div>
            )}
          </div>
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
            <div>
              <h2 style={{ fontSize: '1.25rem', fontWeight: 700, marginBottom: 14 }}>Network Interfaces</h2>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
                {interfaces.map(iface => (
                  <div key={iface.id || iface.name} className="card" style={{ padding: 14 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                      <span style={{ fontWeight: 700 }}>{iface.name}</span>
                      <span className={`badge ${iface.running ? 'badge-success' : 'badge-neutral'}`}>
                        {iface.running ? 'Running' : 'Down'}
                      </span>
                    </div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'flex', justifyContent: 'space-between' }}>
                      <span>Type: {iface.type || 'ethernet'}</span>
                      <span className="font-mono">RX: {(iface.rx_byte / (1024 * 1024)).toFixed(1)} MB</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Alert Event Stream */}
            <div>
              <h2 style={{ fontSize: '1.25rem', fontWeight: 700, marginBottom: 14 }}>System Events & Alerts</h2>
              <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                {alerts.length === 0 ? (
                  <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
                    No events recorded yet.
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

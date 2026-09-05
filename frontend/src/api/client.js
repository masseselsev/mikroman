const API_BASE = '/api/v1';

/**
 * Build a query string from an object, dropping empty values.
 *
 * Returns '' rather than '?' when nothing survives, so it can be appended to
 * any path unconditionally.
 */
function qs(params = {}) {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') q.append(k, v);
  });
  const out = q.toString();
  return out ? `?${out}` : '';
}

async function request(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {})
  };

  const response = await fetch(url, { ...options, headers });
  const text = await response.text();
  let json = {};
  try {
    json = text ? JSON.parse(text) : {};
  } catch (e) {
    if (!response.ok) {
      throw new Error(`Server error (${response.status}): ${text || response.statusText}`);
    }
  }

  if (!response.ok) {
    throw new Error(json.detail || json.message || `Request failed with status ${response.status}`);
  }
  return json;
}

export const api = {
  // Routers
  getRouters: () => request('/routers'),
  createRouter: (data) => request('/routers', { method: 'POST', body: JSON.stringify(data) }),
  testRouterConnection: (data) => request('/routers/test', { method: 'POST', body: JSON.stringify(data) }),
  provisionRouterSsl: (id, data = {}) => request(`/routers/${id}/provision-ssl`, { method: 'POST', body: JSON.stringify(data) }),
  autoProvisionSslDirect: (data) => request('/routers/test-provision-ssl', { method: 'POST', body: JSON.stringify(data) }),
  getRouterCertificates: (id) => request(`/routers/${id}/certificates`),
  testListCertificates: (conn) => request('/routers/test-certificates', { method: 'POST', body: JSON.stringify(conn) }),
  testBindCertificate: (conn, certName) => request('/routers/test-bind-certificate', { method: 'POST', body: JSON.stringify({ cert_req: { certificate_name: certName }, conn }) }),
  testUploadCertificate: (conn, uploadReq) => request('/routers/test-upload-certificate', { method: 'POST', body: JSON.stringify({ upload_req: uploadReq, conn }) }),
  getRouter: (id) => request(`/routers/${id}`),
  updateRouter: (id, data) => request(`/routers/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  // mode: 'archive' (default, keeps all data) | 'purge' (erases it)
  deleteRouter: (id, mode = 'archive') => request(`/routers/${id}`, { method: 'DELETE', body: JSON.stringify({ mode }) }),
  activateRouter: (id) => request(`/routers/${id}/activate`, { method: 'POST' }),
  getArchivedRouters: () => request('/routers/archived'),
  restoreRouter: (id) => request(`/routers/${id}/restore`, { method: 'POST' }),
  changeRouter: (id, data) => request(`/routers/${id}/change`, { method: 'POST', body: JSON.stringify(data) }),

  // Containers (optional RouterOS package)
  getContainers: (routerId) => request(`/routers/${routerId}/containers`),
  containerAction: (routerId, containerId, action) =>
    request(`/routers/${routerId}/containers/${encodeURIComponent(containerId)}/${action}`, { method: 'POST' }),
  createContainer: (routerId, payload) =>
    request(`/routers/${routerId}/containers`, { method: 'POST', body: JSON.stringify(payload) }),

  // Speed test (runs in a container on the router, so it measures the ISP link
  // rather than the path from the router to this browser).
  getSpeedTestStatus: (routerId) => request(`/routers/${routerId}/speedtest`),
  // A run blocks for as long as the test takes - up to a couple of minutes.
  // `fetch` imposes no timeout of its own, so nothing here needs to raise one;
  // the backend caps the wait and returns a 'timeout' result rather than hanging.
  runSpeedTest: (routerId) =>
    request(`/routers/${routerId}/speedtest/run`, { method: 'POST' }),
  createSpeedTestContainer: (routerId, payload) =>
    request(`/routers/${routerId}/speedtest/container`, { method: 'POST', body: JSON.stringify(payload) }),
  getSpeedTestHistory: (routerId, limit = 20) =>
    request(`/routers/${routerId}/speedtest/history?limit=${limit}`),

  // Users
  getUsers: (routerId = null) => request(`/users${routerId ? `?router_id=${routerId}` : ''}`),
  getUser: (id) => request(`/users/${id}`),
  createUser: (data) => request('/users', { method: 'POST', body: JSON.stringify(data) }),
  updateUser: (id, data) => request(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteUser: (id) => request(`/users/${id}`, { method: 'DELETE' }),
  getUserTrafficHistory: (userId, { preset = '7d', startDate = null, endDate = null } = {}) => {
    const params = new URLSearchParams();
    if (preset) params.append('preset', preset);
    if (startDate) params.append('start_date', startDate);
    if (endDate) params.append('end_date', endDate);
    return request(`/users/${userId}/traffic-history?${params.toString()}`);
  },
  getDeviceTrafficHistory: (deviceId, { preset = '7d', startDate = null, endDate = null } = {}) => {
    const params = new URLSearchParams();
    if (preset) params.append('preset', preset);
    if (startDate) params.append('start_date', startDate);
    if (endDate) params.append('end_date', endDate);
    return request(`/devices/${deviceId}/traffic-history?${params.toString()}`);
  },

  // Devices
  // `kind` defaults to 'client' server-side: containers are router workloads,
  // not people's devices, and must not queue in the unassigned inbox.
  getDevices: (unassignedOnly = false, showHidden = true, kind = 'client', routerId = null) => {
    const params = new URLSearchParams({
      unassigned_only: String(unassignedOnly),
      show_hidden: String(showHidden),
      kind
    });
    if (routerId) params.append('router_id', String(routerId));
    return request(`/devices?${params.toString()}`);
  },
  getContainerDevices: (routerId = null) => request(`/devices?kind=container&show_hidden=true${routerId ? `&router_id=${routerId}` : ''}`),
  scanNetwork: (routerId = null) => request(`/devices/scan${routerId ? `?router_id=${routerId}` : ''}`, { method: 'POST' }),
  updateDevice: (id, data) => request(`/devices/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteDevice: (id) => request(`/devices/${id}`, { method: 'DELETE' }),
  splitDevice: (id, macAddress) => request(`/devices/${id}/split`, { method: 'POST', body: JSON.stringify({ mac_address: macAddress }) }),
  toggleHideDevice: (id, isHidden) => request(`/devices/${id}`, { method: 'PATCH', body: JSON.stringify({ is_hidden: isHidden }) }),
  getDeviceHistory: (id) => request(`/devices/${id}/history`),
  reorderUsers: (userIds) => request('/users/reorder', {
    method: 'POST',
    body: JSON.stringify({ user_ids: userIds })
  }),
  getQuota: (routerId = null) => request(`/analytics/quota${routerId ? `?router_id=${routerId}` : ''}`),
  saveQuota: (config, routerId = null) => request(`/analytics/quota${routerId ? `?router_id=${routerId}` : ''}`, {
    method: 'POST',
    body: JSON.stringify(config)
  }),
  getMergeSuggestions: (routerId) => request(`/devices/suggestions${routerId ? `?router_id=${routerId}` : ''}`),
  // Linking keeps both records and presents them as one machine with several
  // network adapters; merging collapses two records into one and exists for
  // MAC rotation, where only one address was ever real.
  getLinkSuggestions: (routerId) => request(`/devices/link-suggestions${routerId ? `?router_id=${routerId}` : ''}`),
  linkDevice: (id, primaryDeviceId) => request(`/devices/${id}/link`, {
    method: 'POST',
    body: JSON.stringify({ primary_device_id: primaryDeviceId })
  }),
  unlinkDevice: (id) => request(`/devices/${id}/unlink`, { method: 'POST' }),
  mergeDevice: (id, targetDeviceId, note = '') => request(`/devices/${id}/merge`, {
    method: 'POST',
    body: JSON.stringify({ target_device_id: targetDeviceId, note })
  }),
  setDeviceLimit: (deviceId, speedLimit) => request(`/devices/${deviceId}/limit`, {
    method: 'POST',
    body: JSON.stringify({ speed_limit: speedLimit })
  }),
  toggleDevicePause: (deviceId, isPaused) => request(`/devices/${deviceId}/pause`, {
    method: 'POST',
    body: JSON.stringify({ is_paused: isPaused })
  }),

  // Traffic & Analytics
  setUserLimit: (userId, speedLimit) => request(`/traffic/users/${userId}/limit`, {
    method: 'POST',
    body: JSON.stringify({ speed_limit: speedLimit })
  }),
  toggleUserPause: (userId, isPaused) => request(`/traffic/users/${userId}/pause`, {
    method: 'POST',
    body: JSON.stringify({ is_paused: isPaused })
  }),
  getTrafficAnalytics: ({ preset = '7d', startDate = null, endDate = null, routerId = null } = {}) => {
    let url = `/analytics/traffic?preset=${preset}`;
    if (startDate) url += `&start_date=${startDate}`;
    if (endDate) url += `&end_date=${endDate}`;
    if (routerId) url += `&router_id=${routerId}`;
    return request(url);
  },
  getBillingCycleConfig: (routerId = null) => request(`/analytics/billing-cycle${routerId ? `?router_id=${routerId}` : ''}`),
  saveBillingCycleConfig: (anchorDay, anchorHour = 0, anchorMinute = 0, routerId = null) => request(`/analytics/billing-cycle${routerId ? `?router_id=${routerId}` : ''}`, {
    method: 'POST',
    body: JSON.stringify({
      anchor_day: Number(anchorDay),
      anchor_hour: Number(anchorHour),
      anchor_minute: Number(anchorMinute),
      router_id: routerId
    }),
  }),

  // Metrics & Graphs
  getSystemMetrics: (range = '1h', routerId = null) => request(`/metrics/system?range=${range}${routerId ? `&router_id=${routerId}` : ''}`),
  getInterfaceMetrics: (range = '1h', interfaces = null, routerId = null) => {
    let url = `/metrics/interfaces?range=${range}`;
    if (interfaces) url += `&interfaces=${encodeURIComponent(Array.isArray(interfaces) ? interfaces.join(',') : interfaces)}`;
    if (routerId) url += `&router_id=${routerId}`;
    return request(url);
  },
  getAvailableInterfaces: (routerId = null) => request(`/metrics/interfaces/list${routerId ? `?router_id=${routerId}` : ''}`),
  getMonitoredInterfacesConfig: (routerId = null) => request(`/metrics/config${routerId ? `?router_id=${routerId}` : ''}`),
  saveMonitoredInterfacesConfig: (routerIdOrPayload, selectedInterfaces) => {
    const payload = (typeof routerIdOrPayload === 'object' && routerIdOrPayload !== null)
      ? routerIdOrPayload
      : { router_id: routerIdOrPayload, selected_interfaces: selectedInterfaces };
    return request('/metrics/config', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  },

  // System & Settings
  getSystemStatus: (routerId = null) => request(`/system/status${routerId ? `?router_id=${routerId}` : ''}`),
  getInterfaces: (routerId = null) => request(`/system/interfaces${routerId ? `?router_id=${routerId}` : ''}`),
  getAlerts: (routerId = null) => request(`/system/alerts${routerId ? `?router_id=${routerId}` : ''}`),
  getSettings: (routerId = null) => request(`/system/settings${routerId ? `?router_id=${routerId}` : ''}`),
  getIpLookup: () => request('/system/ip-lookup'),
  saveIpLookup: (config) => request('/system/ip-lookup', { method: 'POST', body: JSON.stringify(config) }),
  saveSettings: (settings, routerId = null) => request(`/system/settings${routerId ? `?router_id=${routerId}` : ''}`, { method: 'POST', body: JSON.stringify(settings) }),
  rebootRouter: (routerId = null) => request(`/system/reboot${routerId ? `?router_id=${routerId}` : ''}`, { method: 'POST' }),
  testTelegram: (data = {}) => request('/telegram/test', { method: 'POST', body: JSON.stringify(data) }),

  // Live Connections & Geo-IP
  getLiveConnections: (params = {}) => request(`/connections${qs(params)}`),
  killConnection: (connectionId, payload = {}) =>
    request(`/connections/${encodeURIComponent(connectionId)}/kill`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getUserDestinations: (userId, params = {}) => request(`/analytics/users/${userId}/destinations${qs(params)}`),

  // Firmware & Upgrades
  getFirmwareStatus: (routerId) => request(`/routers/${routerId}/firmware`),
  checkFirmwareUpdates: (routerId) => request(`/routers/${routerId}/firmware/check`, { method: 'POST' }),
  setFirmwareChannel: (routerId, channel) => request(`/routers/${routerId}/firmware/channel`, { method: 'PUT', body: JSON.stringify({ channel }) }),
  getChangelog: (routerId, version) => request(`/routers/${routerId}/firmware/changelog?version=${encodeURIComponent(version)}`),
  upgradeRouterFirmware: (routerId, payload) => request(`/routers/${routerId}/firmware/upgrade`, { method: 'POST', body: JSON.stringify(payload) }),
  upgradeBootloader: (routerId, payload) => request(`/routers/${routerId}/firmware/bootloader`, { method: 'POST', body: JSON.stringify(payload) }),

  // Config-drift backups.
  // These endpoints answer with the payload itself, not the {success,data}
  // envelope the rest of the API uses, so callers read `.items` / the record
  // directly rather than `.data`.
  getRouterBackups: (routerId, params = {}) => request(`/routers/${routerId}/backups${qs(params)}`),
  triggerRouterBackup: (routerId) => request(`/routers/${routerId}/backups/run`, { method: 'POST' }),
  getRouterBackup: (routerId, backupId) => request(`/routers/${routerId}/backups/${backupId}`),
  updateRouterBackup: (routerId, backupId, data) =>
    request(`/routers/${routerId}/backups/${backupId}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteRouterBackup: (routerId, backupId) =>
    request(`/routers/${routerId}/backups/${backupId}`, { method: 'DELETE' }),
  getBackupDiff: (routerId, params = {}) => request(`/routers/${routerId}/backups/diff${qs(params)}`),
  // Plain hrefs: the browser downloads these, they never pass through fetch().
  getBackupRscDownloadUrl: (routerId, backupId) =>
    `${API_BASE}/routers/${routerId}/backups/${backupId}/download/rsc`,
  getBackupBinaryDownloadUrl: (routerId, backupId) =>
    `${API_BASE}/routers/${routerId}/backups/${backupId}/download/backup`,

  // Router log stream, stored history and /system/logging topic rules
  getLogs: (params = {}) => request(`/logs${qs(params)}`),
  getLogStats: (params = {}) => request(`/logs/stats${qs(params)}`),
  clearStoredLogs: (params = {}) => request(`/logs${qs(params)}`, { method: 'DELETE' }),
  getLoggingRules: (routerId = null) => request(`/logs/rules${qs({ router_id: routerId })}`),
  createLoggingRule: (data, routerId = null) =>
    request(`/logs/rules${qs({ router_id: routerId })}`, { method: 'POST', body: JSON.stringify(data) }),
  deleteLoggingRule: (ruleId, routerId = null) =>
    request(`/logs/rules/${encodeURIComponent(ruleId)}${qs({ router_id: routerId })}`, { method: 'DELETE' }),
};

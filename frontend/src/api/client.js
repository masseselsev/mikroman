const API_BASE = '/api/v1';

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
  testBindCertificate: (conn, certName, port = 443) => request('/routers/test-bind-certificate', { method: 'POST', body: JSON.stringify({ cert_req: { certificate_name: certName, port }, conn }) }),
  testUploadCertificate: (conn, uploadReq) => request('/routers/test-upload-certificate', { method: 'POST', body: JSON.stringify({ upload_req: uploadReq, conn }) }),
  getRouter: (id) => request(`/routers/${id}`),
  updateRouter: (id, data) => request(`/routers/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteRouter: (id) => request(`/routers/${id}`, { method: 'DELETE' }),
  activateRouter: (id) => request(`/routers/${id}/activate`, { method: 'POST' }),

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
  getUsers: () => request('/users'),
  createUser: (data) => request('/users', { method: 'POST', body: JSON.stringify(data) }),
  getUser: (id) => request(`/users/${id}`),
  updateUser: (id, data) => request(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteUser: (id) => request(`/users/${id}`, { method: 'DELETE' }),

  // Devices
  // `kind` defaults to 'client' server-side: containers are router workloads,
  // not people's devices, and must not queue in the unassigned inbox.
  getDevices: (unassignedOnly = false, showHidden = true, kind = 'client') =>
    request(`/devices?unassigned_only=${unassignedOnly}&show_hidden=${showHidden}&kind=${kind}`),
  getContainerDevices: () => request('/devices?kind=container&show_hidden=true'),
  scanNetwork: () => request('/devices/scan', { method: 'POST' }),
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
  saveQuota: (config) => request('/analytics/quota', {
    method: 'POST',
    body: JSON.stringify(config)
  }),
  getMergeSuggestions: () => request('/devices/suggestions'),
  // Linking keeps both records and presents them as one machine with several
  // network adapters; merging collapses two records into one and exists for
  // MAC rotation, where only one address was ever real.
  getLinkSuggestions: () => request('/devices/link-suggestions'),
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
  getBillingCycleConfig: () => request('/analytics/billing-cycle'),
  saveBillingCycleConfig: (anchorDay, anchorHour = 0, anchorMinute = 0) => request('/analytics/billing-cycle', {
    method: 'POST',
    body: JSON.stringify({
      anchor_day: Number(anchorDay),
      anchor_hour: Number(anchorHour),
      anchor_minute: Number(anchorMinute),
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
  getSystemStatus: () => request('/system/status'),
  getInterfaces: () => request('/system/interfaces'),
  getAlerts: () => request('/system/alerts'),
  getSettings: () => request('/system/settings'),
  getIpLookup: () => request('/system/ip-lookup'),
  saveIpLookup: (config) => request('/system/ip-lookup', { method: 'POST', body: JSON.stringify(config) }),
  saveSettings: (settings) => request('/system/settings', { method: 'POST', body: JSON.stringify(settings) }),
  rebootRouter: () => request('/system/reboot', { method: 'POST' }),
  testTelegram: (data = {}) => request('/telegram/test', { method: 'POST', body: JSON.stringify(data) }),
};

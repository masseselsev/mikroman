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

  // Users
  getUsers: () => request('/users'),
  createUser: (data) => request('/users', { method: 'POST', body: JSON.stringify(data) }),
  getUser: (id) => request(`/users/${id}`),
  updateUser: (id, data) => request(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteUser: (id) => request(`/users/${id}`, { method: 'DELETE' }),

  // Devices
  getDevices: (unassignedOnly = false) => request(`/devices?unassigned_only=${unassignedOnly}`),
  scanNetwork: () => request('/devices/scan', { method: 'POST' }),
  updateDevice: (id, data) => request(`/devices/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),

  // Traffic
  setUserLimit: (userId, speedLimit) => request(`/traffic/users/${userId}/limit`, {
    method: 'POST',
    body: JSON.stringify({ speed_limit: speedLimit })
  }),
  toggleUserPause: (userId, isPaused) => request(`/traffic/users/${userId}/pause`, {
    method: 'POST',
    body: JSON.stringify({ is_paused: isPaused })
  }),

  // System & Settings
  getSystemStatus: () => request('/system/status'),
  getInterfaces: () => request('/system/interfaces'),
  getAlerts: () => request('/system/alerts'),
  getSettings: () => request('/system/settings'),
  saveSettings: (settings) => request('/system/settings', { method: 'POST', body: JSON.stringify(settings) }),
  rebootRouter: () => request('/system/reboot', { method: 'POST' }),
  testTelegram: () => request('/telegram/test', { method: 'POST' }),
};

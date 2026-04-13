// Author: Claude Opus 4.6
// Date: 08-April-2026
// PURPOSE: Farm Guardian Dashboard — Frontend Logic.
//          Vanilla JS, no build step. Communicates with FastAPI backend via /api/ endpoints.
//          Dynamic camera grid renders whatever cameras the API returns.
//          No hardcoded camera names anywhere.
// SRP/DRY check: Pass — single responsibility is UI state and API communication.

// ─────────────────────────────────────────
// State
// ─────────────────────────────────────────
let currentPage = 'dashboard';
let allEvents = [];
let refreshInterval = null;
let cachedCameras = [];  // latest camera list from API

// ─────────────────────────────────────────
// API Client
// ─────────────────────────────────────────
const api = {
    async get(url) {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`GET ${url} → ${res.status}`);
        return res.json();
    },
    async post(url, body = {}) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`POST ${url} → ${res.status}`);
        return res.json();
    },
};

// ─────────────────────────────────────────
// Snapshot Polling
// ─────────────────────────────────────────
// All cameras serve snapshots via /api/cameras/{name}/frame. The dashboard
// refreshes <img> tags on a timer by appending a cache-busting query param.
//
// v2.18.0: house-yard now returns native 4K JPEGs (~1.4MB). Locally that's a
// trivial 2ms transfer; via the Cloudflare tunnel the home upstream bandwidth
// (~600 KB/s effective) makes 1.4MB an intermittent timeout risk. So:
//   - Local clients (localhost / RFC1918 / .local) request full quality.
//   - Tunnel clients request max_width=1920 so each image lands in <1s on
//     a healthy tunnel pull.
// The user can override either way by appending their own ?max_width=N.
function _isLocalNetwork(hostname) {
    return hostname === 'localhost'
        || hostname === '127.0.0.1'
        || hostname.endsWith('.local')
        || /^10\./.test(hostname)
        || /^192\.168\./.test(hostname)
        || /^172\.(1[6-9]|2\d|3[01])\./.test(hostname);
}
const SNAPSHOT_MAX_WIDTH = _isLocalNetwork(window.location.hostname) ? null : 1920;

let _snapshotTimer = null;

function startSnapshotPolling(intervalMs = 5000) {
    stopSnapshotPolling();
    _snapshotTimer = setInterval(() => {
        const ts = Date.now();
        document.querySelectorAll('img[data-snapshot]').forEach(img => {
            const cam = img.dataset.snapshot;
            const widthParam = SNAPSHOT_MAX_WIDTH ? `max_width=${SNAPSHOT_MAX_WIDTH}&` : '';
            img.src = `/api/cameras/${cam}/frame?${widthParam}t=${ts}`;
        });
    }, intervalMs);
}

function stopSnapshotPolling() {
    if (_snapshotTimer) {
        clearInterval(_snapshotTimer);
        _snapshotTimer = null;
    }
}

// ─────────────────────────────────────────
// Router
// ─────────────────────────────────────────
function navigate(page) {
    currentPage = page;
    document.querySelectorAll('.page').forEach(el => el.classList.remove('active'));
    const target = document.getElementById(`page-${page}`);
    if (target) target.classList.add('active');
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.nav === page);
    });
    const titleEl = document.getElementById('page-title');
    if (titleEl) titleEl.textContent = page.toUpperCase();
    refreshCurrentPage();
}

function refreshCurrentPage() {
    const loaders = {
        dashboard: loadDashboard,
        cameras: loadCameras,
        events: loadEventDates,
        alerts: loadAlerts,
        ptz: loadPTZ,
        reports: loadReportDates,
        settings: loadSettings,
    };
    const loader = loaders[currentPage];
    if (loader) loader();
}

// ─────────────────────────────────────────
// Status Bar
// ─────────────────────────────────────────
function updateStatusBar(status, lastDetection) {
    const dot = document.getElementById('sb-dot');
    const statusEl = document.getElementById('sb-status');
    const uptimeEl = document.getElementById('sb-uptime');
    const framesEl = document.getElementById('sb-frames');
    const detectionsEl = document.getElementById('sb-detections');
    const lastEl = document.getElementById('sb-last-detection');
    const sidebarDot = document.getElementById('sidebar-dot');

    if (!status || !status.online) {
        if (dot) { dot.className = 'status-dot dot-red'; }
        if (sidebarDot) { sidebarDot.className = 'status-dot dot-red'; }
        if (statusEl) statusEl.textContent = 'OFFLINE';
        return;
    }

    if (dot) { dot.className = 'status-dot dot-green pulse'; }
    if (sidebarDot) { sidebarDot.className = 'status-dot dot-green'; }
    if (statusEl) { statusEl.textContent = 'ONLINE'; statusEl.style.color = 'var(--green)'; }
    if (uptimeEl) uptimeEl.textContent = formatUptime(status.uptime_seconds);
    if (framesEl) framesEl.textContent = (status.frames_processed || 0).toLocaleString();
    if (detectionsEl) detectionsEl.textContent = status.detections_today || 0;
    if (lastEl && lastDetection) {
        lastEl.textContent = formatTime(lastDetection.timestamp || lastDetection.detected_at);
    }
}

// ─────────────────────────────────────────
// Dashboard — Dynamic Camera Grid
// ─────────────────────────────────────────
async function loadDashboard() {
    try {
        const [status, cameras, detections] = await Promise.all([
            api.get('/api/status'),
            api.get('/api/cameras'),
            api.get('/api/detections/recent?limit=15'),
        ]);
        cachedCameras = cameras;
        const lastDet = detections && detections.length ? detections[0] : null;
        updateStatusBar(status, lastDet);
        renderCameraGrid(cameras);
        renderDashboardDetections(detections);
        loadDashboardPTZStatus();
    } catch (err) {
        console.error('Dashboard load error:', err);
    }
}

function renderCameraGrid(cameras) {
    const grid = document.getElementById('cam-grid');
    if (!grid) return;

    if (!cameras || !cameras.length) {
        grid.innerHTML = '<div class="cam-cell"><div class="cam-offline">NO CAMERAS</div></div>';
        grid.style.gridTemplateColumns = '1fr';
        return;
    }

    // Determine grid layout based on camera count
    const count = cameras.length;
    if (count === 1) {
        grid.style.gridTemplateColumns = '1fr';
        grid.style.gridTemplateRows = '1fr';
    } else if (count === 2) {
        grid.style.gridTemplateColumns = '1fr 1fr';
        grid.style.gridTemplateRows = '1fr';
    } else if (count <= 4) {
        grid.style.gridTemplateColumns = 'repeat(2, 1fr)';
        grid.style.gridTemplateRows = `repeat(${Math.ceil(count / 2)}, 1fr)`;
    } else if (count <= 6) {
        grid.style.gridTemplateColumns = 'repeat(3, 1fr)';
        grid.style.gridTemplateRows = `repeat(${Math.ceil(count / 3)}, 1fr)`;
    } else {
        grid.style.gridTemplateColumns = 'repeat(4, 1fr)';
        grid.style.gridTemplateRows = `repeat(${Math.ceil(count / 4)}, 1fr)`;
    }

    // Build cells — only replace innerHTML if camera list changed
    const currentNames = Array.from(grid.querySelectorAll('[data-cam]')).map(el => el.dataset.cam);
    const newNames = cameras.map(c => c.name);
    const structureChanged = currentNames.length !== newNames.length || currentNames.some((n, i) => n !== newNames[i]);

    if (structureChanged) {
        grid.innerHTML = cameras.map(cam => {
            const dotClass = cam.online ? 'dot-green' : 'dot-red';
            if (cam.capturing && cam.online) {
                // Snapshot-polled <img> — refreshed on a timer via data-snapshot attribute
                return `<div class="cam-cell" data-cam="${cam.name}">
                    <img data-snapshot="${cam.name}" src="/api/cameras/${cam.name}/frame?t=${Date.now()}" alt="${cam.name}"
                         onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';"
                         onload="this.style.display='block'; this.nextElementSibling.style.display='none';">
                    <div class="cam-offline" style="display:none; position:absolute; inset:0;">FEED LOST</div>
                    <div class="cam-label"><span class="status-dot ${dotClass}"></span>${cam.name}</div>
                </div>`;
            } else {
                return `<div class="cam-cell" data-cam="${cam.name}">
                    <div class="cam-offline">${cam.online ? 'IDLE' : 'OFFLINE'}</div>
                    <div class="cam-label"><span class="status-dot ${dotClass}"></span>${cam.name}</div>
                </div>`;
            }
        }).join('');

        // Start polling for snapshot refresh
        startSnapshotPolling();
    } else {
        // Structure same — just update status dots
        cameras.forEach(cam => {
            const cell = grid.querySelector(`[data-cam="${cam.name}"]`);
            if (!cell) return;
            const dot = cell.querySelector('.status-dot');
            if (dot) dot.className = `status-dot ${cam.online ? 'dot-green' : 'dot-red'}`;
        });
    }
}

function renderDashboardDetections(detections) {
    const tbody = document.getElementById('dashboard-detections');
    if (!tbody) return;
    if (!detections || !detections.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-2);">--</td></tr>';
        return;
    }
    const rows = detections.slice(0, 14);
    tbody.innerHTML = rows.map(d => {
        const isPred = d.is_predator;
        const cls = d.class || d.class_name || '?';
        const cam = d.camera || d.camera_id || '?';
        const conf = typeof d.confidence === 'number' ? `${(d.confidence * 100).toFixed(0)}%` : '--';
        const ts = formatTime(d.timestamp || d.detected_at);
        const badgeClass = isPred ? 'badge-red' : 'badge-mute';
        return `<tr>
            <td>${ts}</td>
            <td>${cam}</td>
            <td><span class="badge ${badgeClass}">${cls}</span></td>
            <td>${conf}</td>
            <td style="color:${isPred ? 'var(--red)' : 'var(--text-2)'};">${isPred ? 'PRED' : '--'}</td>
        </tr>`;
    }).join('');
}

// ─────────────────────────────────────────
// Dashboard PTZ Status
// ─────────────────────────────────────────
async function loadDashboardPTZStatus() {
    try {
        const [ptzStatus, deterrent, tracks] = await Promise.all([
            api.get('/api/ptz/status').catch(() => ({ patrol_active: false })),
            api.get('/api/deterrent/status').catch(() => ({ enabled: false, active_count: 0 })),
            api.get('/api/tracks/active').catch(() => []),
        ]);

        const patrolEl = document.getElementById('dash-patrol-status');
        if (patrolEl) {
            if (ptzStatus.patrol_active) {
                patrolEl.textContent = ptzStatus.patrol_paused ? 'PAUSED' : 'ACTIVE';
                patrolEl.style.color = ptzStatus.patrol_paused ? 'var(--amber)' : 'var(--green)';
            } else {
                patrolEl.textContent = 'OFF'; patrolEl.style.color = 'var(--text-2)';
            }
        }

        const detEl = document.getElementById('dash-deterrent-status');
        if (detEl) {
            if (!deterrent.enabled) {
                detEl.textContent = 'OFF'; detEl.style.color = 'var(--text-2)';
            } else if (deterrent.active_count > 0) {
                detEl.textContent = `ACTIVE (${Object.keys(deterrent.active || {}).join(', ')})`;
                detEl.style.color = 'var(--red)';
            } else {
                detEl.textContent = 'READY'; detEl.style.color = 'var(--green)';
            }
        }

        const tracksEl = document.getElementById('dash-tracks-count');
        if (tracksEl) {
            tracksEl.textContent = tracks.length || '0';
            tracksEl.style.color = tracks.length > 0 ? 'var(--amber)' : 'var(--text-2)';
        }
    } catch (err) {
        // non-critical
    }
}

// ─────────────────────────────────────────
// Cameras Page
// ─────────────────────────────────────────
async function loadCameras() {
    try {
        const cameras = await api.get('/api/cameras');
        cachedCameras = cameras;
        renderCamerasGrid(cameras);
    } catch (err) {
        console.error('Cameras load error:', err);
    }
}

function renderCamerasGrid(cameras) {
    const container = document.getElementById('cameras-grid');
    if (!container) return;
    if (!cameras.length) {
        container.innerHTML = '<div class="card" style="text-align:center; color:var(--text-2);">NO CAMERAS — RESCAN</div>';
        return;
    }
    container.innerHTML = cameras.map(cam => {
        let feedHtml;
        if (!cam.capturing) {
            feedHtml = `<div style="width:100%; height:100%; display:flex; align-items:center; justify-content:center; color:var(--text-2);">IDLE</div>`;
        } else {
            feedHtml = `<img data-snapshot="${cam.name}" src="/api/cameras/${cam.name}/frame?t=${Date.now()}" style="width:100%; height:100%; object-fit:contain; display:block;" alt="${cam.name}">`;
        }
        return `
        <div style="border: 1px solid var(--border); overflow: hidden;">
            <div style="height: 180px; background: var(--bg-0); position: relative;">
                ${feedHtml}
                <div style="position:absolute; top:0; left:0; background:rgba(0,0,0,0.75); padding:1px 6px; font-size:10px; display:flex; align-items:center; gap:4px;">
                    <span class="status-dot ${cam.online ? 'dot-green' : 'dot-red'}"></span>
                    ${cam.online ? 'ONLINE' : 'OFFLINE'}
                </div>
            </div>
            <div style="padding:4px 6px; display:flex; align-items:center; justify-content:space-between; background:var(--bg-2); border-top: 1px solid var(--border);">
                <div>
                    <div style="font-size:11px; color:var(--text-0);">${cam.name}</div>
                    <div style="font-size:9px; color:var(--text-2);">${cam.ip} · ${cam.type}</div>
                </div>
                <div style="display:flex; gap:3px;">
                    ${cam.capturing
                        ? `<button onclick="stopCapture('${cam.name}')" class="btn btn-red">STOP</button>`
                        : `<button onclick="startCapture('${cam.name}')" class="btn btn-green">START</button>`
                    }
                </div>
            </div>
        </div>
    `; }).join('');

    // Ensure snapshot polling is running for camera images
    startSnapshotPolling();
}

async function rescanCameras() {
    try {
        const result = await api.post('/api/cameras/rescan');
        showToast(`Scan: ${result.cameras_online} online` +
            (result.started_capture.length ? `, started: ${result.started_capture.join(', ')}` : ''));
        loadCameras();
    } catch (err) {
        showToast('Rescan failed: ' + err.message, 'error');
    }
}

async function startCapture(name) {
    try {
        await api.post(`/api/cameras/${name}/capture/start`);
        showToast(`Capture started: ${name}`);
        refreshCurrentPage();
    } catch (err) {
        showToast(`Failed: ${err.message}`, 'error');
    }
}

async function stopCapture(name) {
    try {
        await api.post(`/api/cameras/${name}/capture/stop`);
        showToast(`Capture stopped: ${name}`);
        refreshCurrentPage();
    } catch (err) {
        showToast(`Failed: ${err.message}`, 'error');
    }
}

// ─────────────────────────────────────────
// Events Page
// ─────────────────────────────────────────
async function loadEventDates() {
    try {
        const dates = await api.get('/api/events/dates');
        const select = document.getElementById('event-date-select');
        if (!select) return;
        const currentVal = select.value;
        select.innerHTML = '<option value="">date...</option>' +
            dates.map(d => `<option value="${d.date}">${d.date} (${d.count})</option>`).join('');
        if (currentVal) { select.value = currentVal; loadEventsForDate(currentVal); }
    } catch (err) {
        console.error('Event dates load error:', err);
    }
}

async function loadEventsForDate(dateStr) {
    if (!dateStr) return;
    try {
        allEvents = await api.get(`/api/events/${dateStr}`);
        filterEvents();
    } catch (err) {
        console.error('Events load error:', err);
    }
}

function filterEvents() {
    const classFilter = document.getElementById('event-class-filter')?.value;
    let filtered = allEvents;
    if (classFilter) filtered = allEvents.filter(e => e.class === classFilter);
    renderEventsTable(filtered);
}

function renderEventsTable(events) {
    const tbody = document.getElementById('events-table');
    if (!tbody) return;
    if (!events.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-2);">--</td></tr>';
        return;
    }
    tbody.innerHTML = events.map(e => {
        const isPred = e.is_predator;
        let snapshotCell = '<span style="color:var(--text-2);">--</span>';
        if (e.snapshot) {
            const parts = e.snapshot.replace(/\\/g, '/').split('/');
            const filename = parts[parts.length - 1];
            const dateDir = parts[parts.length - 2];
            snapshotCell = `<button onclick="openSnapshot('/api/snapshots/${dateDir}/${filename}')" style="color:var(--blue); background:none; border:none; cursor:pointer; font-size:10px; font-family:var(--mono); text-decoration:underline;">VIEW</button>`;
        }
        const badgeClass = isPred ? 'badge-red' : 'badge-mute';
        return `<tr>
            <td>${formatTime(e.timestamp)}</td>
            <td>${e.camera || '--'}</td>
            <td><span class="badge ${badgeClass}">${e.class || '?'}</span></td>
            <td>${(e.confidence * 100).toFixed(0)}%</td>
            <td>${snapshotCell}</td>
        </tr>`;
    }).join('');
}

function openSnapshot(url) {
    const modal = document.getElementById('snapshot-modal');
    const img = document.getElementById('snapshot-modal-img');
    if (modal && img) { img.src = url; modal.style.display = 'flex'; }
}

function closeSnapshot() {
    const modal = document.getElementById('snapshot-modal');
    const img = document.getElementById('snapshot-modal-img');
    if (modal) modal.style.display = 'none';
    if (img) img.src = '';
}

// ─────────────────────────────────────────
// Alerts Page
// ─────────────────────────────────────────
async function loadAlerts() {
    try {
        const alerts = await api.get('/api/alerts/recent?limit=30');
        renderAlertsTable(alerts);
    } catch (err) {
        console.error('Alerts load error:', err);
    }
}

function renderAlertsTable(alerts) {
    const tbody = document.getElementById('alerts-table');
    if (!tbody) return;
    if (!alerts.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-2);">--</td></tr>';
        return;
    }
    tbody.innerHTML = alerts.map(a => `
        <tr>
            <td>${formatTime(a.timestamp || a.alerted_at)}</td>
            <td>${a.camera || a.camera_id || '--'}</td>
            <td>${(a.classes || []).map(c => `<span class="badge badge-red">${c}</span>`).join(' ')}</td>
            <td style="color:${a.sent || a.delivered ? 'var(--green)' : 'var(--red)'};">${a.sent || a.delivered ? 'SENT' : 'FAIL'}</td>
        </tr>
    `).join('');
}

async function sendTestAlert() {
    const btn = document.getElementById('test-alert-btn');
    const result = document.getElementById('test-alert-result');
    if (btn) { btn.disabled = true; btn.textContent = 'SENDING...'; }
    try {
        const res = await api.post('/api/alerts/test');
        if (result) {
            result.style.display = 'block';
            result.style.color = res.ok ? 'var(--green)' : 'var(--red)';
            result.textContent = res.message;
        }
    } catch (err) {
        if (result) {
            result.style.display = 'block';
            result.style.color = 'var(--red)';
            result.textContent = err.message;
        }
    }
    if (btn) { btn.disabled = false; btn.textContent = 'TEST ALERT'; }
    setTimeout(() => { if (result) result.style.display = 'none'; }, 4000);
}

// ─────────────────────────────────────────
// PTZ Control Page
// ─────────────────────────────────────────
async function loadPTZ() {
    try {
        const [cameras, ptzStatus, deterrentStatus, tracks] = await Promise.all([
            api.get('/api/cameras'),
            api.get('/api/ptz/status').catch(() => ({ patrol_active: false, patrol_paused: false })),
            api.get('/api/deterrent/status').catch(() => ({ enabled: false, active_count: 0 })),
            api.get('/api/tracks/active').catch(() => []),
        ]);

        const select = document.getElementById('ptz-camera-select');
        if (select) {
            const ptzCameras = cameras.filter(c => c.type === 'ptz' && c.online);
            select.innerHTML = ptzCameras.length
                ? ptzCameras.map(c => `<option value="${c.name}">${c.name}</option>`).join('')
                : '<option value="">--</option>';
        }

        const statusEl = document.getElementById('ptz-patrol-status');
        if (statusEl) {
            if (ptzStatus.patrol_active) {
                statusEl.textContent = ptzStatus.patrol_paused ? 'PAUSED' : 'ACTIVE';
                statusEl.style.color = ptzStatus.patrol_paused ? 'var(--amber)' : 'var(--green)';
            } else {
                statusEl.textContent = 'OFF'; statusEl.style.color = 'var(--text-2)';
            }
        }

        renderPresetButtons();

        const detEl = document.getElementById('deterrent-status');
        if (detEl) {
            if (!deterrentStatus.enabled) {
                detEl.textContent = 'OFF'; detEl.style.color = 'var(--text-2)';
            } else if (deterrentStatus.active_count > 0) {
                detEl.textContent = `ACTIVE: ${Object.keys(deterrentStatus.active).join(', ')}`; detEl.style.color = 'var(--red)';
            } else {
                detEl.textContent = 'READY'; detEl.style.color = 'var(--green)';
            }
        }

        const tracksEl = document.getElementById('active-tracks');
        if (tracksEl) {
            if (tracks.length) {
                tracksEl.innerHTML = tracks.map(t =>
                    `<div style="display:flex; justify-content:space-between; padding:1px 0;">
                        <span style="color:${t.is_predator ? 'var(--red)' : 'var(--text-1)'};">${t.class_name}</span>
                        <span style="color:var(--text-2);">${t.detection_count}x ${t.duration_sec}s</span>
                    </div>`
                ).join('');
            } else {
                tracksEl.textContent = '--'; tracksEl.style.color = 'var(--text-2)';
            }
        }
    } catch (err) {
        console.error('PTZ load error:', err);
    }
}

async function renderPresetButtons() {
    const container = document.getElementById('ptz-presets');
    if (!container) return;
    try {
        const config = await api.get('/api/config');
        const presets = (config.ptz || {}).presets || [];
        if (!presets.length) {
            container.innerHTML = '<div style="color:var(--text-2);">--</div>';
            return;
        }
        container.innerHTML = presets.map((p, i) =>
            `<button onclick="goToPreset(${i})" class="btn" style="width:100%; display:flex; justify-content:space-between;">
                <span>${p.name}</span>
                <span style="color:var(--text-2);">${p.dwell || 30}s</span>
            </button>`
        ).join('');
    } catch {
        if (container) container.innerHTML = '<div style="color:var(--text-2);">--</div>';
    }
}

function getSelectedPTZCamera() {
    return document.getElementById('ptz-camera-select')?.value || '';
}

async function ptzMove(direction) {
    const cam = getSelectedPTZCamera();
    if (!cam) return showToast('No PTZ camera', 'error');
    const dirs = {
        up: { pan: 0, tilt: 1 }, down: { pan: 0, tilt: -1 },
        left: { pan: -1, tilt: 0 }, right: { pan: 1, tilt: 0 },
    };
    const d = dirs[direction] || { pan: 0, tilt: 0 };
    try {
        await api.post(`/api/ptz/${cam}/move`, { pan: d.pan, tilt: d.tilt, zoom: 0, speed: 25 });
    } catch (err) {
        showToast('PTZ failed: ' + err.message, 'error');
    }
}

async function ptzStop() {
    const cam = getSelectedPTZCamera();
    if (!cam) return;
    try { await api.post(`/api/ptz/${cam}/stop`); }
    catch (err) { showToast('Stop failed: ' + err.message, 'error'); }
}

async function ptzZoom(direction) {
    const cam = getSelectedPTZCamera();
    if (!cam) return showToast('No PTZ camera', 'error');
    const z = direction === 'in' ? 1 : -1;
    try {
        await api.post(`/api/ptz/${cam}/move`, { pan: 0, tilt: 0, zoom: z, speed: 25 });
        setTimeout(() => api.post(`/api/ptz/${cam}/stop`).catch(() => {}), 500);
    } catch (err) {
        showToast('Zoom failed: ' + err.message, 'error');
    }
}

async function goToPreset(index) {
    const cam = getSelectedPTZCamera();
    if (!cam) return showToast('No PTZ camera', 'error');
    try {
        await api.post(`/api/ptz/${cam}/preset/${index}`);
        showToast(`Preset ${index}`);
    } catch (err) {
        showToast('Preset failed: ' + err.message, 'error');
    }
}

async function toggleSpotlight(on) {
    const cam = getSelectedPTZCamera();
    if (!cam) return showToast('No PTZ camera', 'error');
    try {
        await api.post(`/api/ptz/${cam}/spotlight`, { on, brightness: 100 });
        showToast(on ? 'SPOT ON' : 'SPOT OFF');
    } catch (err) {
        showToast('Spotlight failed: ' + err.message, 'error');
    }
}

async function triggerSiren() {
    const cam = getSelectedPTZCamera();
    if (!cam) return showToast('No PTZ camera', 'error');
    try {
        await api.post(`/api/ptz/${cam}/siren`, { duration: 5 });
        showToast('SIREN 5s');
    } catch (err) {
        showToast('Siren failed: ' + err.message, 'error');
    }
}

// ─────────────────────────────────────────
// Reports Page
// ─────────────────────────────────────────
async function loadReportDates() {
    try {
        const dates = await api.get('/api/reports/dates');
        const select = document.getElementById('report-date-select');
        if (!select) return;
        const currentVal = select.value;
        select.innerHTML = '<option value="">date...</option>' +
            (dates || []).map(d => `<option value="${d}">${d}</option>`).join('');
        if (currentVal) { select.value = currentVal; loadReport(currentVal); }
    } catch (err) {
        console.error('Report dates load error:', err);
    }
}

async function loadReport(dateStr) {
    if (!dateStr) return;
    try {
        const report = await api.get(`/api/reports/${dateStr}`);
        renderReport(report);
    } catch (err) {
        const container = document.getElementById('report-content');
        if (container) container.innerHTML = `<div style="color:var(--red); padding:6px;">${err.message}</div>`;
    }
}

async function generateReport() {
    try {
        showToast('Generating...');
        const report = await api.post('/api/reports/generate', {});
        renderReport(report);
        loadReportDates();
        showToast('Report generated');
    } catch (err) {
        showToast('Failed: ' + err.message, 'error');
    }
}

function renderReport(report) {
    const container = document.getElementById('report-content');
    if (!container) return;
    const stats = report.stats || {};
    const visits = report.predator_visits || [];

    let html = `<div class="card" style="margin-bottom:3px;">
        <div class="card-title">${report.date || '--'}</div>
        <div style="color:var(--text-1);">${report.summary || '--'}</div>
    </div>`;

    html += `<div style="display:grid; grid-template-columns:repeat(4,1fr); gap:3px; margin-bottom:3px;">
        <div class="card"><div class="card-title">DETECTIONS</div><div style="font-size:16px; font-weight:700; color:var(--blue);">${stats.total_detections || 0}</div></div>
        <div class="card"><div class="card-title">PREDATORS</div><div style="font-size:16px; font-weight:700; color:var(--red);">${stats.predator_detections || 0}</div></div>
        <div class="card"><div class="card-title">ALERTS</div><div style="font-size:16px; font-weight:700; color:var(--amber);">${stats.alerts_sent || 0}</div></div>
        <div class="card"><div class="card-title">DETERRENT %</div><div style="font-size:16px; font-weight:700; color:var(--green);">${((stats.deterrent_success_rate || 0) * 100).toFixed(0)}%</div></div>
    </div>`;

    const species = stats.species_counts || {};
    if (Object.keys(species).length) {
        const maxCount = Math.max(...Object.values(species));
        const predSet = new Set(['hawk','bobcat','coyote','fox','raccoon','possum','wild_cat']);
        html += `<div class="card" style="margin-bottom:3px;">
            <div class="card-title">SPECIES</div>
            ${Object.entries(species).sort((a, b) => b[1] - a[1]).map(([name, count]) => {
                const pct = maxCount > 0 ? (count / maxCount * 100) : 0;
                const color = predSet.has(name) ? 'var(--red)' : 'var(--blue)';
                return `<div style="display:flex; align-items:center; gap:4px; margin-bottom:1px;">
                    <span style="width:70px; text-align:right; color:var(--text-1);">${name}</span>
                    <div style="flex:1; background:var(--bg-0); height:6px;"><div style="background:${color}; height:6px; width:${pct}%;"></div></div>
                    <span style="color:var(--text-2); width:20px;">${count}</span>
                </div>`;
            }).join('')}
        </div>`;
    }

    if (visits.length) {
        html += `<div class="card" style="margin-bottom:3px;">
            <div class="card-title">PREDATOR VISITS</div>
            <table class="tbl"><thead><tr>
                <th>TIME</th><th>SPECIES</th><th>DUR</th><th>CONF</th><th>ACTION</th><th>RESULT</th>
            </tr></thead><tbody>${visits.map(v => {
                const dur = v.duration_seconds || 0;
                const durStr = dur < 60 ? `${dur.toFixed(0)}s` : `${(dur/60).toFixed(1)}m`;
                return `<tr>
                    <td>${v.time}</td>
                    <td style="color:var(--red);">${v.species}</td>
                    <td>${durStr}</td>
                    <td>${((v.max_confidence || 0) * 100).toFixed(0)}%</td>
                    <td>${(v.deterrent || []).join(', ') || '--'}</td>
                    <td style="color:${v.outcome === 'deterred' ? 'var(--green)' : 'var(--text-2)'};">${v.outcome}</td>
                </tr>`;
            }).join('')}</tbody></table>
        </div>`;
    }

    const hourly = stats.activity_by_hour || {};
    if (Object.keys(hourly).length) {
        const maxH = Math.max(...Object.values(hourly));
        html += `<div class="card">
            <div class="card-title">HOURLY ACTIVITY</div>
            <div style="display:flex; align-items:flex-end; gap:1px; height:50px;">
                ${Array.from({length: 24}, (_, h) => {
                    const count = hourly[String(h)] || 0;
                    const pct = maxH > 0 ? (count / maxH * 100) : 0;
                    return `<div style="flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%;">
                        <div style="width:100%; background:rgba(88,166,255,0.5); height:${pct}%;" title="${h}:00 — ${count}"></div>
                        <span style="font-size:8px; color:var(--text-2); margin-top:1px;">${h}</span>
                    </div>`;
                }).join('')}
            </div>
        </div>`;
    }

    container.innerHTML = html;
}

// ─────────────────────────────────────────
// Settings Page
// ─────────────────────────────────────────
async function loadSettings() {
    try {
        const config = await api.get('/api/config');
        populateSettingsForm(config);
    } catch (err) {
        console.error('Settings load error:', err);
    }
}

function populateSettingsForm(config) {
    const det = config.detection || {};
    const alerts = config.alerts || {};

    const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
    setVal('cfg-confidence', det.confidence_threshold ?? 0.45);
    setVal('cfg-bird-min', det.bird_min_bbox_width_pct ?? 8);
    setVal('cfg-dwell', det.min_dwell_frames ?? 3);
    setVal('cfg-interval', det.frame_interval_seconds ?? 1.0);
    setVal('cfg-predators', (det.predator_classes || []).join(','));
    setVal('cfg-ignore', (det.ignore_classes || []).join(','));
    setVal('cfg-zone', JSON.stringify(det.no_alert_zone || []));

    const thresholds = det.class_confidence_thresholds || {};
    const container = document.getElementById('class-thresholds');
    if (container) {
        const classes = [...new Set([...(det.predator_classes || []), ...Object.keys(thresholds)])];
        container.innerHTML = classes.map(cls => `
            <div>
                <label style="display:block; font-size:9px; color:var(--text-2); margin-bottom:1px;">${cls}</label>
                <input type="number" id="cfg-thresh-${cls}" min="0" max="1" step="0.05"
                    value="${thresholds[cls] ?? det.confidence_threshold ?? 0.45}">
            </div>
        `).join('');
    }

    setVal('cfg-webhook', alerts.discord_webhook_url || '');
    setVal('cfg-cooldown', det.alert_cooldown_seconds ?? 300);
    const snapEl = document.getElementById('cfg-snapshot');
    if (snapEl) snapEl.checked = alerts.include_snapshot !== false;
}

async function saveDetectionConfig() {
    const g = (id) => document.getElementById(id)?.value || '';
    const predators = g('cfg-predators').split(',').map(s => s.trim()).filter(Boolean);
    const ignore = g('cfg-ignore').split(',').map(s => s.trim()).filter(Boolean);

    const classThresholds = {};
    predators.forEach(cls => {
        const input = document.getElementById(`cfg-thresh-${cls}`);
        if (input) classThresholds[cls] = parseFloat(input.value);
    });

    let zone = [];
    try {
        const zoneStr = g('cfg-zone').trim();
        if (zoneStr) zone = JSON.parse(zoneStr);
    } catch {
        showToast('Invalid zone JSON', 'error');
        return;
    }

    const body = {
        confidence_threshold: parseFloat(g('cfg-confidence')),
        bird_min_bbox_width_pct: parseFloat(g('cfg-bird-min')),
        min_dwell_frames: parseInt(g('cfg-dwell')),
        frame_interval_seconds: parseFloat(g('cfg-interval')),
        predator_classes: predators,
        ignore_classes: ignore,
        no_alert_zone: zone,
        class_confidence_thresholds: classThresholds,
    };

    try {
        const res = await api.post('/api/config/detection', body);
        showSaveResult('detection-save-result', res.ok, `Updated: ${res.updated.join(', ')}`);
    } catch (err) {
        showSaveResult('detection-save-result', false, err.message);
    }
}

async function saveAlertConfig() {
    const g = (id) => document.getElementById(id)?.value || '';
    const body = {
        alert_cooldown_seconds: parseInt(g('cfg-cooldown')),
        include_snapshot: document.getElementById('cfg-snapshot')?.checked ?? true,
    };
    const webhook = g('cfg-webhook');
    if (webhook && !webhook.startsWith('...')) body.discord_webhook_url = webhook;

    try {
        const res = await api.post('/api/config/alerts', body);
        showSaveResult('alert-save-result', res.ok, `Updated: ${res.updated.join(', ')}`);
    } catch (err) {
        showSaveResult('alert-save-result', false, err.message);
    }
}

function showSaveResult(elementId, success, message) {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.style.display = 'inline';
    el.style.color = success ? 'var(--green)' : 'var(--red)';
    el.textContent = message;
    setTimeout(() => { el.style.display = 'none'; }, 4000);
}

// ─────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────
function formatUptime(seconds) {
    if (!seconds || seconds < 0) return '--';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}h${m}m`;
    if (m > 0) return `${m}m${s}s`;
    return `${s}s`;
}

function formatTime(timestamp) {
    if (!timestamp) return '--';
    try {
        const d = new Date(timestamp);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    } catch {
        return timestamp;
    }
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    const bg = type === 'error' ? 'var(--red)' : 'var(--green)';
    toast.style.cssText = `position:fixed; bottom:8px; right:8px; background:${bg}; color:#000; padding:3px 8px; font-size:10px; font-family:var(--mono); z-index:100; opacity:1; transition:opacity 0.3s;`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; }, 2000);
    setTimeout(() => toast.remove(), 2500);
}

// ─────────────────────────────────────────
// Layout — adapt detection panel height
// ─────────────────────────────────────────
function adjustDashboardLayout() {
    if (currentPage !== 'dashboard') return;
    const detPanel = document.getElementById('det-panel');
    if (detPanel) {
        detPanel.style.maxHeight = '110px';
        detPanel.style.overflowY = 'auto';
    }
}

// ─────────────────────────────────────────
// Init
// ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    navigate('dashboard');
    adjustDashboardLayout();
    window.addEventListener('resize', adjustDashboardLayout);

    // Auto-refresh every 5s on dashboard
    refreshInterval = setInterval(() => {
        if (currentPage === 'dashboard') loadDashboard();
    }, 5000);
});

// Escape closes snapshot modal
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSnapshot();
});

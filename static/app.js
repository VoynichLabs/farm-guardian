// Author: Claude Sonnet 4-6
// Date: 03-April-2026
// PURPOSE: Farm Guardian Dashboard — Frontend Logic.
//          Vanilla JS, no build step. Communicates with FastAPI backend via /api/ endpoints.
//          Redesigned for single-screen Bloomberg-terminal-style layout.
// SRP/DRY check: Pass — single responsibility is UI state and API communication.

// ─────────────────────────────────────────
// State
// ─────────────────────────────────────────
let currentPage = 'dashboard';
let allEvents = [];       // current date's events (for filtering)
let refreshInterval = null;
const DASH_CAM = 'house-yard';   // hardcoded primary camera for dashboard PTZ controls

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
// Router
// ─────────────────────────────────────────
function navigate(page) {
    currentPage = page;
    // Hide all pages
    document.querySelectorAll('.page').forEach(el => {
        el.style.display = 'none';
    });
    const target = document.getElementById(`page-${page}`);
    if (target) {
        // Dashboard uses flex column layout; scrollable pages use flex column too
        target.style.display = 'flex';
        target.classList.add('fade-in');
    }
    // Update nav highlight
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.nav === page);
    });
    // Update page title in status bar
    const titles = { dashboard: 'Dashboard', cameras: 'Cameras', events: 'Events', alerts: 'Alerts', ptz: 'PTZ', reports: 'Reports', settings: 'Settings' };
    const titleEl = document.getElementById('page-title');
    if (titleEl) titleEl.textContent = titles[page] || page;

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
// Status Bar (always visible at top)
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
        if (dot) dot.style.background = '#ef4444';
        if (sidebarDot) sidebarDot.style.background = '#ef4444';
        if (statusEl) statusEl.textContent = 'Offline';
        return;
    }

    if (dot) { dot.style.background = '#22c55e'; dot.style.animation = 'pulse 2s infinite'; }
    if (sidebarDot) { sidebarDot.style.background = '#22c55e'; }
    if (statusEl) { statusEl.textContent = 'Online'; statusEl.style.color = '#4ade80'; }
    if (uptimeEl) uptimeEl.textContent = formatUptime(status.uptime_seconds);
    if (framesEl) framesEl.textContent = (status.frames_processed || 0).toLocaleString();
    if (detectionsEl) detectionsEl.textContent = status.detections_today ?? '—';

    if (lastEl) {
        if (lastDetection) {
            const t = formatTime(lastDetection.timestamp);
            const cls = lastDetection.class || lastDetection.class_name || '?';
            lastEl.textContent = `${t} — ${cls}`;
            lastEl.style.color = lastDetection.is_predator ? '#f87171' : '#94a3b8';
        } else {
            lastEl.textContent = '—';
        }
    }
}

// ─────────────────────────────────────────
// Dashboard Page
// ─────────────────────────────────────────
async function loadDashboard() {
    try {
        const [status, cameras, detections] = await Promise.all([
            api.get('/api/status'),
            api.get('/api/cameras'),
            api.get('/api/detections/recent?limit=15'),
        ]);
        const lastDet = detections && detections.length ? detections[0] : null;
        updateStatusBar(status, lastDet);
        renderDashboardFeed(cameras);
        renderDashboardDetections(detections);
        // Also load PTZ/deterrent status for the dashboard panel
        loadDashboardPTZStatus();
    } catch (err) {
        console.error('Dashboard load error:', err);
    }
}

function renderDashboardFeed(cameras) {
    if (!cameras || !cameras.length) return;

    // Update each camera feed by matching config name to DOM elements
    const feedMap = [
        { name: DASH_CAM, img: 'main-feed-img', placeholder: 'main-feed-placeholder', dot: 'feed-status-dot' },
        { name: 'nesting-box', img: 'nest-feed-img', placeholder: 'nest-feed-placeholder', dot: 'nest-status-dot' },
    ];

    for (const f of feedMap) {
        const cam = cameras.find(c => c.name === f.name);
        const img = document.getElementById(f.img);
        const placeholder = document.getElementById(f.placeholder);
        const dot = document.getElementById(f.dot);

        if (!cam) {
            if (img) img.style.display = 'none';
            if (placeholder) { placeholder.style.display = 'flex'; placeholder.textContent = `${f.name} not found`; }
            if (dot) dot.style.background = '#ef4444';
            continue;
        }

        if (dot) dot.style.background = cam.online ? '#22c55e' : '#ef4444';

        if (cam.capturing && cam.online) {
            if (img) { img.style.display = 'block'; img.src = `/api/cameras/${cam.name}/stream`; }
            if (placeholder) placeholder.style.display = 'none';
        } else {
            if (img) img.style.display = 'none';
            if (placeholder) {
                placeholder.style.display = 'flex';
                placeholder.textContent = cam.online ? 'Not capturing' : `${cam.name} offline`;
            }
        }
    }
}

function handleFeedError(imgEl) {
    imgEl.style.display = 'none';
    const placeholder = document.getElementById('main-feed-placeholder');
    if (placeholder) { placeholder.style.display = 'flex'; placeholder.textContent = 'Feed unavailable'; }
}

function renderDashboardDetections(detections) {
    const tbody = document.getElementById('dashboard-detections');
    if (!tbody) return;
    if (!detections || !detections.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="padding:6px 8px; text-align:center; color:#475569; font-size:0.7rem;">No detections yet</td></tr>';
        return;
    }
    // Show up to 12 rows max to keep it compact
    const rows = detections.slice(0, 12);
    tbody.innerHTML = rows.map(d => {
        const isPred = d.is_predator;
        const cls = d.class || d.class_name || '?';
        const cam = d.camera || d.camera_id || '?';
        const conf = typeof d.confidence === 'number' ? `${(d.confidence * 100).toFixed(0)}%` : '?';
        const ts = formatTime(d.timestamp || d.detected_at);
        return `<tr style="border-top:1px solid #1e293b;">
            <td style="color:#94a3b8;">${ts}</td>
            <td style="color:#64748b;">${cam}</td>
            <td><span style="display:inline-block; padding:0 4px; border-radius:2px; font-size:0.65rem; ${isPred ? 'background:rgba(239,68,68,0.15); color:#f87171;' : 'background:rgba(71,85,105,0.3); color:#94a3b8;'}">${cls}</span></td>
            <td style="color:#64748b;">${conf}</td>
            <td style="color:${isPred ? '#f87171' : '#334155'}; font-size:0.65rem;">${isPred ? '⚠ PRED' : '·'}</td>
        </tr>`;
    }).join('');
}

// ─────────────────────────────────────────
// Dashboard PTZ helpers (hardcoded to DASH_CAM)
// ─────────────────────────────────────────
function setDashPtzFeedback(msg, color = '#475569') {
    const el = document.getElementById('dash-ptz-feedback');
    if (el) { el.textContent = msg; el.style.color = color; }
    setTimeout(() => { if (el) el.textContent = ''; }, 2000);
}

async function dashPtzMove(direction) {
    const dirs = {
        up: { pan: 0, tilt: 1 }, down: { pan: 0, tilt: -1 },
        left: { pan: -1, tilt: 0 }, right: { pan: 1, tilt: 0 },
    };
    const d = dirs[direction] || { pan: 0, tilt: 0 };
    try {
        const res = await api.post(`/api/ptz/${DASH_CAM}/move`, { pan: d.pan, tilt: d.tilt, zoom: 0, speed: 25 });
        if (!res.ok) setDashPtzFeedback('PTZ failed', '#f87171');
    } catch (err) {
        setDashPtzFeedback('Error: ' + err.message, '#f87171');
    }
}

async function dashPtzStop() {
    try {
        await api.post(`/api/ptz/${DASH_CAM}/stop`);
    } catch (err) {
        setDashPtzFeedback('Stop failed', '#f87171');
    }
}

async function dashPtzZoom(direction) {
    const z = direction === 'in' ? 1 : -1;
    try {
        await api.post(`/api/ptz/${DASH_CAM}/move`, { pan: 0, tilt: 0, zoom: z, speed: 25 });
        setTimeout(() => api.post(`/api/ptz/${DASH_CAM}/stop`).catch(() => {}), 500);
    } catch (err) {
        setDashPtzFeedback('Zoom failed', '#f87171');
    }
}

async function dashSpotlight(on) {
    try {
        const res = await api.post(`/api/ptz/${DASH_CAM}/spotlight`, { on, brightness: 100 });
        setDashPtzFeedback(on ? '☀ Spotlight ON' : 'Spotlight off', on ? '#fbbf24' : '#475569');
    } catch (err) {
        setDashPtzFeedback('Spotlight failed', '#f87171');
    }
}

async function dashSiren() {
    try {
        const res = await api.post(`/api/ptz/${DASH_CAM}/siren`, { duration: 5 });
        setDashPtzFeedback('⚠ Siren triggered (5s)', '#f87171');
    } catch (err) {
        setDashPtzFeedback('Siren failed', '#f87171');
    }
}

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
                patrolEl.textContent = ptzStatus.patrol_paused ? 'Paused (deterrent)' : 'Running';
                patrolEl.style.color = ptzStatus.patrol_paused ? '#fbbf24' : '#4ade80';
            } else {
                patrolEl.textContent = 'Off'; patrolEl.style.color = '#475569';
            }
        }

        const detEl = document.getElementById('dash-deterrent-status');
        if (detEl) {
            if (!deterrent.enabled) {
                detEl.textContent = 'Disabled'; detEl.style.color = '#475569';
            } else if (deterrent.active_count > 0) {
                detEl.textContent = `Active (${Object.keys(deterrent.active || {}).join(', ')})`;
                detEl.style.color = '#f87171';
            } else {
                detEl.textContent = 'Ready'; detEl.style.color = '#4ade80';
            }
        }

        const tracksEl = document.getElementById('dash-tracks-count');
        if (tracksEl) {
            tracksEl.textContent = tracks.length || '0';
            tracksEl.style.color = tracks.length > 0 ? '#fbbf24' : '#475569';
        }
    } catch (err) {
        // Non-critical — ignore
    }
}

// ─────────────────────────────────────────
// Cameras Page
// ─────────────────────────────────────────
async function loadCameras() {
    try {
        const cameras = await api.get('/api/cameras');
        renderCamerasGrid(cameras);
    } catch (err) {
        console.error('Cameras load error:', err);
    }
}

function renderCamerasGrid(cameras) {
    const container = document.getElementById('cameras-grid');
    if (!container) return;
    if (!cameras.length) {
        container.innerHTML = '<div style="background:#1e293b; border:1px solid #334155; border-radius:4px; padding:12px; text-align:center; color:#475569; font-size:0.75rem;">No cameras found — click Rescan</div>';
        return;
    }
    container.innerHTML = cameras.map(cam => `
        <div style="background:#1e293b; border:1px solid #334155; border-radius:4px; overflow:hidden;">
            <div style="height:200px; background:#0a0f1e; position:relative;">
                ${cam.capturing
                    ? `<img src="/api/cameras/${cam.name}/stream" style="width:100%; height:100%; object-fit:contain; display:block;" alt="${cam.name}">`
                    : `<div style="width:100%; height:100%; display:flex; align-items:center; justify-content:center; color:#475569; font-size:0.75rem;">Capture stopped</div>`
                }
                <div style="position:absolute; top:4px; right:4px; background:rgba(0,0,0,0.65); border-radius:3px; padding:2px 6px; font-size:0.65rem; display:flex; align-items:center; gap:4px;">
                    <span style="width:6px; height:6px; border-radius:50%; background:${cam.online ? '#22c55e' : '#ef4444'}; display:inline-block;"></span>
                    ${cam.online ? 'Online' : 'Offline'}
                </div>
            </div>
            <div style="padding:6px 8px; display:flex; align-items:center; justify-content:space-between;">
                <div>
                    <div style="font-size:0.75rem; font-weight:600;">${cam.name}</div>
                    <div style="font-size:0.65rem; color:#475569;">${cam.ip} · ${cam.type}</div>
                </div>
                <div style="display:flex; gap:4px;">
                    ${cam.capturing
                        ? `<button onclick="stopCapture('${cam.name}')" style="padding:2px 8px; background:rgba(239,68,68,0.3); border:1px solid rgba(239,68,68,0.4); border-radius:3px; font-size:0.65rem; color:#f87171; cursor:pointer;">Stop</button>`
                        : `<button onclick="startCapture('${cam.name}')" style="padding:2px 8px; background:rgba(34,197,94,0.2); border:1px solid rgba(34,197,94,0.3); border-radius:3px; font-size:0.65rem; color:#4ade80; cursor:pointer;">Start</button>`
                    }
                </div>
            </div>
        </div>
    `).join('');
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
        showToast(`Capture started for ${name}`);
        refreshCurrentPage();
    } catch (err) {
        showToast(`Failed to start ${name}: ${err.message}`, 'error');
    }
}

async function stopCapture(name) {
    try {
        await api.post(`/api/cameras/${name}/capture/stop`);
        showToast(`Capture stopped for ${name}`);
        refreshCurrentPage();
    } catch (err) {
        showToast(`Failed to stop ${name}: ${err.message}`, 'error');
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
        select.innerHTML = '<option value="">Select date...</option>' +
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
        tbody.innerHTML = '<tr><td colspan="5" style="padding:8px; text-align:center; color:#475569; font-size:0.7rem;">No events match filters</td></tr>';
        return;
    }
    tbody.innerHTML = events.map(e => {
        const isPred = e.is_predator;
        let snapshotCell = '<span style="color:#334155;">—</span>';
        if (e.snapshot) {
            const parts = e.snapshot.replace(/\\/g, '/').split('/');
            const filename = parts[parts.length - 1];
            const dateDir = parts[parts.length - 2];
            snapshotCell = `<button onclick="openSnapshot('/api/snapshots/${dateDir}/${filename}')" style="color:#60a5fa; background:none; border:none; cursor:pointer; font-size:0.7rem; text-decoration:underline;">View</button>`;
        }
        return `<tr style="border-top:1px solid #1e293b;">
            <td style="color:#94a3b8;">${formatTime(e.timestamp)}</td>
            <td style="color:#64748b;">${e.camera || '?'}</td>
            <td><span style="display:inline-block; padding:0 4px; border-radius:2px; font-size:0.65rem; ${isPred ? 'background:rgba(239,68,68,0.15); color:#f87171;' : 'background:rgba(71,85,105,0.3); color:#94a3b8;'}">${e.class || '?'}</span></td>
            <td style="color:#64748b;">${(e.confidence * 100).toFixed(0)}%</td>
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
        tbody.innerHTML = '<tr><td colspan="4" style="padding:8px; text-align:center; color:#475569; font-size:0.7rem;">No alerts sent yet</td></tr>';
        return;
    }
    tbody.innerHTML = alerts.map(a => `
        <tr style="border-top:1px solid #1e293b;">
            <td style="color:#94a3b8;">${formatTime(a.timestamp || a.alerted_at)}</td>
            <td style="color:#64748b;">${a.camera || a.camera_id || '—'}</td>
            <td>${(a.classes || []).map(c =>
                `<span style="display:inline-block; padding:0 4px; border-radius:2px; font-size:0.65rem; background:rgba(239,68,68,0.15); color:#f87171; margin-right:2px;">${c}</span>`
            ).join('')}</td>
            <td style="font-size:0.65rem; color:${a.sent || a.delivered ? '#4ade80' : '#f87171'};">${a.sent || a.delivered ? 'Sent' : 'Failed'}</td>
        </tr>
    `).join('');
}

async function sendTestAlert() {
    const btn = document.getElementById('test-alert-btn');
    const result = document.getElementById('test-alert-result');
    if (btn) { btn.disabled = true; btn.textContent = 'Sending...'; }
    try {
        const res = await api.post('/api/alerts/test');
        if (result) {
            result.style.display = 'block';
            result.style.background = res.ok ? 'rgba(6,78,59,0.4)' : 'rgba(127,29,29,0.4)';
            result.style.color = res.ok ? '#4ade80' : '#f87171';
            result.textContent = res.message;
        }
    } catch (err) {
        if (result) {
            result.style.display = 'block';
            result.style.background = 'rgba(127,29,29,0.4)';
            result.style.color = '#f87171';
            result.textContent = 'Request failed: ' + err.message;
        }
    }
    if (btn) { btn.disabled = false; btn.textContent = 'Test Alert'; }
    setTimeout(() => { if (result) result.style.display = 'none'; }, 5000);
}

// ─────────────────────────────────────────
// PTZ Control Page (full page — uses dropdown)
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
                : '<option value="">No PTZ cameras online</option>';
        }

        const statusEl = document.getElementById('ptz-patrol-status');
        if (statusEl) {
            if (ptzStatus.patrol_active) {
                statusEl.textContent = ptzStatus.patrol_paused ? 'Patrol paused (deterrent active)' : 'Patrol running';
                statusEl.style.color = ptzStatus.patrol_paused ? '#fbbf24' : '#4ade80';
            } else {
                statusEl.textContent = 'Patrol not active'; statusEl.style.color = '#475569';
            }
        }

        renderPresetButtons();

        const detEl = document.getElementById('deterrent-status');
        if (detEl) {
            if (!deterrentStatus.enabled) {
                detEl.textContent = 'Deterrents disabled'; detEl.style.color = '#475569';
            } else if (deterrentStatus.active_count > 0) {
                detEl.textContent = `Active: ${Object.keys(deterrentStatus.active).join(', ')}`; detEl.style.color = '#f87171';
            } else {
                detEl.textContent = 'Ready (no active threats)'; detEl.style.color = '#4ade80';
            }
        }

        const tracksEl = document.getElementById('active-tracks');
        if (tracksEl) {
            if (tracks.length) {
                tracksEl.innerHTML = tracks.map(t =>
                    `<div style="display:flex; justify-content:space-between; padding:2px 0; font-size:0.7rem;">
                        <span style="color:${t.is_predator ? '#f87171' : '#94a3b8'};">${t.class_name}</span>
                        <span style="color:#475569;">${t.detection_count} det, ${t.duration_sec}s</span>
                    </div>`
                ).join('');
            } else {
                tracksEl.textContent = 'No active tracks'; tracksEl.style.color = '#475569';
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
            container.innerHTML = '<div style="font-size:0.7rem; color:#475569;">No presets configured</div>';
            return;
        }
        container.innerHTML = presets.map((p, i) =>
            `<button onclick="goToPreset(${i})"
                style="display:flex; align-items:center; justify-content:space-between; padding:3px 8px; background:#0f172a; border:1px solid #334155; border-radius:3px; font-size:0.7rem; color:#94a3b8; cursor:pointer; width:100%;">
                <span>${p.name}</span>
                <span style="color:#334155; font-size:0.65rem;">dwell: ${p.dwell || 30}s</span>
            </button>`
        ).join('');
    } catch {
        if (container) container.innerHTML = '<div style="font-size:0.7rem; color:#475569;">Could not load presets</div>';
    }
}

function getSelectedPTZCamera() {
    return document.getElementById('ptz-camera-select')?.value || '';
}

async function ptzMove(direction) {
    const cam = getSelectedPTZCamera();
    if (!cam) return showToast('No PTZ camera selected', 'error');
    const dirs = {
        up: { pan: 0, tilt: 1 }, down: { pan: 0, tilt: -1 },
        left: { pan: -1, tilt: 0 }, right: { pan: 1, tilt: 0 },
    };
    const d = dirs[direction] || { pan: 0, tilt: 0 };
    try {
        await api.post(`/api/ptz/${cam}/move`, { pan: d.pan, tilt: d.tilt, zoom: 0, speed: 25 });
    } catch (err) {
        showToast('PTZ move failed: ' + err.message, 'error');
    }
}

async function ptzStop() {
    const cam = getSelectedPTZCamera();
    if (!cam) return;
    try { await api.post(`/api/ptz/${cam}/stop`); }
    catch (err) { showToast('PTZ stop failed: ' + err.message, 'error'); }
}

async function ptzZoom(direction) {
    const cam = getSelectedPTZCamera();
    if (!cam) return showToast('No PTZ camera selected', 'error');
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
    if (!cam) return showToast('No PTZ camera selected', 'error');
    try {
        await api.post(`/api/ptz/${cam}/preset/${index}`);
        showToast(`Moving to preset ${index}`);
    } catch (err) {
        showToast('Preset goto failed: ' + err.message, 'error');
    }
}

async function toggleSpotlight(on) {
    const cam = getSelectedPTZCamera();
    if (!cam) return showToast('No PTZ camera selected', 'error');
    try {
        await api.post(`/api/ptz/${cam}/spotlight`, { on, brightness: 100 });
        showToast(on ? 'Spotlight on' : 'Spotlight off');
    } catch (err) {
        showToast('Spotlight failed: ' + err.message, 'error');
    }
}

async function triggerSiren() {
    const cam = getSelectedPTZCamera();
    if (!cam) return showToast('No PTZ camera selected', 'error');
    try {
        await api.post(`/api/ptz/${cam}/siren`, { duration: 5 });
        showToast('Siren triggered (5s)');
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
        select.innerHTML = '<option value="">Select date...</option>' +
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
        if (container) container.innerHTML = `<div style="color:#f87171; font-size:0.75rem; padding:8px;">Failed to load report: ${err.message}</div>`;
    }
}

async function generateReport() {
    try {
        showToast('Generating report...');
        const report = await api.post('/api/reports/generate', {});
        renderReport(report);
        loadReportDates();
        showToast('Report generated');
    } catch (err) {
        showToast('Generate failed: ' + err.message, 'error');
    }
}

function renderReport(report) {
    const container = document.getElementById('report-content');
    if (!container) return;
    const stats = report.stats || {};
    const visits = report.predator_visits || [];

    let html = `<div style="background:#1e293b; border:1px solid #334155; border-radius:4px; padding:8px; margin-bottom:6px;">
        <div style="font-size:0.75rem; font-weight:600; margin-bottom:4px;">Summary — ${report.date || ''}</div>
        <div style="font-size:0.7rem; color:#94a3b8;">${report.summary || 'No summary available.'}</div>
    </div>`;

    html += `<div style="display:grid; grid-template-columns:repeat(4,1fr); gap:6px; margin-bottom:6px;">
        <div style="background:#1e293b; border:1px solid #334155; border-radius:4px; padding:6px;">
            <div style="font-size:0.6rem; color:#475569;">Total Detections</div>
            <div style="font-size:1rem; font-weight:700; color:#60a5fa;">${stats.total_detections || 0}</div>
        </div>
        <div style="background:#1e293b; border:1px solid #334155; border-radius:4px; padding:6px;">
            <div style="font-size:0.6rem; color:#475569;">Predator Detections</div>
            <div style="font-size:1rem; font-weight:700; color:#f87171;">${stats.predator_detections || 0}</div>
        </div>
        <div style="background:#1e293b; border:1px solid #334155; border-radius:4px; padding:6px;">
            <div style="font-size:0.6rem; color:#475569;">Alerts Sent</div>
            <div style="font-size:1rem; font-weight:700; color:#fbbf24;">${stats.alerts_sent || 0}</div>
        </div>
        <div style="background:#1e293b; border:1px solid #334155; border-radius:4px; padding:6px;">
            <div style="font-size:0.6rem; color:#475569;">Deterrent Success</div>
            <div style="font-size:1rem; font-weight:700; color:#4ade80;">${((stats.deterrent_success_rate || 0) * 100).toFixed(0)}%</div>
        </div>
    </div>`;

    const species = stats.species_counts || {};
    if (Object.keys(species).length) {
        html += `<div style="background:#1e293b; border:1px solid #334155; border-radius:4px; padding:8px; margin-bottom:6px;">
            <div style="font-size:0.7rem; font-weight:600; margin-bottom:4px;">Species Breakdown</div>
            ${Object.entries(species).sort((a, b) => b[1] - a[1]).map(([name, count]) => {
                const maxCount = Math.max(...Object.values(species));
                const pct = maxCount > 0 ? (count / maxCount * 100) : 0;
                const isPred = new Set(['hawk','bobcat','coyote','fox','raccoon','possum','wild_cat']).has(name);
                return `<div style="display:flex; align-items:center; gap:6px; margin-bottom:2px;">
                    <span style="width:80px; font-size:0.65rem; text-align:right; color:#94a3b8;">${name}</span>
                    <div style="flex:1; background:#0f172a; border-radius:2px; height:8px;">
                        <div style="background:${isPred ? '#ef4444' : '#3b82f6'}; height:8px; border-radius:2px; width:${pct}%;"></div>
                    </div>
                    <span style="font-size:0.65rem; color:#475569; width:24px;">${count}</span>
                </div>`;
            }).join('')}
        </div>`;
    }

    if (visits.length) {
        html += `<div style="background:#1e293b; border:1px solid #334155; border-radius:4px; padding:8px; margin-bottom:6px;">
            <div style="font-size:0.7rem; font-weight:600; margin-bottom:4px;">Predator Visits</div>
            <table style="width:100%; border-collapse:collapse;">
                <thead><tr style="background:#0f172a;">
                    <th style="text-align:left; padding:2px 6px; font-size:0.65rem; color:#475569; font-weight:500;">Time</th>
                    <th style="text-align:left; padding:2px 6px; font-size:0.65rem; color:#475569; font-weight:500;">Species</th>
                    <th style="text-align:left; padding:2px 6px; font-size:0.65rem; color:#475569; font-weight:500;">Duration</th>
                    <th style="text-align:left; padding:2px 6px; font-size:0.65rem; color:#475569; font-weight:500;">Conf</th>
                    <th style="text-align:left; padding:2px 6px; font-size:0.65rem; color:#475569; font-weight:500;">Deterrent</th>
                    <th style="text-align:left; padding:2px 6px; font-size:0.65rem; color:#475569; font-weight:500;">Outcome</th>
                </tr></thead>
                <tbody>${visits.map(v => {
                    const dur = v.duration_seconds || 0;
                    const durStr = dur < 60 ? `${dur.toFixed(0)}s` : `${(dur/60).toFixed(1)}m`;
                    return `<tr style="border-top:1px solid #1e293b;">
                        <td style="padding:2px 6px; font-size:0.7rem; color:#94a3b8;">${v.time}</td>
                        <td style="padding:2px 6px; font-size:0.7rem; color:#f87171;">${v.species}</td>
                        <td style="padding:2px 6px; font-size:0.7rem; color:#64748b;">${durStr}</td>
                        <td style="padding:2px 6px; font-size:0.7rem; color:#64748b;">${((v.max_confidence || 0) * 100).toFixed(0)}%</td>
                        <td style="padding:2px 6px; font-size:0.7rem; color:#64748b;">${(v.deterrent || []).join(', ') || '--'}</td>
                        <td style="padding:2px 6px; font-size:0.7rem; color:${v.outcome === 'deterred' ? '#4ade80' : '#475569'};">${v.outcome}</td>
                    </tr>`;
                }).join('')}</tbody>
            </table>
        </div>`;
    }

    const hourly = stats.activity_by_hour || {};
    if (Object.keys(hourly).length) {
        const maxH = Math.max(...Object.values(hourly));
        html += `<div style="background:#1e293b; border:1px solid #334155; border-radius:4px; padding:8px;">
            <div style="font-size:0.7rem; font-weight:600; margin-bottom:4px;">Activity by Hour</div>
            <div style="display:flex; align-items:flex-end; gap:1px; height:60px;">
                ${Array.from({length: 24}, (_, h) => {
                    const count = hourly[String(h)] || 0;
                    const pct = maxH > 0 ? (count / maxH * 100) : 0;
                    return `<div style="flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%;">
                        <div style="width:100%; background:rgba(59,130,246,0.6); border-radius:1px 1px 0 0;" title="${h}:00 — ${count}" style="height:${pct}%;"></div>
                        <span style="font-size:0.5rem; color:#334155; margin-top:1px;">${h}</span>
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
                <label style="display:block; font-size:0.6rem; color:#475569; margin-bottom:1px;">${cls}</label>
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
        showToast('Invalid JSON for no-alert zone', 'error');
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
    el.style.display = 'block';
    el.style.color = success ? '#4ade80' : '#f87171';
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
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

function formatTime(timestamp) {
    if (!timestamp) return '--';
    try {
        const d = new Date(timestamp);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
        return timestamp;
    }
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    const bg = type === 'error' ? '#dc2626' : '#059669';
    toast.style.cssText = `position:fixed; bottom:12px; right:12px; background:${bg}; color:white; padding:4px 10px; border-radius:4px; font-size:0.7rem; z-index:100; opacity:1; transition:opacity 0.3s;`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; }, 2500);
    setTimeout(() => toast.remove(), 3000);
}

// ─────────────────────────────────────────
// Dashboard resize handler — keep layout tight
// ─────────────────────────────────────────
function adjustDashboardLayout() {
    const dashPage = document.getElementById('page-dashboard');
    if (!dashPage || currentPage !== 'dashboard') return;

    const totalH = window.innerHeight;
    const statusBarH = document.getElementById('status-bar')?.offsetHeight || 22;
    const availH = totalH - statusBarH - 8; // 8px padding

    const mainArea = document.getElementById('dashboard-main-area');
    const detPanel = document.getElementById('det-panel');

    if (!mainArea || !detPanel) return;

    // Detection table gets ~100px (enough for ~8-10 rows at 0.7rem)
    const detH = 100;
    const mainH = availH - detH - 4; // 4px gap between areas

    mainArea.style.height = mainH + 'px';
    mainArea.style.flex = 'none';
    detPanel.style.maxHeight = detH + 'px';
    detPanel.style.overflowY = 'auto';
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

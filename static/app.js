// Farm Guardian Dashboard — Frontend Logic
// Vanilla JS, no build step. Communicates with FastAPI backend via /api/ endpoints.

// ─────────────────────────────────────────
// State
// ─────────────────────────────────────────
let currentPage = 'dashboard';
let allEvents = [];       // current date's events (for filtering)
let refreshInterval = null;

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
    // Hide all pages, show selected
    document.querySelectorAll('.page').forEach(el => el.classList.add('hidden'));
    const target = document.getElementById(`page-${page}`);
    if (target) {
        target.classList.remove('hidden');
        target.classList.add('fade-in');
    }
    // Update nav highlight
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.toggle('bg-guardian-hover', btn.dataset.nav === page);
        btn.classList.toggle('text-white', btn.dataset.nav === page);
    });
    // Update page title
    const titles = { dashboard: 'Dashboard', cameras: 'Cameras', events: 'Events', alerts: 'Alerts', settings: 'Settings' };
    document.getElementById('page-title').textContent = titles[page] || 'Dashboard';
    // Load page data
    refreshCurrentPage();
}

function refreshCurrentPage() {
    const loaders = {
        dashboard: loadDashboard,
        cameras: loadCameras,
        events: loadEventDates,
        alerts: loadAlerts,
        settings: loadSettings,
    };
    const loader = loaders[currentPage];
    if (loader) loader();
}

// ─────────────────────────────────────────
// Dashboard Page
// ─────────────────────────────────────────
async function loadDashboard() {
    try {
        const [status, cameras, detections] = await Promise.all([
            api.get('/api/status'),
            api.get('/api/cameras'),
            api.get('/api/detections/recent?limit=20'),
        ]);
        renderStatus(status);
        renderDashboardFeeds(cameras);
        renderDashboardDetections(detections);
    } catch (err) {
        console.error('Dashboard load error:', err);
    }
}

function renderStatus(s) {
    const el = (id) => document.getElementById(id);
    if (s.online) {
        el('stat-service').textContent = 'Online';
        el('stat-service').className = 'text-xl font-bold text-emerald-400';
        el('stat-uptime').textContent = formatUptime(s.uptime_seconds);
        el('sidebar-status').innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 pulse-dot"></span><span class="hidden lg:block">Online</span>';
    } else {
        el('stat-service').textContent = 'Offline';
        el('stat-service').className = 'text-xl font-bold text-red-400';
        el('sidebar-status').innerHTML = '<span class="w-2 h-2 rounded-full bg-red-400"></span><span class="hidden lg:block">Offline</span>';
    }
    el('stat-cameras').textContent = `${s.cameras_online} / ${s.cameras_total}`;
    el('stat-cameras-detail').textContent = `${s.cameras_online} online, ${s.cameras_total} configured`;
    el('stat-detections').textContent = s.detections_today;
    el('stat-frames').textContent = `${s.frames_processed.toLocaleString()} frames processed`;
    el('stat-alerts').textContent = s.alerts_today;
    el('stat-alerts-total').textContent = `${s.alerts_sent} total alerts sent`;
    el('header-status').textContent = s.online ? `Up ${formatUptime(s.uptime_seconds)}` : 'Service offline';
}

function renderDashboardFeeds(cameras) {
    const container = document.getElementById('dashboard-feeds');
    if (!cameras.length) {
        container.innerHTML = '<div class="bg-guardian-card border border-guardian-border rounded-xl p-6 text-center text-slate-500">No cameras configured</div>';
        return;
    }
    container.innerHTML = cameras.map(cam => `
        <div class="bg-guardian-card border border-guardian-border rounded-xl overflow-hidden">
            <div class="relative">
                ${cam.capturing
                    ? `<img src="/api/cameras/${cam.name}/stream" class="camera-feed" alt="${cam.name}" onerror="this.src=''; this.alt='Feed unavailable'">`
                    : '<div class="camera-feed flex items-center justify-center text-slate-500 text-sm">Not capturing</div>'
                }
                <div class="absolute top-2 right-2 flex items-center gap-1.5 bg-black/60 rounded-full px-2.5 py-1">
                    <span class="w-2 h-2 rounded-full ${cam.online ? 'bg-emerald-400' : 'bg-red-400'}"></span>
                    <span class="text-xs">${cam.online ? 'Online' : 'Offline'}</span>
                </div>
            </div>
            <div class="p-3 flex items-center justify-between">
                <div>
                    <div class="font-medium text-sm">${cam.name}</div>
                    <div class="text-xs text-slate-500">${cam.ip} &middot; ${cam.type}</div>
                </div>
                <div class="flex gap-2">
                    ${cam.capturing
                        ? `<button onclick="stopCapture('${cam.name}')" class="px-2 py-1 bg-red-600/20 text-red-400 rounded text-xs hover:bg-red-600/40 transition-colors">Stop</button>`
                        : `<button onclick="startCapture('${cam.name}')" class="px-2 py-1 bg-emerald-600/20 text-emerald-400 rounded text-xs hover:bg-emerald-600/40 transition-colors">Start</button>`
                    }
                </div>
            </div>
        </div>
    `).join('');
}

function renderDashboardDetections(detections) {
    const tbody = document.getElementById('dashboard-detections');
    if (!detections.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-8 text-center text-slate-500">No detections yet</td></tr>';
        return;
    }
    tbody.innerHTML = detections.map(d => `
        <tr class="border-t border-guardian-border hover:bg-slate-800/30">
            <td class="px-4 py-2 text-slate-300">${formatTime(d.timestamp)}</td>
            <td class="px-4 py-2">${d.camera}</td>
            <td class="px-4 py-2">
                <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium
                    ${d.is_predator ? 'bg-red-600/20 text-red-400' : 'bg-slate-600/30 text-slate-300'}">
                    ${d.class}
                </span>
            </td>
            <td class="px-4 py-2">${(d.confidence * 100).toFixed(0)}%</td>
            <td class="px-4 py-2">${d.is_predator
                ? '<span class="text-red-400 text-xs font-medium">PREDATOR</span>'
                : '<span class="text-slate-500 text-xs">no</span>'
            }</td>
        </tr>
    `).join('');
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
    if (!cameras.length) {
        container.innerHTML = '<div class="bg-guardian-card border border-guardian-border rounded-xl p-6 text-center text-slate-500">No cameras found — click Rescan Network</div>';
        return;
    }
    container.innerHTML = cameras.map(cam => `
        <div class="bg-guardian-card border border-guardian-border rounded-xl overflow-hidden">
            <div class="relative">
                ${cam.capturing
                    ? `<img src="/api/cameras/${cam.name}/stream" class="camera-feed" style="min-height:320px" alt="${cam.name}">`
                    : '<div class="camera-feed flex items-center justify-center text-slate-500" style="min-height:320px">Capture stopped</div>'
                }
            </div>
            <div class="p-4 space-y-3">
                <div class="flex items-center justify-between">
                    <div>
                        <h3 class="font-semibold">${cam.name}</h3>
                        <div class="text-xs text-slate-500">${cam.ip} &middot; Port ${cam.type === 'ptz' ? 'PTZ' : 'Fixed'}</div>
                    </div>
                    <span class="flex items-center gap-1.5 text-xs ${cam.online ? 'text-emerald-400' : 'text-red-400'}">
                        <span class="w-2 h-2 rounded-full ${cam.online ? 'bg-emerald-400' : 'bg-red-400'}"></span>
                        ${cam.online ? 'Online' : 'Offline'}
                    </span>
                </div>
                <div class="flex items-center gap-2 text-xs text-slate-400">
                    <span>RTSP: ${cam.rtsp_url ? 'Resolved' : 'N/A'}</span>
                    <span>&middot;</span>
                    <span>Motion Events: ${cam.supports_motion ? 'Yes' : 'No'}</span>
                </div>
                <div class="flex gap-2">
                    ${cam.capturing
                        ? `<button onclick="stopCapture('${cam.name}')" class="flex-1 px-3 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm text-center transition-colors">Stop Capture</button>`
                        : `<button onclick="startCapture('${cam.name}')" class="flex-1 px-3 py-2 bg-emerald-600 hover:bg-emerald-700 rounded-lg text-sm text-center transition-colors">Start Capture</button>`
                    }
                </div>
            </div>
        </div>
    `).join('');
}

async function rescanCameras() {
    try {
        const result = await api.post('/api/cameras/rescan');
        showToast(`Scan complete: ${result.cameras_online} online` +
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
        const currentVal = select.value;
        select.innerHTML = '<option value="">Select date...</option>' +
            dates.map(d => `<option value="${d.date}">${d.date} (${d.count} events)</option>`).join('');
        if (currentVal) {
            select.value = currentVal;
            loadEventsForDate(currentVal);
        }
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
    const classFilter = document.getElementById('event-class-filter').value;
    let filtered = allEvents;
    if (classFilter) {
        filtered = allEvents.filter(e => e.class === classFilter);
    }
    renderEventsTable(filtered);
}

function renderEventsTable(events) {
    const tbody = document.getElementById('events-table');
    if (!events.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-8 text-center text-slate-500">No events match filters</td></tr>';
        return;
    }
    tbody.innerHTML = events.map(e => {
        const snapshotPath = e.snapshot;
        let snapshotCell = '<span class="text-slate-500">—</span>';
        if (snapshotPath) {
            // Extract date and filename from path like "events/2026-04-02/123456_bird.jpg"
            const parts = snapshotPath.replace(/\\/g, '/').split('/');
            const filename = parts[parts.length - 1];
            const dateDir = parts[parts.length - 2];
            snapshotCell = `<button onclick="openSnapshot('/api/snapshots/${dateDir}/${filename}')" class="text-blue-400 hover:text-blue-300 text-xs underline">View</button>`;
        }
        return `
            <tr class="border-t border-guardian-border hover:bg-slate-800/30">
                <td class="px-4 py-2 text-slate-300">${formatTime(e.timestamp)}</td>
                <td class="px-4 py-2">${e.camera}</td>
                <td class="px-4 py-2">
                    <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium
                        ${e.is_predator ? 'bg-red-600/20 text-red-400' : 'bg-slate-600/30 text-slate-300'}">
                        ${e.class}
                    </span>
                </td>
                <td class="px-4 py-2">${(e.confidence * 100).toFixed(0)}%</td>
                <td class="px-4 py-2">${snapshotCell}</td>
            </tr>
        `;
    }).join('');
}

function openSnapshot(url) {
    document.getElementById('snapshot-modal-img').src = url;
    document.getElementById('snapshot-modal').classList.remove('hidden');
}

function closeSnapshot() {
    document.getElementById('snapshot-modal').classList.add('hidden');
    document.getElementById('snapshot-modal-img').src = '';
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
    if (!alerts.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="px-4 py-8 text-center text-slate-500">No alerts sent yet</td></tr>';
        return;
    }
    tbody.innerHTML = alerts.map(a => `
        <tr class="border-t border-guardian-border hover:bg-slate-800/30">
            <td class="px-4 py-2 text-slate-300">${formatTime(a.timestamp)}</td>
            <td class="px-4 py-2">${a.camera || '—'}</td>
            <td class="px-4 py-2">${(a.classes || []).map(c =>
                `<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-red-600/20 text-red-400 mr-1">${c}</span>`
            ).join('')}</td>
            <td class="px-4 py-2">${a.sent
                ? '<span class="text-emerald-400 text-xs">Sent</span>'
                : '<span class="text-red-400 text-xs">Failed</span>'
            }</td>
        </tr>
    `).join('');
}

async function sendTestAlert() {
    const btn = document.getElementById('test-alert-btn');
    const result = document.getElementById('test-alert-result');
    btn.disabled = true;
    btn.textContent = 'Sending...';
    try {
        const res = await api.post('/api/alerts/test');
        result.className = `rounded-lg p-3 text-sm ${res.ok ? 'bg-emerald-900/50 text-emerald-300' : 'bg-red-900/50 text-red-300'}`;
        result.textContent = res.message;
        result.classList.remove('hidden');
    } catch (err) {
        result.className = 'rounded-lg p-3 text-sm bg-red-900/50 text-red-300';
        result.textContent = 'Request failed: ' + err.message;
        result.classList.remove('hidden');
    }
    btn.disabled = false;
    btn.innerHTML = '<i data-lucide="send" class="w-4 h-4"></i> Send Test Alert';
    lucide.createIcons();
    setTimeout(() => result.classList.add('hidden'), 5000);
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

    document.getElementById('cfg-confidence').value = det.confidence_threshold ?? 0.45;
    document.getElementById('cfg-bird-min').value = det.bird_min_bbox_width_pct ?? 8;
    document.getElementById('cfg-dwell').value = det.min_dwell_frames ?? 3;
    document.getElementById('cfg-interval').value = det.frame_interval_seconds ?? 1.0;
    document.getElementById('cfg-predators').value = (det.predator_classes || []).join(',');
    document.getElementById('cfg-ignore').value = (det.ignore_classes || []).join(',');
    document.getElementById('cfg-zone').value = JSON.stringify(det.no_alert_zone || []);

    // Per-class thresholds
    const thresholds = det.class_confidence_thresholds || {};
    const container = document.getElementById('class-thresholds');
    const classes = [...new Set([...(det.predator_classes || []), ...Object.keys(thresholds)])];
    container.innerHTML = classes.map(cls => `
        <div>
            <label class="block text-xs text-slate-500 mb-0.5">${cls}</label>
            <input type="number" id="cfg-thresh-${cls}" min="0" max="1" step="0.05"
                value="${thresholds[cls] ?? det.confidence_threshold ?? 0.45}" class="text-sm">
        </div>
    `).join('');

    // Alert settings
    document.getElementById('cfg-webhook').value = alerts.discord_webhook_url || '';
    document.getElementById('cfg-cooldown').value = det.alert_cooldown_seconds ?? 300;
    document.getElementById('cfg-snapshot').checked = alerts.include_snapshot !== false;
}

async function saveDetectionConfig() {
    const predators = document.getElementById('cfg-predators').value.split(',').map(s => s.trim()).filter(Boolean);
    const ignore = document.getElementById('cfg-ignore').value.split(',').map(s => s.trim()).filter(Boolean);

    // Build per-class thresholds from form
    const classThresholds = {};
    predators.forEach(cls => {
        const input = document.getElementById(`cfg-thresh-${cls}`);
        if (input) classThresholds[cls] = parseFloat(input.value);
    });

    let zone = [];
    try {
        const zoneStr = document.getElementById('cfg-zone').value.trim();
        if (zoneStr) zone = JSON.parse(zoneStr);
    } catch (e) {
        showToast('Invalid JSON for no-alert zone', 'error');
        return;
    }

    const body = {
        confidence_threshold: parseFloat(document.getElementById('cfg-confidence').value),
        bird_min_bbox_width_pct: parseFloat(document.getElementById('cfg-bird-min').value),
        min_dwell_frames: parseInt(document.getElementById('cfg-dwell').value),
        frame_interval_seconds: parseFloat(document.getElementById('cfg-interval').value),
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
    const body = {
        alert_cooldown_seconds: parseInt(document.getElementById('cfg-cooldown').value),
        include_snapshot: document.getElementById('cfg-snapshot').checked,
    };
    // Only send webhook URL if the user typed a full URL (not the redacted one)
    const webhook = document.getElementById('cfg-webhook').value;
    if (webhook && !webhook.startsWith('...')) {
        body.discord_webhook_url = webhook;
    }

    try {
        const res = await api.post('/api/config/alerts', body);
        showSaveResult('alert-save-result', res.ok, `Updated: ${res.updated.join(', ')}`);
    } catch (err) {
        showSaveResult('alert-save-result', false, err.message);
    }
}

function showSaveResult(elementId, success, message) {
    const el = document.getElementById(elementId);
    el.className = `text-sm mt-2 ${success ? 'text-emerald-400' : 'text-red-400'}`;
    el.textContent = message;
    el.classList.remove('hidden');
    setTimeout(() => el.classList.add('hidden'), 4000);
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
    const bg = type === 'error' ? 'bg-red-600' : 'bg-emerald-600';
    toast.className = `fixed bottom-4 right-4 ${bg} text-white px-4 py-2 rounded-lg shadow-lg text-sm z-50 fade-in`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; }, 3000);
    setTimeout(() => toast.remove(), 3500);
}

// ─────────────────────────────────────────
// Init
// ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    navigate('dashboard');
    // Auto-refresh every 5 seconds
    refreshInterval = setInterval(() => {
        if (currentPage === 'dashboard') loadDashboard();
    }, 5000);
});

// Keyboard shortcut: Escape closes snapshot modal
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSnapshot();
});

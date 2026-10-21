/**
 * Real backend adapter — connects to the FastAPI server on the Raspberry Pi.
 *
 * Pi endpoints used (all added to main.py):
 *   GET  /api/v1/live               — live crusher / feeder / VFD snapshot
 *   GET  /api/v1/vfd/trend?minutes= — [{t, hz}] trend array
 *   GET  /api/v1/alerts?limit=      — alert history
 *   GET  /api/v1/analytics?from=&to=&label= — period analytics
 *   WS   /ws/camera                 — raw state pushed every new frame
 *   POST /api/vfd/set/{rpm}         — manual speed override
 *   POST /api/vfd/stop              — stop VFD
 *   POST /api/vfd/reset-fault       — reset fault
 *
 * The Pi WebSocket sends the raw crusher_camera state dict (no {type,data} wrapper).
 * This adapter transforms it into the live-snapshot shape the views expect.
 */

import { APP } from '../config.js';

let ws = null;
let handlers = {};
let retry = 0;
let retryTimer = null;

// ── RPM→Hz helper (Pi stores RPM in the "vfd_hz" DB column) ──────────────
function rpmToHz(rpm) {
  return parseFloat((rpm * 50 / 1500).toFixed(1));
}

// ── Transform the Pi's raw WS state into the live-snapshot shape ──────────
const MACHINE_TO_CRUSHER = {
  NORMAL: 'RUNNING', 'STONE STUCK': 'WARNING',
  'NO RAW MATERIAL': 'RUNNING', STOPPED: 'STOPPED', FAULT: 'FAULT',
};
const JAW_TO_AI = {
  'jaw filled': 'FULL', 'jaw partially filled': 'PARTIAL', 'jaw empty': 'EMPTY',
};

function piStateToSnap(d) {
  const machineStatus = d.machine_status || 'STOPPED';
  const jawLabel      = d.jaw_label      || 'unknown';
  const targetRpm     = d.target_vfd_rpm || 0;
  const targetHz      = rpmToHz(targetRpm);
  const aiState       = JAW_TO_AI[jawLabel] || 'EMPTY';

  let driveStatus = 'Stopped';
  if (machineStatus === 'FAULT')                                                  driveStatus = 'Fault';
  else if (machineStatus === 'NORMAL' || machineStatus === 'STONE STUCK' ||
           machineStatus === 'NO RAW MATERIAL')                                   driveStatus = 'Running';

  return {
    timestamp : new Date().toISOString(),
    connection: { source: 'api', online: d.cam_status === 'live' },
    crusher   : {
      status       : MACHINE_TO_CRUSHER[machineStatus] || 'STOPPED',
      motorCurrentA: null,
    },
    feeder    : {
      aiState    : aiState,
      confidence : Math.round((d.jaw_conf || 0) * 100),
      detectedAt : new Date().toISOString(),
      detecting  : d.cam_status === 'live',
    },
    vfd       : {
      requestedMode: 'AUTO',
      mode         : 'AUTO',
      manualHz     : targetHz,
      commandHz    : targetHz,
      outputHz     : targetHz,
      autoHz       : targetHz,
      manualAllowed: aiState === 'EMPTY',
      atMax        : targetRpm >= 1290,
      interlock    : machineStatus === 'FAULT',
      driveStatus  : driveStatus,
    },
  };
}

// ── HTTP helper ───────────────────────────────────────────────────────────
async function request(path, opts = {}) {
  const ctrl    = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), 8000);
  try {
    const res = await fetch(`${APP.apiBase}${path}`, {
      ...opts,
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      signal : ctrl.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timeout);
  }
}

// ── WebSocket ─────────────────────────────────────────────────────────────
function connect() {
  const wsBase = APP.apiBase
    ? APP.apiBase.replace(/^http/, 'ws')
    : (location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host;
  const url = wsBase + APP.wsPath;
  ws = new WebSocket(url);

  ws.onopen = () => {
    retry = 0;
    handlers.onConnection?.({ online: true });
  };

  ws.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch { return; }

    // Pi sends raw state dict — strip the base64 frame before passing to store
    const { frame: _f, ...state } = d;
    const snap = piStateToSnap(state);
    handlers.onLive?.(snap, { trendSample: true });
  };

  ws.onclose = () => {
    handlers.onConnection?.({ online: false });
    retryTimer = setTimeout(connect, Math.min(30000, 1000 * 2 ** retry++));
  };

  ws.onerror = () => {
    handlers.onConnection?.({ online: false });
  };
}

// ── Public service ────────────────────────────────────────────────────────
export const apiService = {
  name: 'api',

  async start(h) {
    handlers = h;

    // Initial live snapshot
    try {
      const live = await request('/api/v1/live');
      handlers.onLive?.(live, { trendSample: false });
    } catch (e) {
      console.warn('apiService: /api/v1/live failed:', e.message);
      handlers.onConnection?.({ online: false });
    }

    // VFD trend seed (last 60 minutes)
    try {
      const trend = await request('/api/v1/vfd/trend?minutes=60');
      handlers.onTrendSeed?.(Array.isArray(trend) ? trend : []);
    } catch (e) {
      console.warn('apiService: /api/v1/vfd/trend failed:', e.message);
    }

    connect();
  },

  stop() {
    clearTimeout(retryTimer);
    if (ws) { ws.onclose = null; ws.close(); ws = null; }
  },

  getAlerts() {
    return request('/api/v1/alerts?limit=100');
  },

  getAnalytics({ from, to, label }) {
    return request(
      `/api/v1/analytics?from=${from}&to=${to}&label=${encodeURIComponent(label || '')}`
    );
  },

  async setControl(body) {
    try {
      if (body.mode === 'MANUAL' && body.manualHz != null) {
        const rpm = Math.round(body.manualHz * 1500 / 50);  // Hz → RPM
        await request(`/api/vfd/set/${rpm}`, { method: 'POST' });
      } else if (body.mode === 'AUTO') {
        // Pi auto-mode: no explicit command needed — the background task
        // resumes AI control automatically. Optionally stop the override.
        // No-op: AI will re-assert on the next camera frame.
      }
      return { ok: true };
    } catch (e) {
      return { ok: false, error: `Command not accepted: ${e.message}` };
    }
  },

  refresh() {},

  demo: null,
};

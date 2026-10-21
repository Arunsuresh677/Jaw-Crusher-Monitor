/** Dashboard — immediate overview of crusher, feeder AI state and VFD. */

import { settings } from '../core/settings.js';
import { DataService } from '../services/dataService.js';
import { icon } from '../ui/icons.js';
import { $, toneChip } from '../ui/components.js';
import { feederScene } from '../ui/scene.js';
import {
  setHTML, vfdCardHTML, crusherCardHTML, feederCardHTML, alertRowHTML, feederTone,
} from '../ui/widgets.js';
import { CRUSHER_STATES, FEEDER_STATES } from '../config.js';
import { fmtDuration, fmtDate, fmtTimeSec, toISODate, relTime } from '../core/format.js';

let todayTimer = null;
let clockTimer = null;

async function loadToday(root) {
  const today = toISODate(new Date());
  const a = await DataService.getAnalytics({ from: today, to: today, label: 'Today' });
  const el = $('[data-today]', root);
  if (!el) return;
  setHTML(el, `
    <div class="grid-4">
      <div class="metric"><div class="eyebrow">${icon('clock', { size: 14 })}Runtime</div><div class="metric-val">${fmtDuration(a.runtimeSec)}</div><div class="metric-sub">Crusher running</div></div>
      <div class="metric"><div class="eyebrow">${icon('power', { size: 14 })}Downtime</div><div class="metric-val">${fmtDuration(a.downtimeSec)}</div><div class="metric-sub">${a.stopEvents.length} stop${a.stopEvents.length === 1 ? '' : 's'}</div></div>
      <div class="metric"><div class="eyebrow">${icon('truck', { size: 14 })}Production</div><div class="metric-val">${Math.round(a.outputT)}<small>t</small></div><div class="metric-sub">${a.avgTph.toFixed(0)} t/h average</div></div>
      <div class="metric"><div class="eyebrow">${icon('gauge', { size: 14 })}OEE</div><div class="metric-val">${a.oee.overall.toFixed(1)}<small>%</small></div><div class="metric-sub">A ${a.oee.availability.toFixed(0)} · P ${a.oee.performance.toFixed(0)} · Q ${a.oee.quality.toFixed(0)}</div></div>
    </div>`);
}

export default {
  id: 'dashboard',
  head: () => ({ brand: true, title: 'Dashboard' }),

  render(root) {
    root.innerHTML = `
      <div class="status-strip">
        <span data-sys></span>
        ${DataService.source === 'mock' ? toneChip('gray', 'Demo data', { dot: false }) : ''}
        <span class="time"><span data-date></span> · <span data-clock class="mono"></span></span>
      </div>
      <div class="dash-grid stack-y">
        <div class="grid-2 keep d-6 t-full l-half">
          <a class="card status-card" data-crusher href="#/machine" style="color:inherit;text-decoration:none"></a>
          <a class="card status-card" data-feeder href="#/camera" style="color:inherit;text-decoration:none"></a>
        </div>
        <section class="card d-6 t-full l-half" aria-label="VFD">
          <div class="card-head"><div class="card-title">${icon('pulse', { size: 16 })}Feeder VFD</div><a class="card-link" href="#/machine">Control ${icon('chevron', { size: 16 })}</a></div>
          <div data-vfd></div>
        </section>
        <section class="card d-7 t-full l-full" aria-label="Today">
          <div class="card-head"><div class="card-title">${icon('calendar', { size: 16 })}Today</div><a class="card-link" href="#/analytics">Reports ${icon('chevron', { size: 16 })}</a></div>
          <div data-today><div class="subtle">Loading…</div></div>
        </section>
        <section class="card d-5 l-full" aria-label="AI feeder detection">
          <div class="card-head"><div class="card-title">${icon('camera', { size: 16 })}AI Feeder Detection</div><a class="card-link" href="#/camera">Live ${icon('chevron', { size: 16 })}</a></div>
          <div class="grid-2 keep" style="align-items:center">
            <div class="feed-thumb" data-thumb></div>
            <div data-ai></div>
          </div>
        </section>
        <section class="card span-2" aria-label="Recent alerts">
          <div class="card-head"><div class="card-title">${icon('bell', { size: 16 })}Recent Alerts</div><a class="card-link" href="#/alerts">View all ${icon('chevron', { size: 16 })}</a></div>
          <div data-alerts></div>
        </section>
      </div>`;

    const tick = () => {
      setHTML($('[data-clock]', root), fmtTimeSec(new Date()));
      setHTML($('[data-date]', root), fmtDate(new Date()));
    };
    tick();
    clockTimer = setInterval(tick, 1000);
    loadToday(root);
    todayTimer = setInterval(() => loadToday(root), 60000);
  },

  update(st, path) {
    const root = document.getElementById('view');
    if (!root) return;
    const s = settings.all;
    if (path === 'live' || path === '*' || path === 'trend') {
      const c = CRUSHER_STATES[st.crusher.status];
      const crusherCard = $('[data-crusher]', root);
      crusherCard.className = `card status-card tone-${c.tone}`;
      setHTML(crusherCard, crusherCardHTML(st));
      const feederCard = $('[data-feeder]', root);
      feederCard.className = `card status-card tone-${feederTone(st.feeder.aiState)}`;
      setHTML(feederCard, feederCardHTML(st));
      setHTML($('[data-vfd]', root), vfdCardHTML(st, s));

      const sysTone = { RUNNING: 'green', WARNING: 'amber', FAULT: 'red', STOPPED: 'gray' }[st.crusher.status];
      const sysLabel = { RUNNING: 'System running', WARNING: 'System warning', FAULT: 'System fault', STOPPED: 'System stopped' }[st.crusher.status];
      setHTML($('[data-sys]', root), `<span class="chip chip-${sysTone} chip-live"><i class="dot"></i>${sysLabel}</span>`);

      setHTML($('[data-thumb]', root), `${feederScene(st.feeder.aiState, { box: true, cam: 1 })}
        <div class="feed-osd"><span class="chip"><i class="dot" style="color:#ef4444"></i>Camera 01</span></div>`);
      const f = FEEDER_STATES[st.feeder.aiState];
      setHTML($('[data-ai]', root), `
        <div class="eyebrow">AI decision</div>
        <div class="big-value t-${f.tone}" style="font-size:22px;margin:4px 0 10px">${f.long}</div>
        <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:5px"><span class="muted">Confidence</span><strong>${st.feeder.confidence}%</strong></div>
        <div class="meter"><span style="width:${st.feeder.confidence}%"></span></div>
        <div class="subtle" style="font-size:12px;margin-top:8px">Detected ${relTime(st.feeder.detectedAt)}</div>`);
    }
    if (path === 'alerts' || path === '*') {
      const list = st.alerts.slice(0, 4);
      setHTML($('[data-alerts]', root), list.length ? list.map((a) => alertRowHTML(a, { compact: true })).join('') : '<p class="subtle">No alerts.</p>');
    }
  },

  destroy() {
    clearInterval(todayTimer);
    clearInterval(clockTimer);
  },
};

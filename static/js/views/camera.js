/**
 * Live Camera. Prototype shows a placeholder scene; the backend will relay
 * the Hikvision RTSP stream as WebRTC/HLS/MJPEG (browsers cannot play RTSP).
 */

import { mockCameras } from '../services/mockData.js';
import { icon } from '../ui/icons.js';
import { $, toast } from '../ui/components.js';
import { feederScene } from '../ui/scene.js';
import { setHTML } from '../ui/widgets.js';
import { FEEDER_STATES } from '../config.js';
import { fmtDate, fmtTimeSec } from '../core/format.js';
import { APP } from '../config.js';

let camId = 'cam1';
let clock = null;

export default {
  id: 'camera',
  head: () => ({ title: 'Live Camera', subtitle: 'Crusher feeder monitoring' }),

  render(root) {
    root.innerHTML = `
      <div class="two-col">
        <div class="stack-y">
          <div class="tabs" role="tablist">${mockCameras.map((c) => `<button class="tab" role="tab" data-cam="${c.id}">${c.name}</button>`).join('')}</div>
          <div class="cam-view" data-view>
            <div data-scene style="width:100%;height:100%"></div>
            <div class="cam-osd-tl"><span class="osd-pill"><i class="dot"></i>LIVE · DEMO</span></div>
            <div class="cam-osd-tr" data-osd-time></div>
            <div class="cam-osd-bl"><strong data-cam-name></strong><span data-cam-loc></span></div>
            <div class="cam-osd-br" data-osd-ai></div>
          </div>
          <div class="cam-actions">
            <button class="cam-action" data-act="snapshot">${icon('snapshot', { size: 22 })}Snapshot</button>
            <button class="cam-action" data-act="record">${icon('record', { size: 22 })}Record</button>
            <button class="cam-action" data-act="fullscreen">${icon('fullscreen', { size: 22 })}Fullscreen</button>
          </div>
        </div>
        <div class="stack-y">
          <section class="card">
            <div class="card-head"><div class="card-title">${icon('camera', { size: 16 })}Camera</div></div>
            <div data-info></div>
          </section>
          <section class="card">
            <div class="banner soft-blue">${icon('info', { size: 18 })}<div><strong>Demo video area</strong>
              The Hikvision RTSP stream will be connected through the Raspberry Pi backend and shown here. The image above is a placeholder that follows the demo AI state.</div></div>
          </section>
        </div>
      </div>`;

    root.querySelectorAll('[data-cam]').forEach((b) => b.addEventListener('click', () => { camId = b.dataset.cam; this.update(); }));
    root.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', () => {
      const act = b.dataset.act;
      if (act === 'fullscreen') {
        const v = $('[data-view]', root);
        if (document.fullscreenElement) document.exitFullscreen();
        else v.requestFullscreen?.().catch(() => toast('Fullscreen not available on this device', 'warning'));
        return;
      }
      toast(`${act === 'snapshot' ? 'Snapshot' : 'Recording'} will be available when the camera stream is connected`, 'info');
    }));

    const tick = () => setHTML($('[data-osd-time]', root), `${fmtDate(new Date())} ${fmtTimeSec(new Date())}`);
    tick();
    clock = setInterval(tick, 1000);
  },

  update(st = this._st, path) {
    if (st) this._st = st;
    st = this._st;
    const root = document.getElementById('view');
    if (!root || !st || !$('[data-scene]', root)) return;
    if (path && path !== 'live' && path !== '*') return;
    const cam = mockCameras.find((c) => c.id === camId) || mockCameras[0];
    const idx = mockCameras.indexOf(cam) + 1;
    root.querySelectorAll('[data-cam]').forEach((b) => {
      b.classList.toggle('on', b.dataset.cam === cam.id);
      b.setAttribute('aria-selected', b.dataset.cam === cam.id);
    });
    const ai = st.feeder.aiState;
    const f = FEEDER_STATES[ai];
    setHTML($('[data-scene]', root), feederScene(cam.ai ? ai : 'PARTIAL', { box: cam.ai, cam: idx }));
    setHTML($('[data-cam-name]', root), `${cam.location} — ${cam.name}`);
    setHTML($('[data-cam-loc]', root), cam.ai ? 'AI feeder detection active' : 'Monitoring only');
    setHTML($('[data-osd-ai]', root), cam.ai ? `<span class="osd-pill">AI: ${f.label.toUpperCase()} · ${st.feeder.confidence}%</span>` : '');
    setHTML($('[data-info]', root), `
      <div class="kv"><span>Camera</span><strong>${cam.name}</strong></div>
      <div class="kv"><span>Location</span><strong>${cam.location}</strong></div>
      <div class="kv"><span>AI detection</span><strong class="${cam.ai ? 't-green' : 'subtle'}">${cam.ai ? 'Enabled' : 'Not used'}</strong></div>
      ${cam.ai ? `<div class="kv"><span>Current AI state</span><strong class="t-${f.tone}">${f.label}</strong></div>
      <div class="kv"><span>Confidence</span><strong>${st.feeder.confidence}%</strong></div>` : ''}
      <div class="kv"><span>Stream</span><strong>${APP.dataSource === 'mock' ? 'Placeholder (demo)' : 'Backend relay'}</strong></div>`);
  },

  destroy() { clearInterval(clock); },
};

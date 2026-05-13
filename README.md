<div align="center">

<img src="https://img.shields.io/badge/STATUS-LIVE%20IN%20PRODUCTION-brightgreen?style=for-the-badge&logo=raspberry-pi" />

# Jaw Crusher Monitor
### Edge AI Pipeline for Real-Time Industrial Crusher State Classification

*Deployed at Kannan Blue Metals, Chennimalai, Erode, Tamil Nadu, India*

---

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![YOLOv8](https://img.shields.io/badge/YOLOv8s--cls-Ultralytics-FF6B35?style=flat-square)](https://ultralytics.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Flutter](https://img.shields.io/badge/Flutter-Mobile-02569B?style=flat-square&logo=flutter)](https://flutter.dev)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry_Pi-5-C51A4A?style=flat-square&logo=raspberry-pi)](https://raspberrypi.org)
[![WebSocket](https://img.shields.io/badge/WebSocket-Live_Feed-4CAF50?style=flat-square)](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

</div>

---

## Overview

A production-deployed edge AI system that classifies jaw crusher states in real time using a fine-tuned YOLOv8s-cls model running on Raspberry Pi 5. The system streams live telemetry via WebSocket to a web dashboard and Flutter mobile app, enabling remote monitoring and automated VFD frequency recommendations.

---

## Problem

At quarry sites, jaw crusher feed levels are monitored manually — creating three recurring operational issues:

- **Motor overload** when jaw is overfilled and material jams
- **Idle energy consumption** when jaw runs empty between feed cycles
- **Zero remote visibility** — supervisors have no real-time awareness of crusher state

---

## Solution

A camera-based AI pipeline running entirely on-device (Raspberry Pi 5) that:

1. Captures RTSP stream from an IP camera mounted above the crusher jaw
2. Runs YOLOv8s-cls inference at 10 FPS to classify jaw state
3. Applies a state machine with configurable time thresholds to detect anomalies
4. Pushes live state, confidence, and OEE metrics via WebSocket to web and mobile clients

---

## Impact

| Metric | Before | After |
|---|---|---|
| Remote visibility | None | Full real-time dashboard |
| Stone stuck detection | Manual observation | Automated alert at 15s threshold |
| No-feed detection | Manual observation | Automated alert at 30s threshold |
| Supervisor response time | Minutes | Seconds |

---

## Classification Model

| Detail | Value |
|---|---|
| Architecture | YOLOv8s-cls (Ultralytics) |
| Task | Image Classification |
| Classes | `jaw filled` / `jaw partially filled` / `jaw empty` |
| Input Resolution | 224 × 224 |
| Confidence Threshold | 0.50 |
| Inference Speed | ~100ms per frame on Pi 5 |
| Training Data | ~3,000 labeled frames from live crusher feed |

### VFD Frequency Mapping

| Jaw State | VFD Output | Rationale |
|---|---|---|
| `jaw filled` | **20 Hz** | Jaw loaded — protect motor, reduce feeder speed |
| `jaw partially filled` | **37 Hz** | Normal crushing — maintain throughput |
| `jaw empty` | **50 Hz** | No material — increase feeder speed |

---

## System Architecture

```
IP Camera (Hikvision RTSP :1060)
              │
              ▼
    ┌─────────────────────┐
    │    Raspberry Pi 5   │
    │                     │
    │  camera.py          │  ← RTSP capture + frame buffering
    │      │              │
    │      ▼              │
    │  YOLOv8s-cls        │  ← 100ms inference cycle
    │      │              │
    │      ▼              │
    │  crusher_logic.py   │  ← State machine + OEE + alerts
    │      │              │
    │      ▼              │
    │  main.py (FastAPI)  │  ← REST API + WebSocket server
    └──────┬──────────────┘
           │
     ┌─────┴──────┐
     ▼            ▼
Web Dashboard  Flutter App
(Browser)      (Android/iOS)
```

---

## Key Engineering Decisions

**1. No auto-retry on RTSP failure**
Hikvision cameras enforce a lockout after repeated failed login attempts. The system connects once on manual trigger only — subsequent retries require an explicit `POST /api/camera/restart` call. This prevents background retry loops from locking the camera in production.

**2. Single source of truth for configuration**
All tunable parameters (RTSP URL, VFD speeds, alert thresholds, FPS) live in `config.py`. No hardcoded values elsewhere.

**3. Headless by default**
`HEADLESS = True` in config disables `cv2.imshow` — the Pi runs without a monitor attached at the client site.

**4. WebSocket over polling**
Live state is pushed to all connected clients on every new frame rather than polled — reduces latency and server load.

---

## Project Structure

```
Jaw-Crusher-Monitor/
├── camera.py                    # RTSP capture, YOLO inference, frame encoding
├── crusher_logic.py             # State machine, OEE tracking, alert system
├── main.py                      # FastAPI server, WebSocket, REST endpoints
├── config.py                    # All settings — single source of truth
├── requirements.txt
├── static/
│   └── index.html               # Web dashboard (vanilla JS + WebSocket)
└── kannan_crusher_flutter/
    └── kannan_crusher_flutter/
        ├── lib/
        │   ├── screens/         # Dashboard, Camera, Alerts, Machine, Settings
        │   ├── services/        # WebSocket client, API service, app state
        │   ├── models/          # CrusherState data model
        │   └── widgets/         # OEE card, VFD trend chart, stat cards
        └── pubspec.yaml
```

---

## Setup

### Requirements
- Raspberry Pi 5 (2GB+ RAM)
- IP camera with RTSP support
- Python 3.11+
- Trained YOLOv8s-cls weights at `weights/best.pt`

### Install

```bash
git clone https://github.com/Arunsuresh677/Jaw-Crusher-Monitor.git
cd Jaw-Crusher-Monitor
pip install -r requirements.txt
```

### Configure

```python
# config.py
RTSP_URL       = "rtsp://admin:password@<camera-ip>:<port>/Streaming/Channels/101"
MODEL_PATH     = "/path/to/weights/best.pt"
CONF_THRESHOLD = 0.50

VFD_SPEEDS = {
    "jaw filled"           : 20,
    "jaw partially filled" : 37,
    "jaw empty"            : 50,
}

PARTIAL_STUCK_TIME = 15   # seconds → STONE STUCK alert
EMPTY_NO_FEED_TIME = 30   # seconds → NO RAW MATERIAL alert
```

### Run

```bash
python3 main.py
```

```bash
# Trigger camera connection manually (by design — see Engineering Decisions)
curl -X POST http://localhost:8000/api/camera/restart
```

Dashboard: `http://<pi-ip>:8000`

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web dashboard |
| `WS` | `/ws/camera` | Live frames + crusher state |
| `GET` | `/api/status` | Camera + machine status summary |
| `GET` | `/api/crusher` | Full crusher logic state |
| `GET` | `/api/oee` | OEE metrics + shift timers |
| `GET` | `/api/alerts` | Active alerts |
| `GET` | `/api/history` | Last 100 state transitions |
| `GET` | `/api/snapshot` | Latest annotated frame (base64 JPEG) |
| `POST` | `/api/camera/restart` | Reconnect RTSP stream |
| `GET` | `/api/camera/status` | Camera connection health |
| `POST` | `/api/shift/reset` | Reset shift counters |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Edge AI | YOLOv8s-cls (Ultralytics + PyTorch) |
| Hardware | Raspberry Pi 5 (4GB RAM) |
| Camera | Hikvision IP Camera (RTSP) |
| Backend | FastAPI + Uvicorn (async) |
| Realtime | WebSocket (native browser API) |
| Web UI | Vanilla JS + HTML (zero dependencies) |
| Mobile | Flutter / Dart (Android + iOS) |
| Vision | OpenCV (RTSP capture + frame encode) |
| Remote Access | RustDesk |

---

## Deployment

| Detail | Value |
|---|---|
| Site | Kannan Blue Metals, Chennimalai, Erode, Tamil Nadu |
| Device | Raspberry Pi 5 (4GB) |
| Camera | Hikvision IP Camera, RTSP port 1060 |
| Network | Local LAN (192.168.0.x) |
| Access | RustDesk remote desktop |
| Status | ✅ Running in production |

---

## Author

**Arun S** — AI/ML Engineer, Nithil Innovations

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Arun_S-0077B5?style=flat-square&logo=linkedin)](https://linkedin.com/in/arun-s-481b2b3a2)
[![GitHub](https://img.shields.io/badge/GitHub-Arunsuresh677-181717?style=flat-square&logo=github)](https://github.com/Arunsuresh677)

---

## License

MIT License — see [LICENSE](LICENSE) for details.

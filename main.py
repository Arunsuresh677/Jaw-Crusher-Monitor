"""
main.py — FastAPI WebSocket + REST API
Crusher Monitor — Kannan Blue Metals
"""

import json
import logging
import asyncio
import os
import time as _time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from camera import crusher_camera
from crusher_logic import crusher_logic
from config import HOST, PORT, LOG_DIR, STATIC_DIR, AUTH_USER, AUTH_PASS
from database import (
    init_db, close_db, save_jaw_state, save_alert, resolve_alert,
    save_vfd_log, save_oee_snapshot, save_shift_report,
    get_alerts_history, get_oee_history, get_vfd_history,
    get_shift_reports, get_state_summary_today, get_period_summary,
)
from reports import (
    build_shift_pdf, build_shift_csv,
    build_period_pdf, build_period_csv, build_period_excel,
)
from vfd_controller import vfd_controller

# ── Logging ──────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(f"{LOG_DIR}/crusher_yolo.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ── Background DB save tasks ──────────────────────────────────
_last_vfd_save   = 0.0
_last_oee_save   = 0.0
_last_alert_ids  = set()


async def _db_background_task():
    """Runs every second — saves VFD every 5s, OEE every 60s"""
    global _last_vfd_save, _last_oee_save, _last_alert_ids
    while True:
        try:
            now   = _time.time()
            state = crusher_logic.get_state()

            # ── Save jaw state every frame ──────────────────
            await save_jaw_state(
                jaw_label      = state.get("jaw_label", "unknown"),
                confidence     = state.get("jaw_conf", 0.0),
                machine_status = state.get("machine_status", "STOPPED"),
                target_vfd_hz  = state.get("target_vfd_hz", 0),
                partial_secs   = state.get("partial_secs", 0),
                empty_secs     = state.get("empty_secs", 0),
            )

            # ── Save VFD log every 5 seconds ────────────────
            if now - _last_vfd_save >= 5:
                await save_vfd_log(
                    vfd_hz         = state.get("target_vfd_hz", 0),
                    jaw_label      = state.get("jaw_label", "unknown"),
                    machine_status = state.get("machine_status", "STOPPED"),
                )
                _last_vfd_save = now

            # ── Save OEE snapshot every 60 seconds ──────────
            if now - _last_oee_save >= 60:
                await save_oee_snapshot(state)
                _last_oee_save = now

            # ── Save new alerts ─────────────────────────────
            active = state.get("active_alerts", [])
            current_ids = {a["id"] for a in active}

            for alert in active:
                if alert["id"] not in _last_alert_ids:
                    await save_alert(
                        alert_id = alert["id"],
                        level    = alert["level"],
                        message  = alert["message"],
                    )

            # Resolve alerts no longer active
            for aid in _last_alert_ids - current_ids:
                await resolve_alert(aid)

            _last_alert_ids = current_ids

        except Exception as e:
            log.error("DB background task error: %s", e)

        await asyncio.sleep(1)


async def _vfd_background_task():
    """
    Every 1 s: send target_vfd_hz to the VFD (only on change).
    Every 5 s: read Status Word + actual Hz from the drive for diagnostics.
    """
    last_status_read = 0.0
    while True:
        try:
            hz = crusher_logic.get_state().get("target_vfd_hz", 0)
            await vfd_controller.set_frequency(hz)

            now = _time.time()
            if now - last_status_read >= 5:
                await vfd_controller.read_status()
                last_status_read = now
        except Exception as e:
            log.error("VFD background task error: %s", e)
        await asyncio.sleep(1)


# ── Lifespan ─────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting Crusher Monitor backend...")
    log.info("Camera NOT auto-started. Call POST /api/camera/restart to connect.")

    # Init database
    await init_db()

    # Load YOLO model — don't block startup if file is missing
    # (e.g. dev machine without weights/best.pt). Camera /api/camera/restart
    # will retry the load when the user is ready.
    try:
        crusher_camera.load_model()
    except Exception as e:
        log.error("YOLO model load failed at startup: %s", e)
        log.error("Backend will run but /api/camera/restart will fail until the model file is present.")

    # Connect VFD Modbus
    await vfd_controller.connect()

    # Start background tasks
    task_db  = asyncio.create_task(_db_background_task())
    task_vfd = asyncio.create_task(_vfd_background_task())

    yield

    log.info("Shutting down...")
    task_db.cancel()
    task_vfd.cancel()
    await vfd_controller.stop_drive()   # safe-stop the VFD on server shutdown
    await vfd_controller.disconnect()
    crusher_camera.stop()
    await close_db()


# ── App ───────────────────────────────────────────────────────
app = FastAPI(
    title="Crusher Monitor API",
    version="3.0.0",
    lifespan=lifespan,
)

os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── WebSocket Manager ─────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        log.info(f"WebSocket connected. Total: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        log.info(f"WebSocket disconnected. Total: {len(self.active)}")

    async def broadcast(self, data: dict):
        msg  = json.dumps(data)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self.active:
                self.active.remove(ws)


manager = ConnectionManager()


# ── WebSocket Endpoint ────────────────────────────────────────
@app.websocket("/ws/camera")
async def camera_ws(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        last_frame_count = -1
        while True:
            state = crusher_camera.get_state()
            if state["frame_count"] != last_frame_count:
                last_frame_count = state["frame_count"]
                await websocket.send_text(json.dumps(state))
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        log.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


# ── REST Endpoints ────────────────────────────────────────────

@app.post("/api/auth/login")
async def auth_login(request: Request):
    """
    Server-side credential check.
    Credentials are read from .env (AUTH_USER / AUTH_PASS).
    Returns 200 + role on success, 401 on failure.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid request body"}, status_code=400)
    username = body.get("username", "")
    password = body.get("password", "")
    if username == AUTH_USER and password == AUTH_PASS:
        log.info("Login success for user: %s", username)
        return JSONResponse({"ok": True, "role": "Plant Manager", "site": "Chennimalai"})
    log.warning("Failed login attempt for user: %s", username)
    return JSONResponse({"ok": False, "error": "Invalid username or password"}, status_code=401)


@app.get("/api/config")
async def get_public_config():
    """
    Returns non-secret backend config for the dashboard to display
    (RTSP host without credentials, VFD profile, etc.).
    """
    import re
    from config import RTSP_URL, MODEL_PATH, VFD_PROFILE, VFD_MODE, VFD_PORT, VFD_ENABLED, VFD_MAX_HZ
    # Strip user:pass@ from RTSP URL before exposing
    rtsp_safe = re.sub(r"://[^@/]+@", "://", RTSP_URL or "")
    return JSONResponse({
        "rtsp_host"   : rtsp_safe,
        "model"       : os.path.basename(MODEL_PATH or ""),
        "vfd_enabled" : VFD_ENABLED,
        "vfd_profile" : VFD_PROFILE,
        "vfd_mode"    : VFD_MODE,
        "vfd_port"    : VFD_PORT if VFD_ENABLED else None,
        "vfd_max_hz"  : VFD_MAX_HZ,
    })


@app.get("/api/status")
async def get_status():
    state = crusher_camera.get_state()
    return JSONResponse({
        "cam_status"     : state.get("cam_status"),
        "machine_status" : state.get("machine_status"),
        "jaw_label"      : state.get("jaw_label"),
        "jaw_conf"       : state.get("jaw_conf"),
        "target_vfd_hz"  : state.get("target_vfd_hz"),
        "camera_fps"     : state.get("camera_fps"),
        "frame_count"    : state.get("frame_count"),
    })


@app.get("/api/crusher")
async def get_crusher():
    return JSONResponse(crusher_logic.get_state())


@app.get("/api/oee")
async def get_oee():
    state = crusher_logic.get_state()
    return JSONResponse({
        "availability_pct" : state["availability_pct"],
        "timer_run"        : state["timer_run"],
        "timer_stuck"      : state["timer_stuck"],
        "timer_no_feed"    : state["timer_no_feed"],
        "timer_idle"       : state["timer_idle"],
        "timer_shift"      : state["timer_shift"],
        "frames_running"   : state["frames_running"],
        "frames_stuck"     : state["frames_stuck"],
        "frames_no_feed"   : state["frames_no_feed"],
        "tonnage_actual"   : state["tonnage_actual"],
    })


@app.get("/api/alerts")
async def get_alerts():
    state = crusher_logic.get_state()
    return JSONResponse({
        "active_alerts" : state["active_alerts"],
        "alert_count"   : state["alert_count"],
    })


@app.get("/api/history")
async def get_history():
    return JSONResponse({"history": crusher_logic.get_history()})


@app.get("/api/snapshot")
async def get_snapshot():
    state = crusher_camera.get_state()
    if not state.get("frame"):
        return JSONResponse({"error": "No frame available"}, status_code=503)
    return JSONResponse({"frame": state["frame"]})


@app.post("/api/tonnage/{tonnes}")
async def add_tonnage(tonnes: float):
    crusher_logic.update_tonnage(tonnes)
    return JSONResponse({"ok": True, "added": tonnes})


@app.post("/api/shift/reset")
async def reset_shift():
    # Save shift report before reset
    state = crusher_logic.get_state()
    await save_shift_report(state)
    crusher_logic.reset_shift()
    return JSONResponse({"ok": True, "message": "Shift reset and report saved"})


@app.post("/api/camera/restart")
async def restart_camera():
    log.info("Manual camera restart requested via API.")
    await crusher_camera.restart()
    return JSONResponse({"ok": True, "message": "Camera restart initiated."})


@app.get("/api/camera/status")
async def camera_status():
    state = crusher_camera.get_state()
    return JSONResponse({
        "cam_status"  : state.get("cam_status"),
        "camera_fps"  : state.get("camera_fps"),
        "frame_count" : state.get("frame_count"),
    })


@app.get("/api/v1/machines/feeder-1/status")
async def flutter_machine_status():
    """Flutter app compatible endpoint"""
    state = crusher_camera.get_state()
    cs    = crusher_logic.get_state()
    return JSONResponse({
        "cam_status"      : state.get("cam_status"),
        "machine_status"  : cs.get("machine_status"),
        "jaw_label"       : cs.get("jaw_label"),
        "jaw_conf"        : cs.get("jaw_conf"),
        "target_vfd_hz"   : cs.get("target_vfd_hz"),
        "availability_pct": cs.get("availability_pct"),
        "active_alerts"   : cs.get("active_alerts"),
        "timer_run"       : cs.get("timer_run"),
        "timer_stuck"     : cs.get("timer_stuck"),
        "timer_no_feed"   : cs.get("timer_no_feed"),
        "camera_fps"      : state.get("camera_fps"),
        "shift"           : cs.get("shift"),
    })


# ── NEW DB History Endpoints ──────────────────────────────────

@app.get("/api/db/alerts")
async def db_alerts(limit: int = 100):
    """Get alert history from database"""
    data = await get_alerts_history(limit)
    return JSONResponse({"alerts": data, "count": len(data)})


@app.get("/api/db/oee")
async def db_oee(hours: int = 24):
    """Get OEE history from database"""
    data = await get_oee_history(hours)
    return JSONResponse({"oee_history": data, "count": len(data)})


@app.get("/api/db/vfd")
async def db_vfd(minutes: int = 60):
    """Get VFD history from database"""
    data = await get_vfd_history(minutes)
    return JSONResponse({"vfd_logs": data, "count": len(data)})


@app.get("/api/db/shifts")
async def db_shifts(limit: int = 30):
    """Get shift reports from database"""
    data = await get_shift_reports(limit)
    return JSONResponse({"shift_reports": data, "count": len(data)})


@app.get("/api/db/summary")
async def db_summary():
    """Get today's state summary from database"""
    data = await get_state_summary_today()
    return JSONResponse({"summary": data})


# ── VFD Endpoints ────────────────────────────────────────────

@app.get("/api/vfd/status")
async def vfd_status():
    """Live Modbus connection status and write counters."""
    return JSONResponse(vfd_controller.status)


@app.post("/api/vfd/set/{hz}")
async def vfd_set(hz: int):
    """
    Manually override the VFD frequency (0–60 Hz).
    0 sends the stop command.
    """
    if hz < 0 or hz > 60:
        return JSONResponse({"ok": False, "error": "hz must be 0–60"}, status_code=400)
    vfd_controller._last_hz = -1          # force write even if value unchanged
    await vfd_controller.set_frequency(hz)
    return JSONResponse({"ok": True, "hz": hz})


@app.post("/api/vfd/stop")
async def vfd_force_stop():
    """Send stop command to VFD immediately."""
    await vfd_controller.stop_drive()
    return JSONResponse({"ok": True, "message": "Stop command sent to VFD."})


@app.post("/api/vfd/reset-fault")
async def vfd_reset_fault():
    """
    Send the ABB fault-reset command (CW = 0x04FF).
    Use after the drive has tripped — e.g. overcurrent, undervoltage.
    """
    await vfd_controller.reset_fault()
    return JSONResponse({"ok": True, "message": "Fault-reset command sent to VFD."})


# ── Report Endpoints ─────────────────────────────────────────

@app.get("/api/reports/shift-pdf")
async def report_shift_pdf(limit: int = 20):
    """
    Generate and return a PDF containing the last `limit` shift reports.
    The browser receives it as a file download.
    """
    try:
        rows       = await get_shift_reports(limit)
        live_state = crusher_logic.get_state()
        pdf_bytes  = await asyncio.to_thread(build_shift_pdf, rows, live_state)
        filename   = f"crusher_shift_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        return Response(
            content     = pdf_bytes,
            media_type  = "application/pdf",
            headers     = {"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        log.error("PDF report error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/reports/shift-csv")
async def report_shift_csv(limit: int = 20):
    """
    Generate and return a CSV file with a Daily Summary header block
    followed by the last `limit` shift reports.
    """
    try:
        rows       = await get_shift_reports(limit)
        live_state = crusher_logic.get_state()
        csv_text   = build_shift_csv(rows, live_state)
        filename   = f"crusher_shift_report_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        return Response(
            content     = csv_text,
            media_type  = "text/csv",
            headers     = {"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        log.error("CSV report error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Period Report Endpoints (DB-based, any date range) ───────

def _period_dates(period: str, from_date: str = "", to_date: str = "") -> tuple[str, str, str]:
    """Return (from_date, to_date, label) for a given period string."""
    from datetime import date, timedelta
    today = date.today()
    if period == "1w":
        return (today - timedelta(days=6)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"), "1 Week"
    if period == "1m":
        return (today - timedelta(days=29)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"), "1 Month"
    if period == "custom" and from_date and to_date:
        return from_date, to_date, "Custom Range"
    # default: 1d
    td = today.strftime("%Y-%m-%d")
    return td, td, "1 Day"


@app.get("/api/reports/pdf")
async def report_pdf(
    period: str   = "1d",
    from_date: str = "",
    to_date: str   = "",
):
    """
    Generate PDF for any period.
    ?period=1d|1w|1m|custom  &from_date=YYYY-MM-DD &to_date=YYYY-MM-DD
    """
    try:
        fd, td, label = _period_dates(period, from_date, to_date)
        summary   = await get_period_summary(fd, td)
        pdf_bytes = await asyncio.to_thread(build_period_pdf, summary, label, fd, td)
        filename  = f"crusher_report_{fd}_{td}.pdf"
        return Response(
            content    = pdf_bytes,
            media_type = "application/pdf",
            headers    = {"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        log.error("PDF report error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/reports/csv")
async def report_csv(
    period: str   = "1d",
    from_date: str = "",
    to_date: str   = "",
):
    """Generate CSV for any period."""
    try:
        fd, td, label = _period_dates(period, from_date, to_date)
        summary  = await get_period_summary(fd, td)
        csv_text = await asyncio.to_thread(build_period_csv, summary, label, fd, td)
        filename = f"crusher_report_{fd}_{td}.csv"
        return Response(
            content    = csv_text,
            media_type = "text/csv",
            headers    = {"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        log.error("CSV report error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/reports/excel")
async def report_excel(
    period: str   = "1d",
    from_date: str = "",
    to_date: str   = "",
):
    """Generate Excel .xlsx for any period."""
    try:
        fd, td, label  = _period_dates(period, from_date, to_date)
        summary        = await get_period_summary(fd, td)
        excel_bytes    = await asyncio.to_thread(build_period_excel, summary, label, fd, td)
        filename       = f"crusher_report_{fd}_{td}.xlsx"
        return Response(
            content    = excel_bytes,
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers    = {"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        log.error("Excel report error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── MJPEG Stream ──────────────────────────────────────────────

@app.get("/stream/mjpeg")
async def mjpeg_stream():
    import base64
    async def generate():
        while True:
            state = crusher_camera.get_state()
            frame_b64 = state.get("frame")
            if frame_b64:
                frame_bytes = base64.b64decode(frame_b64)
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
            await asyncio.sleep(0.1)
    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace;boundary=frame")


# ── Dashboard ─────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(html_path):
        with open(html_path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Dashboard not found.</h1>")


# ── Entry Point ───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)

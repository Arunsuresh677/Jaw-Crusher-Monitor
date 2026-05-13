"""
camera.py — RTSP + YOLOv8 Classification + Crusher Logic Integration
Device  : Raspberry Pi 4
Plant   : Kannan Blue Metals

IMPORTANT: RTSP connects ONLY when start() is called manually.
           No auto-retry loop — prevents camera lockout.
"""

import asyncio
import cv2
import os
import time
import base64
import logging
import threading
from datetime import datetime

from ultralytics import YOLO
from crusher_logic import crusher_logic
from config import (
    MODEL_PATH, RTSP_URL, CONF_THRESHOLD,
    FRAME_INTERVAL, SAVE_ANNOTATED, OUTPUT_DIR,
    LOG_DIR, HEADLESS, VFD_SPEEDS,
)

log = logging.getLogger(__name__)


class CrusherCamera:

    def __init__(self):
        self.model        = None
        self.cap          = None
        self.running      = False
        self.thread       = None
        self.latest_frame = None
        self.latest_state = {}
        self.status       = "stopped"
        self.fps_actual   = 0.0
        self.frame_count  = 0
        self._lock        = threading.Lock()

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------
    def load_model(self):
        if not os.path.exists(MODEL_PATH):
            log.error(f"Model not found: {MODEL_PATH}")
            raise RuntimeError(f"YOLO model file missing: {MODEL_PATH}")
        log.info(f"Loading YOLO model: {MODEL_PATH}")
        self.model = YOLO(MODEL_PATH)
        log.info("YOLO model loaded successfully.")

    # ------------------------------------------------------------------
    # RTSP connection — single attempt only, NO auto-retry
    # ------------------------------------------------------------------
    def connect_rtsp(self):
        log.info(f"Attempting RTSP connection: {RTSP_URL}")
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def get_latest_frame(self):
        """Flush buffer and get freshest frame."""
        for _ in range(3):
            self.cap.grab()
        return self.cap.retrieve()

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------
    def start(self):
        """
        Call this ONCE manually to start the camera.
        If RTSP fails, the thread exits — call start() again to retry.
        """
        if self.running:
            log.warning("Camera already running.")
            return

        if self.model is None:
            self.load_model()

        self.running = True
        self.thread  = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        log.info("CrusherCamera thread started.")

    def stop(self):
        """Cleanly stop the camera."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        if self.cap:
            self.cap.release()
            self.cap = None
        self._set_status("stopped")
        log.info("CrusherCamera stopped.")

    async def restart(self):
        """Stop and restart — useful from async API endpoint."""
        log.info("Restarting camera...")
        self.stop()
        await asyncio.sleep(2)
        self.start()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _set_status(self, s: str):
        with self._lock:
            self.status = s

    def _update(self, frame_b64: str, state: dict, fps: float):
        with self._lock:
            self.latest_frame = frame_b64
            self.latest_state = state
            self.fps_actual   = fps
            self.frame_count += 1

    def get_state(self) -> dict:
        with self._lock:
            return {
                "frame"       : self.latest_frame,
                "camera_fps"  : round(self.fps_actual, 1),
                "frame_count" : self.frame_count,
                "cam_status"  : self.status,
                **self.latest_state,
            }

    # ------------------------------------------------------------------
    # Main camera loop — auto-reconnect on stream loss
    # ------------------------------------------------------------------
    def _loop(self):
        _MAX_FAILS    = 15    # consecutive frame failures before reconnect
        _MAX_RETRIES  = 10    # reconnect attempts before giving up
        _RETRY_DELAY  = 5     # seconds between reconnect attempts
        _retry_count  = 0

        while self.running and _retry_count <= _MAX_RETRIES:

            # ── Connect ───────────────────────────────────────────────
            self._set_status("connecting")
            log.info(f"RTSP connect attempt {_retry_count + 1}/{_MAX_RETRIES + 1}")
            self.cap = self.connect_rtsp()

            if not self.cap.isOpened():
                _retry_count += 1
                log.error(
                    f"RTSP connection failed (attempt {_retry_count}). "
                    f"Retrying in {_RETRY_DELAY}s..."
                )
                self._set_status("error")
                if _retry_count > _MAX_RETRIES:
                    log.error("Max reconnect attempts reached. Giving up.")
                    self.running = False
                    return
                time.sleep(_RETRY_DELAY)
                continue

            log.info("RTSP connected successfully.")
            self._set_status("live")
            _retry_count = 0   # reset on successful connect
            _fail_count  = 0

            # ── Frame loop ────────────────────────────────────────────
            while self.running:
                t0 = time.time()

                ret, frame = self.get_latest_frame()

                if not ret or frame is None:
                    _fail_count += 1
                    log.warning(f"RTSP frame read failed ({_fail_count}/{_MAX_FAILS})")
                    if _fail_count >= _MAX_FAILS:
                        log.error("RTSP stream lost — attempting reconnect...")
                        self._set_status("error")
                        if self.cap:
                            self.cap.release()
                            self.cap = None
                        break   # break inner loop → outer loop reconnects
                    time.sleep(0.1)
                    continue

                # Frame OK — reset failure counter
                _fail_count = 0
                if self.status != "live":
                    self._set_status("live")

            # ── YOLO inference ────────────────────────────────────────
            try:
                results   = self.model(frame, conf=CONF_THRESHOLD, verbose=False)
                result    = results[0]
                annotated = result.plot()
                label     = "unknown"
                conf      = 0.0

                if result.probs is not None:
                    top_id = int(result.probs.top1)
                    conf   = float(result.probs.top1conf)
                    label  = result.names[top_id].lower().replace("_", " ")

            except Exception as e:
                log.error(f"YOLO inference error: {e}")
                continue

            # ── Crusher logic update ──────────────────────────────────
            crusher_logic.update(label, conf)
            crusher_state = crusher_logic.get_state()

            # ── On-frame overlay ──────────────────────────────────────
            if not HEADLESS:
                target_hz    = VFD_SPEEDS.get(label, 0)
                status       = crusher_state["machine_status"]
                status_color = (0, 255, 0) if status == "NORMAL" else (0, 80, 255)
                cv2.rectangle(annotated, (20, 20), (620, 200), (30, 30, 30), -1)
                cv2.putText(annotated, f"AI State : {label}",
                            (40, 60),  cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                cv2.putText(annotated, f"VFD      : {target_hz} Hz",
                            (40, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                cv2.putText(annotated, f"Status   : {status}",
                            (40, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
                cv2.putText(annotated, f"Conf     : {conf:.2f}",
                            (40, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1)

            # ── Encode frame to base64 for WebSocket ──────────────────
            try:
                _, buffer = cv2.imencode(
                    ".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75]
                )
                frame_b64 = base64.b64encode(buffer).decode("utf-8")
            except Exception as e:
                log.error(f"Frame encode error: {e}")
                continue

            # ── Save annotated frame if enabled ───────────────────────
            if SAVE_ANNOTATED:
                try:
                    os.makedirs(OUTPUT_DIR, exist_ok=True)
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    cv2.imwrite(f"{OUTPUT_DIR}/frame_{ts}.jpg", annotated)
                except Exception as e:
                    log.error(f"Frame save error: {e}")

            # ── Show window if not headless ───────────────────────────
            if not HEADLESS:
                cv2.imshow("Crusher YOLO Live", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    log.info("Q pressed. Stopping.")
                    self.running = False
                    break

            # ── FPS control ───────────────────────────────────────────
            elapsed    = time.time() - t0
            fps        = 1.0 / elapsed if elapsed > 0 else 0
            self._update(frame_b64, crusher_state, fps)

            sleep_time = FRAME_INTERVAL - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        # ── Cleanup ───────────────────────────────────────────────────
        if self.cap:
            self.cap.release()
            self.cap = None
        if not HEADLESS:
            cv2.destroyAllWindows()
        log.info("Camera loop exited cleanly.")


# Global singleton
crusher_camera = CrusherCamera()

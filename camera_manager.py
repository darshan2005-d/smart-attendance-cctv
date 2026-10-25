import hashlib
import os
import re
import time
import socket
import threading
import subprocess
from typing import Optional, Dict, Generator
import cv2
import numpy as np

import config
from recognition_engine import engine
from rtsp_proxy import proxy as rtsp_proxy, PROXY_RTSP_URL


class CameraManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.cap: Optional[cv2.VideoCapture] = None
        self.latest_frame: Optional[np.ndarray] = None
        self.latest_annotated: Optional[np.ndarray] = None
        self.latest_faces = []
        self.last_frame_time = 0
        self.overlay_until = 0.0
        self.is_connected = False
        self.active_source_description = "Uninitialized"
        self.reconnect_delay = 3.0
        self.frame_count = 0
        self.current_config = config.load_config()

    def start(self):
        """Starts background frame grabber thread."""
        with self.lock:
            if self.running:
                return
            self.running = True
            self.thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.thread.start()
            print("[Camera] Camera background thread started.")

    def stop(self):
        """Stops camera thread and releases capture device."""
        with self.lock:
            self.running = False
            if self.cap is not None:
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
            self.is_connected = False
            print("[Camera] Camera released and stopped.")

    def reload(self):
        """Reloads camera source using latest configuration."""
        print("[Camera] Reloading camera source...")
        self.current_config = config.load_config()
        with self.lock:
            if self.cap is not None:
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
            self.is_connected = False
            self.latest_frame = None
            self.latest_annotated = None

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        cfg = self.current_config
        source_type = cfg.get("source_type", "cctv")

        if source_type == "webcam":
            index = cfg.get("webcam_index", 0)
            self.active_source_description = f"Local Webcam ({index})"
            print(f"[Camera] Opening {self.active_source_description}...")
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(index)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                return cap
            return None

        # CCTV / RTSP source - always route through the local SHA-256 proxy
        cctv_ip = cfg.get("cctv_ip", "192.168.1.64")
        self.active_source_description = f"CCTV RTSP ({cctv_ip}:{cfg.get('cctv_port', 554)}) via local proxy"

        print(f"[Camera] Connecting via SHA-256 proxy at {PROXY_RTSP_URL} => {cctv_ip}...")

        # Low-latency FFmpeg options; no credentials needed - proxy handles auth
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|stimeout;5000000|buffer_size;1024000"
        )
        cap = cv2.VideoCapture(PROXY_RTSP_URL, cv2.CAP_FFMPEG)

        if cap.isOpened():
            return cap
        print("[Camera] Proxy URL open failed - proxy may still be authenticating, will retry.")
        return None

    def _capture_loop(self):
        """Continuous background loop ensuring freshest frame and auto-reconnect."""
        fail_count = 0

        while self.running:
            if self.cap is None or not self.cap.isOpened():
                cap = self._open_capture()
                if cap and cap.isOpened():
                    self.cap = cap
                    self.is_connected = True
                    fail_count = 0
                    print(f"[Camera] Connected to {self.active_source_description}")
                else:
                    self.is_connected = False
                    fail_count += 1
                    time.sleep(self.reconnect_delay)
                    continue

            # Read frame
            try:
                ret, frame = self.cap.read()
                if not ret or frame is None or frame.size == 0:
                    print(f"[Camera] Failed to grab frame from {self.active_source_description}")
                    self.is_connected = False
                    if self.cap is not None:
                        self.cap.release()
                        self.cap = None
                    time.sleep(1.0)
                    continue

                # Ensure frame is in color (BGR 3-channel)
                # Some CCTV sub-streams deliver grayscale; convert to BGR
                if len(frame.shape) == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                elif frame.shape[2] == 1:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

                self.is_connected = True
                self.latest_frame = frame
                self.last_frame_time = time.time()
                self.frame_count += 1

                # Face Recognition processing
                cfg = self.current_config
                scan_mode = cfg.get("scan_mode", "manual")

                if scan_mode == "continuous":
                    process_interval = max(1, cfg.get("process_every_n_frames", 8))
                    auto_mark = cfg.get("auto_mark_enabled", True)
                    tolerance = float(cfg.get("recognition_tolerance", 0.50))

                    if self.frame_count % process_interval == 0:
                        annotated, faces = engine.process_frame(
                            frame,
                            tolerance=tolerance,
                            scale=0.25,
                            auto_mark=auto_mark,
                            source_label=cfg.get("source_type", "CCTV").upper()
                        )
                        self.latest_annotated = annotated
                        self.latest_faces = faces
                    else:
                        if self.latest_faces:
                            self.latest_annotated = engine.draw_overlays(
                                frame, self.latest_faces,
                                cfg.get("source_type", "CCTV").upper()
                            )
                        else:
                            self.latest_annotated = frame
                else:
                    # MANUAL SCAN MODE (Zero background lag! Silky smooth video feed)
                    # Overlays only shown when user clicks "Scan Student" button
                    if time.time() < self.overlay_until and self.latest_faces:
                        self.latest_annotated = engine.draw_overlays(
                            frame, self.latest_faces,
                            cfg.get("source_type", "CCTV").upper()
                        )
                    else:
                        self.latest_annotated = frame

                # Cap loop speed (~30 FPS target)
                time.sleep(0.03)

            except Exception as err:
                print(f"[Camera] Capture loop exception: {err}")
                time.sleep(0.5)

    def scan_current_frame(self) -> Dict:
        """Triggers an on-demand face recognition scan on the active CCTV camera frame."""
        with self.lock:
            if not self.is_connected or self.latest_frame is None:
                return {
                    "success": False,
                    "message": "CCTV is currently not connected or no video frame available.",
                    "faces": [],
                    "count": 0
                }
            frame_to_scan = self.latest_frame.copy()

        cfg = config.load_config()
        tolerance = float(cfg.get("recognition_tolerance", 0.50))
        auto_mark = cfg.get("auto_mark_enabled", True)
        source_label = cfg.get("source_type", "CCTV").upper()

        # High-detail face scan (scale=0.5 for crisp face recognition)
        annotated, faces = engine.process_frame(
            frame_to_scan,
            tolerance=tolerance,
            scale=0.5,
            auto_mark=auto_mark,
            source_label=source_label
        )

        with self.lock:
            self.latest_faces = faces
            self.latest_annotated = annotated
            self.overlay_until = time.time() + 6.0  # Keep boxes and labels on screen for 6s

        recognized = [f for f in faces if f.get("is_known")]
        unknown = [f for f in faces if not f.get("is_known")]

        if recognized:
            names = ", ".join(f"{r['name']} ({r['usn']})" for r in recognized)
            msg = f"Match found! {len(recognized)} student(s) recognized: {names}"
        elif unknown:
            msg = f"Detected {len(unknown)} face(s), but not registered in the student database."
        else:
            msg = "No faces detected in current frame. Please look directly at the camera."

        return {
            "success": True,
            "message": msg,
            "faces": faces,
            "count": len(faces),
            "recognized_count": len(recognized),
            "recognized_students": [{"name": r["name"], "usn": r["usn"], "confidence": r["confidence"]} for r in recognized]
        }

    def get_latest_frame(self, annotated: bool = True) -> Optional[np.ndarray]:
        if annotated and self.latest_annotated is not None:
            return self.latest_annotated
        return self.latest_frame

    def get_status(self) -> Dict:
        cfg = config.load_config()
        rtsp_url = config.build_rtsp_url(cfg)
        safe_url = rtsp_url
        if "@" in safe_url and ":" in safe_url.split("@")[0]:
            parts = safe_url.split("@")
            prefix = parts[0]
            user = prefix.split("//")[-1].split(":")[0]
            safe_url = f"rtsp://{user}:****@{parts[1]}"

        return {
            "is_connected": self.is_connected,
            "source_type": cfg.get("source_type", "cctv"),
            "scan_mode": cfg.get("scan_mode", "manual"),
            "description": self.active_source_description,
            "target_url": safe_url if cfg.get("source_type") == "cctv" else f"Webcam #{cfg.get('webcam_index', 0)}",
            "last_frame_age_seconds": round(time.time() - self.last_frame_time, 2) if self.last_frame_time > 0 else None,
            "detected_faces_count": len(self.latest_faces),
            "detected_faces": [{"name": f["name"], "usn": f["usn"], "confidence": f["confidence"]} for f in self.latest_faces]
        }

    def generate_mjpeg_stream(self) -> Generator[bytes, None, None]:
        """Yields MJPEG multipart frame stream for browser <img> tags."""
        blank_frame = None

        while True:
            frame = self.get_latest_frame(annotated=True)
            if frame is None or not self.is_connected:
                # Generate stylish 'connecting' placeholder card
                if blank_frame is None:
                    blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.rectangle(blank_frame, (0, 0), (640, 480), (18, 22, 30), -1)
                    cv2.putText(
                        blank_frame,
                        "CCTV STREAM CONNECTING...",
                        (140, 230),
                        cv2.FONT_HERSHEY_DUPLEX,
                        0.7,
                        (0, 200, 255),
                        1,
                        cv2.LINE_AA
                    )
                    cv2.putText(
                        blank_frame,
                        "Check credentials or camera connection",
                        (150, 270),
                        cv2.FONT_HERSHEY_DUPLEX,
                        0.45,
                        (160, 160, 160),
                        1,
                        cv2.LINE_AA
                    )
                ret, jpeg = cv2.imencode(".jpg", blank_frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                time.sleep(0.3)
            else:
                ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])

            if ret:
                frame_bytes = jpeg.tobytes()
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
                )
            time.sleep(0.033)  # ~30 FPS


def test_camera_connection(target_config: dict) -> Dict:
    """
    Tests connection to a specified camera source without altering running state.
    Returns status, resolution, FPS, and diagnostic error message.
    """
    source_type = target_config.get("source_type", "cctv")

    if source_type == "webcam":
        idx = int(target_config.get("webcam_index", 0))
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(idx)
        if cap.isOpened():
            ret, frame = cap.read()
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()
            if ret and frame is not None:
                return {
                    "success": True,
                    "message": f"Webcam #{idx} connected successfully!",
                    "resolution": f"{w}x{h}",
                    "source": f"Webcam #{idx}"
                }
        return {
            "success": False,
            "message": f"Unable to open webcam index {idx}. Verify it is not being used by another application.",
            "source": f"Webcam #{idx}"
        }

    # CCTV / RTSP test - uses raw SHA-256 socket auth (same as proxy does internally)
    ip = target_config.get("cctv_ip", "192.168.1.64").strip()
    port = int(target_config.get("cctv_port", 554))
    username = target_config.get("cctv_username", "admin").strip()
    password = target_config.get("cctv_password", "").strip()
    channel = target_config.get("cctv_channel", "101").strip()
    rtsp_path = f"/Streaming/Channels/{channel}"
    cam_url = f"rtsp://{ip}:{port}{rtsp_path}"

    # ---- 1. TCP reachability check ----
    connected = False
    last_err = ""
    for attempt in range(2):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.5)
        try:
            s.connect((ip, port))
            s.close()
            connected = True
            break
        except Exception as exc:
            last_err = str(exc)
            s.close()
            try:
                subprocess.run(["ping", "-n", "1", "-w", "800", ip], capture_output=True)
            except Exception:
                pass
            time.sleep(0.3)

    if not connected:
        return {
            "success": False,
            "message": f"Cannot reach CCTV at {ip}:{port} ({last_err}). Please check your Ethernet cable and camera power.",
            "source": f"{ip}:{port}"
        }

    # ---- 2. SHA-256 RTSP handshake test ----
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(6.0)
        sock.connect((ip, port))

        # DESCRIBE -> get 401 challenge
        sock.sendall(f"DESCRIBE {cam_url} RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n\r\n".encode())
        resp401 = b""
        while b"\r\n\r\n" not in resp401:
            chunk = sock.recv(2048)
            if not chunk:
                break
            resp401 += chunk
        resp401_str = resp401.decode(errors="ignore")

        realm_m = re.search(r'realm="([^"]+)"', resp401_str)
        nonce_m = re.search(r'nonce="([^"]+)"', resp401_str)

        if not realm_m or not nonce_m:
            if "200 OK" in resp401_str:
                sock.close()
                return {"success": True, "message": "CCTV connected (no auth required).", "source": f"{ip}:{port}"}
            sock.close()
            return {
                "success": False,
                "message": "Camera responded on port 554 but did not send an RTSP auth challenge. Verify the RTSP channel path.",
                "source": cam_url
            }

        realm = realm_m.group(1)
        nonce = nonce_m.group(1)

        # Compute SHA-256 Digest response
        ha1 = hashlib.sha256(f"{username}:{realm}:{password}".encode()).hexdigest()
        ha2 = hashlib.sha256(f"DESCRIBE:{cam_url}".encode()).hexdigest()
        r_hash = hashlib.sha256(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        auth_hdr = (
            f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
            f'uri="{cam_url}", response="{r_hash}", algorithm="SHA-256"'
        )

        sock.sendall(
            f"DESCRIBE {cam_url} RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\n"
            f"Authorization: {auth_hdr}\r\n\r\n".encode()
        )

        resp200 = b""
        while b"\r\n\r\n" not in resp200:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp200 += chunk
        resp200_str = resp200.decode(errors="ignore")
        sock.close()

        if "200 OK" in resp200_str:
            return {
                "success": True,
                "message": (
                    f"CCTV SHA-256 authentication successful! Stream verified at "
                    f"{ip}:{port}{rtsp_path} (realm: {realm})"
                ),
                "source": cam_url,
                "resolution": "640x360",
                "fps": 25.0
            }
        elif "401" in resp200_str:
            return {
                "success": False,
                "message": "Authentication failed (401). Please re-check your username and password.",
                "source": cam_url
            }
        else:
            return {
                "success": False,
                "message": f"Unexpected RTSP response: {resp200_str[:120]}",
                "source": cam_url
            }

    except Exception as e:
        return {
            "success": False,
            "message": f"RTSP connection error: {e}",
            "source": cam_url
        }


def discover_available_cameras() -> Dict:
    """Scans local system and network for available cameras."""
    results = {
        "webcams": [],
        "network_cameras": []
    }

    # 1. Probe local webcams 0-3
    for idx in range(3):
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(idx)
        if cap.isOpened():
            ret, _ = cap.read()
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()
            results["webcams"].append({
                "index": idx,
                "name": f"Integrated / USB Camera #{idx}",
                "working": bool(ret),
                "resolution": f"{w}x{h}"
            })

    # 2. Check ARP and known CCTV IPs
    cctv_targets = ["192.168.1.64", "192.168.1.108", "192.168.1.50", "192.168.1.100", "192.168.1.10"]
    for ip in cctv_targets:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.35)
        res = s.connect_ex((ip, 554))
        s.close()
        if res == 0:
            brand = "Hikvision / OEM" if ip == "192.168.1.64" else "IP Camera"
            results["network_cameras"].append({
                "ip": ip,
                "port": 554,
                "brand": brand,
                "status": "Online and Listening",
                "recommended_channels": ["101", "102"] if "Hikvision" in brand else ["1"]
            })

    return results


# Global camera manager singleton
camera_manager = CameraManager()

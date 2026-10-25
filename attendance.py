import os
import cv2
import time
from datetime import datetime
import pandas as pd

import config
import database
from recognition_engine import engine
from rtsp_proxy import proxy as rtsp_proxy, PROXY_RTSP_URL

def main():
    print("=" * 60)
    print("  SMART CLASSROOM ATTENDANCE - DESKTOP MONITOR")
    print("=" * 60)

    # Ensure DB & Encodings
    database.init_db()
    encodings_count = engine.load_encodings()
    if encodings_count == 0:
        print("[Error] No face encodings found in trainer/encodings.pickle.")
        print("Please enroll students and run 'python train_faces.py' first.")
        return

    cfg = config.load_config()
    source_type = cfg.get("source_type", "cctv")
    tolerance = float(cfg.get("recognition_tolerance", 0.50))
    cooldown = int(cfg.get("cooldown_seconds", 60))

    if source_type == "cctv":
        cctv_ip = cfg.get("cctv_ip", "192.168.1.64")
        channel = cfg.get("cctv_channel", "101").strip()
        print(f"[Camera] Starting local SHA-256 RTSP proxy for CCTV ({cctv_ip})...")
        rtsp_proxy.configure(
            ip=cctv_ip,
            port=int(cfg.get("cctv_port", 554)),
            username=cfg.get("cctv_username", "admin"),
            password=cfg.get("cctv_password", ""),
            rtsp_path=f"/Streaming/Channels/{channel}",
        )
        rtsp_proxy.start()
        time.sleep(0.5)

        print(f"[Camera] Connecting to CCTV via proxy...")
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;4000000"
        cap = cv2.VideoCapture(PROXY_RTSP_URL, cv2.CAP_FFMPEG)
    else:
        idx = int(cfg.get("webcam_index", 0))
        print(f"[Camera] Opening Webcam #{idx}...")
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(idx)

    if not cap.isOpened():
        print("[Error] Unable to open camera source.")
        print("If using CCTV, verify your IP (192.168.1.64) and credentials in camera_config.json or http://127.0.0.1:5000/camera")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n[System] Live Attendance Active for Date: {today}")
    print("[System] Press 'ESC' or 'Q' to quit.\n")

    frame_count = 0
    annotated = None

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            print("[Warning] Frame read failed. Reconnecting in 1 second...")
            time.sleep(1.0)
            continue

        frame_count += 1
        # Process face recognition every 2 frames for optimal speed
        if frame_count % 2 == 0 or annotated is None:
            annotated, faces = engine.process_frame(
                frame,
                tolerance=tolerance,
                scale=0.5,
                auto_mark=True,
                source_label=source_type.upper()
            )

        cv2.imshow("Smart Classroom Attendance AI - Monitor", annotated)

        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord('q'), ord('Q')):
            break

    cap.release()
    if source_type == "cctv":
        rtsp_proxy.stop()
    cv2.destroyAllWindows()
    print("\n[System] Attendance session ended.")

if __name__ == "__main__":
    main()

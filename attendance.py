import os
import cv2
import time
from datetime import datetime
import pandas as pd

import config
import database
from recognition_engine import engine

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
    tolerance = float(cfg.get("recognition_tolerance", 0.48))
    cooldown = int(cfg.get("cooldown_seconds", 60))

    if source_type == "cctv":
        target_source = config.build_rtsp_url(cfg)
        print(f"[Camera] Connecting to CCTV RTSP Stream: {cfg.get('cctv_ip', '192.168.1.64')}:{cfg.get('cctv_port', 554)}...")
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;3000000"
        cap = cv2.VideoCapture(target_source, cv2.CAP_FFMPEG)
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
    cv2.destroyAllWindows()
    print("\n[System] Attendance session ended.")

if __name__ == "__main__":
    main()

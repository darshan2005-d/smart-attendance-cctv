import os
import cv2
import time
import face_recognition

import config
import database
from recognition_engine import engine

def main():
    print("=" * 60)
    print("      STUDENT REGISTRATION & ENROLLMENT ASSISTANT")
    print("=" * 60)

    database.init_db()

    usn = input("Enter Student USN (e.g. 4KV23EC011): ").strip().upper()
    name = input("Enter Student Full Name           : ").strip().upper()

    if not usn or not name:
        print("[Error] USN and Name cannot be empty.")
        return

    # Create dataset folder
    student_folder = config.DATASET_PATH / usn
    student_folder.mkdir(parents=True, exist_ok=True)

    # Save to database and CSV
    database.save_student(usn, name)
    print(f"\n[Database] Registered {name} ({usn})")

    # Camera selection
    cfg = config.load_config()
    source_type = cfg.get("source_type", "cctv")

    print(f"[Camera] Connecting to {source_type.upper()}...")
    if source_type == "cctv":
        target = config.build_rtsp_url(cfg)
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;3000000"
        cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG)
    else:
        idx = int(cfg.get("webcam_index", 0))
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(idx)

    if not cap.isOpened():
        print(f"[Error] Failed to open {source_type.upper()} camera.")
        print("Please check camera settings in camera_config.json or http://127.0.0.1:5000/camera")
        return

    print("\n[Instructions]")
    print("- Look at the camera.")
    print("- Move head slightly left, right, up, down to capture diverse angles.")
    print("- Press 'C' to capture a sample, or 'SPACE' to toggle auto-capture.")
    print("- Press 'ESC' or 'Q' to finish early.")
    print("Target: 20-30 clear face images.\n")

    captured_count = len([f for f in student_folder.glob("*.jpg")])
    auto_capture = True
    last_capture_time = 0

    while captured_count < 30:
        ret, frame = cap.read()
        if not ret or frame is None:
            time.sleep(0.1)
            continue

        display = frame.copy()
        h, w = frame.shape[:2]

        # Detect face
        small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        boxes = face_recognition.face_locations(rgb_small)

        color = (0, 255, 0) if boxes else (0, 165, 255)
        for box in boxes:
            top, right, bottom, left = [v * 2 for v in box]
            cv2.rectangle(display, (left, top), (right, bottom), (0, 255, 0), 2)

        # Status HUD
        cv2.putText(display, f"Enrolling: {name} ({usn})", (20, 30), cv2.FONT_HERSHEY_DUPLEX, 0.7, (255, 255, 255), 1)
        cv2.putText(display, f"Captured: {captured_count}/30 photos", (20, 60), cv2.FONT_HERSHEY_DUPLEX, 0.7, (0, 255, 0), 1)
        status_txt = "AUTO-CAPTURE ACTIVE" if auto_capture else "MANUAL (Press C to capture)"
        cv2.putText(display, status_txt, (20, 90), cv2.FONT_HERSHEY_DUPLEX, 0.5, (0, 255, 255), 1)

        cv2.imshow("Enrollment Camera - Smart Attendance", display)

        now = time.time()
        key = cv2.waitKey(1) & 0xFF

        if key in (27, ord('q'), ord('Q')):
            break
        elif key == 32:  # Spacebar toggles auto-capture
            auto_capture = not auto_capture
        elif key in (ord('c'), ord('C')) or (auto_capture and boxes and (now - last_capture_time) > 0.4):
            captured_count += 1
            filename = student_folder / f"{captured_count}.jpg"
            cv2.imwrite(str(filename), frame)
            last_capture_time = now
            print(f"Captured photo #{captured_count}/30 -> {filename.name}")

    cap.release()
    cv2.destroyAllWindows()

    print(f"\n[Completed] Saved {captured_count} face images for {name}.")
    train_choice = input("Would you like to retrain the face model now? (y/n): ").strip().lower()
    if train_choice == 'y':
        print("\nRetraining model...")
        engine.train()
        print("Training complete! Model is ready for live recognition.")

if __name__ == "__main__":
    main()

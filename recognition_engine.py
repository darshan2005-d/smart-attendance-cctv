import os
import pickle
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import collections

import cv2
import face_recognition
import numpy as np

import config
import database

# In-memory queue of recent live attendance events for the frontend
recent_events = collections.deque(maxlen=30)
recent_cooldowns = {}  # {usn: timestamp}


class RecognitionEngine:
    def __init__(self):
        self.known_encodings = []
        self.known_names = []
        self.known_usns = []
        self.last_loaded = 0
        self.load_encodings()

    def load_encodings(self) -> int:
        """Loads face encodings from trainer/encodings.pickle."""
        if not config.ENCODINGS_FILE.exists():
            print("[Recognition] encodings.pickle not found.")
            self.known_encodings = []
            self.known_names = []
            self.known_usns = []
            return 0

        try:
            with config.ENCODINGS_FILE.open("rb") as f:
                data = pickle.load(f)
            self.known_encodings = data.get("encodings", [])
            self.known_names = data.get("names", [])
            self.known_usns = data.get("usns", [])
            self.last_loaded = time.time()
            print(f"[Recognition] Successfully loaded {len(self.known_encodings)} encodings.")
            return len(self.known_encodings)
        except Exception as err:
            print(f"[Recognition] Error loading encodings: {err}")
            return 0

    def process_frame(
        self,
        frame: np.ndarray,
        tolerance: float = 0.48,
        scale: float = 0.5,
        auto_mark: bool = True,
        source_label: str = "CCTV"
    ) -> Tuple[np.ndarray, List[Dict]]:
        """
        Detects faces in frame, recognizes students, marks attendance if enabled,
        and draws visual feedback on the frame.
        """
        if frame is None or frame.size == 0:
            return frame, []

        height, width = frame.shape[:2]
        
        # Downscale for fast face detection
        if scale != 1.0:
            small_frame = cv2.resize(frame, (0, 0), fx=scale, fy=scale)
        else:
            small_frame = frame

        rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

        # Detect face locations using HOG model (much faster than CNN on CPU)
        face_locations = face_recognition.face_locations(rgb_small, model="hog")
        face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

        detected = []
        now_ts = time.time()
        today_str = datetime.now().strftime("%Y-%m-%d")
        time_str = datetime.now().strftime("%H:%M:%S")

        inv_scale = 1.0 / scale if scale > 0 else 1.0

        for face_encoding, face_location in zip(face_encodings, face_locations):
            name = "Unknown"
            usn = "Unknown"
            confidence = 0.0
            is_known = False

            if len(self.known_encodings) > 0:
                face_distances = face_recognition.face_distance(self.known_encodings, face_encoding)
                if len(face_distances) > 0:
                    best_match_idx = face_distances.argmin()
                    best_dist = float(face_distances[best_match_idx])
                    if best_dist <= tolerance:
                        name = self.known_names[best_match_idx]
                        usn = self.known_usns[best_match_idx]
                        is_known = True
                        # Confidence score formula (1.0 - distance) scaled to percentage
                        confidence = round(max(0.0, min(100.0, (1.0 - best_dist) * 100)), 1)
                    else:
                        confidence = round(max(0.0, min(100.0, (1.0 - best_dist) * 100)), 1)

            # Map coordinates back to original frame size
            top, right, bottom, left = [int(v * inv_scale) for v in face_location]
            top = max(0, top)
            left = max(0, left)
            bottom = min(height, bottom)
            right = min(width, right)

            face_info = {
                "name": name,
                "usn": usn,
                "confidence": confidence,
                "is_known": is_known,
                "box": [top, right, bottom, left]
            }
            detected.append(face_info)

            # Automated attendance marking if student is recognized
            if is_known and auto_mark and usn != "Unknown":
                last_marked_time = recent_cooldowns.get(usn, 0)
                cfg = config.load_config()
                cooldown_period = cfg.get("cooldown_seconds", 60)

                if (now_ts - last_marked_time) > cooldown_period:
                    success, msg = database.mark_attendance(
                        usn=usn,
                        name=name,
                        date_str=today_str,
                        time_str=time_str,
                        status="Present",
                        method=source_label
                    )
                    recent_cooldowns[usn] = now_ts
                    if success:
                        event_record = {
                            "usn": usn,
                            "name": name,
                            "time": time_str,
                            "date": today_str,
                            "confidence": confidence,
                            "status": "Present",
                            "method": source_label,
                            "msg": f"Attendance marked for {name}"
                        }
                        recent_events.appendleft(event_record)
                        print(f"[Attendance] Auto-marked: {name} ({usn}) via {source_label} at {time_str}")

        # Draw overlays on frame
        annotated_frame = self.draw_overlays(frame, detected, source_label)
        return annotated_frame, detected

    def draw_overlays(self, frame: np.ndarray, detected_faces: List[Dict], source_label: str) -> np.ndarray:
        output = frame.copy()
        font = cv2.FONT_HERSHEY_DUPLEX

        for face in detected_faces:
            top, right, bottom, left = face["box"]
            is_known = face["is_known"]
            name = face["name"]
            usn = face["usn"]
            conf = face["confidence"]

            if is_known:
                # Modern Emerald Green for recognized
                primary_color = (46, 204, 113)   # BGR: Green
                bg_color = (25, 135, 84)
                label_text = f"{name} ({usn}) - {conf}%"
            else:
                # Coral Crimson for unknown
                primary_color = (46, 46, 235)    # BGR: Red
                bg_color = (30, 30, 180)
                label_text = "Unknown Face"

            # Draw rounded or corner bounding box
            cv2.rectangle(output, (left, top), (right, bottom), primary_color, 2)

            # Draw corner accents
            corner_len = min(20, (right - left) // 4, (bottom - top) // 4)
            cv2.line(output, (left, top), (left + corner_len, top), primary_color, 4)
            cv2.line(output, (left, top), (left, top + corner_len), primary_color, 4)
            cv2.line(output, (right, top), (right - corner_len, top), primary_color, 4)
            cv2.line(output, (right, top), (right, top + corner_len), primary_color, 4)
            cv2.line(output, (left, bottom), (left + corner_len, bottom), primary_color, 4)
            cv2.line(output, (left, bottom), (left, bottom - corner_len), primary_color, 4)
            cv2.line(output, (right, bottom), (right - corner_len, bottom), primary_color, 4)
            cv2.line(output, (right, bottom), (right, bottom - corner_len), primary_color, 4)

            # Draw top label tag
            (text_w, text_h), baseline = cv2.getTextSize(label_text, font, 0.5, 1)
            tag_top = max(0, top - text_h - 10)
            tag_bottom = top
            tag_right = min(output.shape[1], left + text_w + 14)

            cv2.rectangle(output, (left, tag_top), (tag_right, tag_bottom), bg_color, -1)
            cv2.putText(
                output,
                label_text,
                (left + 6, tag_bottom - 5),
                font,
                0.5,
                (255, 255, 255),
                1,
                cv2.LINE_AA
            )

        # Draw system status banner at top
        banner_bg = (20, 24, 33)
        cv2.rectangle(output, (0, 0), (output.shape[1], 32), banner_bg, -1)
        
        # Source badge
        status_text = f"SMART ATTENDANCE  |  FEED: {source_label.upper()}  |  FACES DETECTED: {len(detected_faces)}"
        cv2.putText(output, status_text, (14, 21), font, 0.45, (220, 220, 220), 1, cv2.LINE_AA)
        
        # Live Indicator dot
        cv2.circle(output, (output.shape[1] - 50, 16), 5, (0, 230, 0), -1)
        cv2.putText(output, "LIVE", (output.shape[1] - 40, 20), font, 0.4, (0, 230, 0), 1, cv2.LINE_AA)

        return output

    def train(self) -> Dict:
        """Trains face encodings from dataset/ folder and writes to trainer/encodings.pickle."""
        students = {}
        for s in database.load_students_from_csv():
            students[s["usn"]] = s["name"]

        conn = database.get_db_connection()
        try:
            for row in conn.execute("SELECT usn, name FROM students").fetchall():
                students[row["usn"]] = row["name"]
        finally:
            conn.close()

        known_encodings = []
        known_names = []
        known_usns = []
        processed_students = 0
        total_images = 0

        dataset_path = config.DATASET_PATH
        if not dataset_path.exists():
            dataset_path.mkdir(parents=True, exist_ok=True)

        for usn in sorted(os.listdir(dataset_path)):
            student_folder = dataset_path / usn
            if not student_folder.is_dir():
                continue

            name = students.get(usn, usn)
            student_images = 0

            for img_name in sorted(os.listdir(student_folder)):
                if not img_name.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")):
                    continue
                img_path = student_folder / img_name
                img = cv2.imread(str(img_path))
                if img is None:
                    continue

                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                boxes = face_recognition.face_locations(rgb)
                encodings = face_recognition.face_encodings(rgb, boxes)
                for enc in encodings:
                    known_encodings.append(enc)
                    known_names.append(name)
                    known_usns.append(usn)
                    student_images += 1
                    total_images += 1

            if student_images > 0:
                processed_students += 1

        config.TRAINER_PATH.mkdir(parents=True, exist_ok=True)
        with config.ENCODINGS_FILE.open("wb") as f:
            pickle.dump({
                "encodings": known_encodings,
                "names": known_names,
                "usns": known_usns,
                "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }, f)

        # Reload encodings into current engine
        self.load_encodings()

        return {
            "success": True,
            "message": "Model trained successfully.",
            "total_encodings": len(known_encodings),
            "total_students": processed_students,
            "total_images": total_images
        }


# Global engine singleton
engine = RecognitionEngine()


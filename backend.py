import os
import io
import csv
import base64
import secrets
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from flask import (
    Flask, jsonify, request, abort, render_template, Response,
    send_file, redirect, url_for, session, flash
)

import config
import database
from recognition_engine import engine, recent_events
from camera_manager import camera_manager, test_camera_connection, discover_available_cameras
from rtsp_proxy import proxy as rtsp_proxy

app = Flask(__name__, template_folder="templates")
app.config["JSON_SORT_KEYS"] = False
app.secret_key = secrets.token_hex(32)

# ---- Default admin credentials (change these for production) ----
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"


# ============================================================================
# Authentication
# ============================================================================

def login_required(f):
    """Decorator that redirects to login page if user is not authenticated."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated_function


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("logged_in"):
        return redirect(url_for("dashboard_page"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            session["username"] = username
            session.permanent = True
            return redirect(url_for("dashboard_page"))
        else:
            error = "Invalid username or password. Please try again."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ============================================================================
# Page Routes (all protected)
# ============================================================================

@app.route("/", methods=["GET"])
@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard_page():
    return render_template("dashboard.html")


@app.route("/attendance", methods=["GET"])
@login_required
def attendance_page():
    return render_template("attendance.html")


@app.route("/camera", methods=["GET"])
@login_required
def camera_page():
    return render_template("camera.html")


@app.route("/register", methods=["GET"])
@login_required
def register_page():
    return render_template("register.html")


@app.route("/train", methods=["GET"])
@login_required
def train_page():
    return render_template("train.html")


@app.route("/reports", methods=["GET"])
@login_required
def reports_page():
    return render_template("reports.html")


# ============================================================================
# Live Video Stream & Camera APIs
# ============================================================================

@app.route("/video_feed", methods=["GET"])
@login_required
def video_feed():
    """Real-time multipart MJPEG video stream with face recognition overlay."""
    return Response(
        camera_manager.generate_mjpeg_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/api/camera/frame", methods=["GET"])
@login_required
def camera_frame_api():
    """Returns single frame JPEG for snapshots or legacy clients."""
    frame = camera_manager.get_latest_frame(annotated=True)
    if frame is None:
        abort(503, "Camera frame not available. Verify camera connection.")

    success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not success:
        abort(500, "Unable to encode camera frame")

    return Response(
        encoded.tobytes(),
        mimetype="image/jpeg",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
    )


@app.route("/api/camera/scan_now", methods=["GET", "POST"])
@login_required
def camera_scan_now_api():
    """Triggers an immediate face recognition scan on the current CCTV video frame."""
    result = camera_manager.scan_current_frame()
    return jsonify(result)


@app.route("/api/camera/status", methods=["GET"])
@login_required
def camera_status_api():
    return jsonify(camera_manager.get_status())


@app.route("/api/camera/config", methods=["GET", "POST"])
@login_required
def camera_config_api():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        current = config.load_config()
        current.update(data)

        saved = config.save_config(current)
        if not saved:
            abort(500, "Failed to save configuration")

        # If switching to CCTV mode, reconfigure and restart the SHA-256 proxy
        if current.get("source_type", "cctv") == "cctv":
            channel = current.get("cctv_channel", "101").strip()
            rtsp_proxy.stop()
            rtsp_proxy.configure(
                ip=current.get("cctv_ip", "192.168.1.64"),
                port=int(current.get("cctv_port", 554)),
                username=current.get("cctv_username", "admin"),
                password=current.get("cctv_password", ""),
                rtsp_path=f"/Streaming/Channels/{channel}",
            )
            rtsp_proxy.start()
        else:
            rtsp_proxy.stop()

        # Reload camera to apply changes immediately
        camera_manager.reload()
        return jsonify({"message": "Camera configuration updated and applied.", "config": current})

    return jsonify(config.load_config())


@app.route("/api/camera/test", methods=["POST"])
@login_required
def camera_test_api():
    """Tests connection to a camera with provided parameters without switching active source."""
    data = request.get_json(silent=True) or {}
    test_cfg = config.load_config()
    test_cfg.update(data)
    result = test_camera_connection(test_cfg)
    return jsonify(result)


@app.route("/api/camera/discover", methods=["GET"])
@login_required
def camera_discover_api():
    """Scans local subnet and devices for CCTV cameras and local webcams."""
    results = discover_available_cameras()
    return jsonify(results)


# ============================================================================
# Attendance & Events APIs
# ============================================================================

@app.route("/api/attendance/events", methods=["GET"])
@login_required
def attendance_events_api():
    """Returns recent real-time attendance events for UI toast / notification feed."""
    return jsonify(list(recent_events))


@app.route("/api/attendance", methods=["POST"])
@login_required
def attendance_api():
    """Manual or frame-based attendance marking endpoint."""
    data = request.get_json(silent=True) or {}
    manual_identifier = str(
        data.get("manual_usn", data.get("manual_name", data.get("name", "")))
    ).strip()
    image_data = data.get("image")
    today = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")

    # Manual attendance by USN or Name
    if manual_identifier and not image_data:
        conn = database.get_db_connection()
        try:
            student = conn.execute(
                "SELECT usn, name FROM students WHERE usn = ? COLLATE NOCASE OR name = ? COLLATE NOCASE",
                (manual_identifier, manual_identifier)
            ).fetchone()
        finally:
            conn.close()

        if student is None:
            abort(404, f"Student '{manual_identifier}' not found in registry")

        usn = student["usn"]
        name = student["name"]
        success, msg = database.mark_attendance(usn, name, today, now_time, status="Present", method="MANUAL")

        if success:
            recent_events.appendleft({
                "usn": usn,
                "name": name,
                "time": now_time,
                "date": today,
                "confidence": 100.0,
                "status": "Present",
                "method": "MANUAL",
                "msg": f"Manually marked {name}"
            })

        marked_usns = list(database.get_marked_usns(today))
        return jsonify({
            "results": [{"name": name, "usn": usn, "message": msg, "success": success}],
            "marked": marked_usns
        })

    # Base64 image payload from browser client
    if image_data:
        try:
            if "," in image_data:
                image_data = image_data.split(",", 1)[1]
            img_bytes = base64.b64decode(image_data)
            nparr = np.frombuffer(img_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                abort(400, "Invalid image data format")
        except Exception as err:
            abort(400, f"Error decoding image: {err}")

        annotated, detected = engine.process_frame(
            frame,
            tolerance=config.load_config().get("recognition_tolerance", 0.50),
            scale=0.5,
            auto_mark=True,
            source_label="BROWSER_CAM"
        )
        marked_usns = list(database.get_marked_usns(today))
        return jsonify({
            "results": detected,
            "marked": marked_usns
        })

    abort(400, "Provide either image data or manual_usn")


@app.route("/api/attendance_records", methods=["GET"])
@login_required
def get_attendance_records():
    date_str = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    records = database.get_attendance_records(date_str)
    return jsonify(records)


@app.route("/api/absent_students", methods=["GET"])
@login_required
def get_absent_students_api():
    """Returns list of all enrolled students who are absent on the given date."""
    date_str = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    absent = database.get_absent_students(date_str)
    return jsonify(absent)


@app.route("/api/attendance_records", methods=["DELETE"])
@login_required
def clear_attendance_records():
    date_str = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    success, msg = database.clear_attendance_records(date_str)
    return jsonify({"message": msg, "date": date_str})


@app.route("/api/stats", methods=["GET"])
@login_required
def get_stats():
    date_str = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    stats = database.get_dashboard_stats(date_str)
    return jsonify(stats)


# ============================================================================
# Students & Registration APIs
# ============================================================================

@app.route("/students", methods=["GET"])
@app.route("/api/students", methods=["GET"])
@login_required
def list_students():
    date_str = request.args.get("date")
    students = database.get_all_students(date_str)
    return jsonify(students)


@app.route("/students", methods=["POST"])
@app.route("/api/students", methods=["POST"])
@login_required
def add_student():
    data = request.get_json(silent=True) or {}
    usn = str(data.get("usn", "")).strip()
    name = str(data.get("name", "")).strip()

    if not usn or not name:
        abort(400, "Both 'usn' and 'name' are required")

    database.save_student(usn, name)
    return jsonify({"usn": usn, "name": name, "message": "Student created successfully"}), 201


@app.route("/students/<usn>", methods=["DELETE"])
@login_required
def delete_student(usn):
    if not usn:
        abort(400, "USN is required")

    deleted = database.delete_student(usn)
    if not deleted:
        abort(404, f"Student with USN '{usn}' not found")

    return jsonify({"message": f"Student '{usn}' deleted successfully", "usn": usn})


@app.route("/api/register", methods=["POST"])
@login_required
def register_student_with_images():
    """Registers student and saves 5+ captured face training images."""
    data = request.get_json(silent=True) or {}
    usn = str(data.get("usn", "")).strip()
    name = str(data.get("name", "")).strip()
    images = data.get("images", [])

    if not usn or not name:
        abort(400, "USN and Name are required")

    if not isinstance(images, list) or len(images) < 3:
        abort(400, "Please provide at least 3 face images for enrollment")

    database.save_student(usn, name)

    # Save images to dataset/<usn>/
    student_folder = config.DATASET_PATH / usn
    student_folder.mkdir(parents=True, exist_ok=True)

    saved_count = 0
    for idx, img_b64 in enumerate(images, start=1):
        try:
            if "," in img_b64:
                img_b64 = img_b64.split(",", 1)[1]
            raw_bytes = base64.b64decode(img_b64)
            img_path = student_folder / f"{idx}.jpg"
            with img_path.open("wb") as f:
                f.write(raw_bytes)
            saved_count += 1
        except Exception as err:
            print(f"[Register] Error saving image {idx} for {usn}: {err}")

    # Retrain model automatically after enrollment
    train_result = engine.train()

    return jsonify({
        "message": f"Student '{name}' registered with {saved_count} face samples.",
        "usn": usn,
        "name": name,
        "images_saved": saved_count,
        "training": train_result
    }), 201


# ============================================================================
# Training & Export APIs
# ============================================================================

@app.route("/api/train", methods=["POST"])
@login_required
def train_api():
    result = engine.train()
    return jsonify(result)


@app.route("/api/train/status", methods=["GET"])
@login_required
def train_status_api():
    encodings_count = len(engine.known_encodings)
    unique_students = len(set(engine.known_usns))
    last_mod = None
    if config.ENCODINGS_FILE.exists():
        ts = config.ENCODINGS_FILE.stat().st_mtime
        last_mod = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    return jsonify({
        "encodings_count": encodings_count,
        "enrolled_students": unique_students,
        "last_trained": last_mod,
        "model_file_exists": config.ENCODINGS_FILE.exists()
    })


@app.route("/api/export/csv", methods=["GET"])
@login_required
def export_csv_api():
    date_str = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    records = database.get_attendance_records(date_str)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["USN", "Name", "Date", "Time", "Status", "Method"])
    writer.writeheader()
    for r in records:
        writer.writerow({
            "USN": r.get("usn", ""),
            "Name": r.get("name", ""),
            "Date": r.get("date", date_str),
            "Time": r.get("time", ""),
            "Status": r.get("status", "Present"),
            "Method": r.get("method", "FACE")
        })

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=Attendance_{date_str}.csv"}
    )


@app.route("/api/export/absent_csv", methods=["GET"])
@login_required
def export_absent_csv_api():
    date_str = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    records = database.get_absent_students(date_str)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["USN", "Name", "Date", "Status"])
    writer.writeheader()
    for r in records:
        writer.writerow({
            "USN": r.get("usn", ""),
            "Name": r.get("name", ""),
            "Date": r.get("date", date_str),
            "Status": "Absent"
        })

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=Absent_Students_{date_str}.csv"}
    )


@app.route("/api/attendance/summary", methods=["GET"])
@login_required
def attendance_summary_api():
    """Returns overall total number of classes and attendance breakdown for every student."""
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    summary = database.get_student_attendance_summary(date_from=date_from, date_to=date_to)
    return jsonify(summary)


@app.route("/api/attendance/student/<path:usn>", methods=["GET"])
@login_required
def attendance_student_detail_api(usn):
    """Returns day-by-day attendance history and class breakdown for a specific student."""
    detail = database.get_student_attendance_detail(usn)
    if not detail:
        abort(404, f"Student '{usn}' not found")
    return jsonify(detail)


@app.route("/api/export/student_summary_csv", methods=["GET"])
@login_required
def export_student_summary_csv_api():
    """Exports full student-wise attendance sheet with total and present classes."""
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    summary = database.get_student_attendance_summary(date_from=date_from, date_to=date_to)

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "USN",
            "Student Name",
            "Total Classes Held",
            "Present Classes",
            "Absent Classes",
            "Attendance Percentage (%)",
            "Status"
        ]
    )
    writer.writeheader()
    for s in summary.get("students", []):
        writer.writerow({
            "USN": s.get("usn", ""),
            "Student Name": s.get("name", ""),
            "Total Classes Held": s.get("total_classes", 0),
            "Present Classes": s.get("present_classes", 0),
            "Absent Classes": s.get("absent_classes", 0),
            "Attendance Percentage (%)": f"{s.get('attendance_percentage', 0.0):.1f}%",
            "Status": s.get("status_badge", "Unknown")
        })

    output.seek(0)
    today_str = datetime.now().strftime("%Y-%m-%d")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=Student_Attendance_Summary_{today_str}.csv"}
    )


# ============================================================================
# Health Check
# ============================================================================

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "camera_connected": camera_manager.is_connected,
        "source": camera_manager.active_source_description
    })


# ============================================================================
# App Initialization & Startup
# ============================================================================

def startup():
    print("=" * 60)
    print("   SMART CLASSROOM ATTENDANCE & CCTV MONITORING SYSTEM")
    print("=" * 60)
    database.init_db()
    engine.load_encodings()

    # Start RTSP SHA-256 proxy before camera_manager so OpenCV can connect
    cfg = config.load_config()
    if cfg.get("source_type", "cctv") == "cctv":
        channel = cfg.get("cctv_channel", "101").strip()
        rtsp_path = f"/Streaming/Channels/{channel}"
        rtsp_proxy.configure(
            ip=cfg.get("cctv_ip", "192.168.1.64"),
            port=int(cfg.get("cctv_port", 554)),
            username=cfg.get("cctv_username", "admin"),
            password=cfg.get("cctv_password", ""),
            rtsp_path=rtsp_path,
        )
        rtsp_proxy.start()

    camera_manager.start()
    print("[System] Ready on http://127.0.0.1:5000")
    print("[System] Login with username: admin / password: admin123")


if __name__ == "__main__":
    startup()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

# Smart Classroom Attendance & CCTV Surveillance AI System

An enterprise-grade, real-time automated classroom attendance monitoring system powered by Computer Vision and Deep Biometric Facial Recognition. Designed for seamless integration with Ethernet/IP CCTV cameras (such as Hikvision, Dahua, CP Plus) and standard USB webcams.

---

## 🌟 Key Features

- **Scan-On-Demand Button:** One-click "Scan Student (CCTV)" button and keyboard shortcut (<kbd>Spacebar</kbd>) triggers instant high-precision facial recognition without lagging the continuous 30 FPS video feed.
- **Local SHA-256 RTSP Proxy:** Built-in proxy (`rtsp_proxy.py`) handles SHA-256 Digest Authentication for modern Prama/Hikvision OEM cameras, bridging OpenCV's standard FFmpeg backend seamlessly.
- **Secure Authentication:** Session-based administrative login system protects all dashboard pages and attendance control APIs (`admin` / `admin123`).
- **Automated CCTV Attendance:** Option for continuous background scanning or on-demand instant verification.
- **Zero-Latency Video Pipeline:** Dedicated background frame reader thread with RTSP buffer draining prevents OpenCV stream lag.
- **Real-Time Facial Recognition Overlay:**
  - High-precision 128-dimensional biometric embeddings using dlib and Face Recognition models.
  - Green bounding box with Student Name, USN, and Match Confidence (%) for registered students.
  - Red bounding box for unrecognized visitors or unregistered individuals.
- **Smart Cooldown & Duplicate Prevention:** Configurable attendance cooldown timer prevents duplicate database records while a student remains in camera view.
- **CCTV & Camera Setup Center:**
  - Auto-discovery scanner detects Ethernet IP cameras and local webcams on the network.
  - Built-in connection tester with immediate diagnostics (RTSP status, 401 Unauthorized detection, FPS & resolution reporting).
  - Stream presets for Hikvision (`/Streaming/Channels/102` Sub-Stream and `101` Main HD), Dahua, and custom RTSP streams.
- **Unified Modern Web Dashboard:**
  - Executive KPI Cards: Total Students, Present Today, Absent Students, Classroom Turnout Rate (%).
  - Live CCTV Monitor with Real-Time Activity Feed and Audio Confirmation Chimes (Web Audio API & Text-to-Speech).
  - Student Enrollment Studio with auto 5-burst capture and facial guide framing.
  - One-Click Model Retraining with live compilation console.
  - Historical Attendance Archives & Reports with instant CSV export and print-ready sheets.
- **Hybrid Dual-Store Architecture:** Synchronizes attendance records simultaneously across SQLite database (`attendance.db`), daily CSV registers (`attendance/Attendance_YYYY-MM-DD.csv`), and optional Supabase cloud storage.

---

## 🏗 Architecture Overview

```
                      ┌──────────────────────────────────────┐
                      │    Ethernet CCTV / RTSP Stream       │
                      │       (e.g. 192.168.1.64:554)        │
                      └──────────────────┬───────────────────┘
                                         │
                                         ▼
                      ┌──────────────────────────────────────┐
                      │   CameraManager (Background Thread)  │
                      │    - Buffer Lag Mitigation           │
                      │    - Auto-reconnect & Health Check   │
                      └──────────────────┬───────────────────┘
                                         │
                                         ▼
                      ┌──────────────────────────────────────┐
                      │ RecognitionEngine (Biometric Model)  │
                      │    - 128D Face Encoding Matching     │
                      │    - Cooldown & Duplicate Protection │
                      └──────────────────┬───────────────────┘
                                         │
                   ┌─────────────────────┴─────────────────────┐
                   ▼                                           ▼
┌──────────────────────────────────────┐   ┌──────────────────────────────────────┐
│        Storage & Persistence         │   │        Modern Web Interface          │
│   - SQLite DB (attendance.db)        │   │   - Live Video Monitor (/attendance) │
│   - Daily CSV Files (/attendance/)   │   │   - Executive Dashboard (/dashboard) │
│   - Optional Supabase Cloud Sync     │   │   - CCTV Setup Center (/camera)      │
└──────────────────────────────────────┘   └──────────────────────────────────────┘
```

---

## 📷 Connecting to Your CCTV Camera

### Auto-Detected CCTV (192.168.1.64)
The system has detected an active **Hikvision IP CCTV Camera** connected directly to the Ethernet network interface:
- **IP Address:** `192.168.1.64`
- **RTSP Port:** `554` (Open and active)
- **Web Console:** `https://192.168.1.64/`
- **Default Authentication Realm:** Digest (`IP Camera(GT952)`)

### Steps to Connect:
1. Start the system by running `start_backend.bat` or opening `http://127.0.0.1:5000`.
2. In the navigation bar, click **CCTV Settings** (or go to `http://127.0.0.1:5000/camera`).
3. Under **Stream Configuration**, verify:
   - **IP Address:** `192.168.1.64`
   - **Port:** `554`
   - **Username:** `admin`
   - **Password:** Enter your CCTV camera password (set during camera initialization).
   - **Channel Preset:** `Hikvision Sub-Stream (/Streaming/Channels/102)` *(Recommended for optimal AI speed)*.
4. Click **Test Connection**. The system verifies the RTSP stream and displays resolution and FPS.
5. Click **Save & Apply**. The live feed and automated recognition activate immediately across the entire system.

---

## 📁 Project Directory Structure

```
SMART ATTENDANCE PROJECT/
├── dataset/                      # Enrolled face images by USN (e.g. dataset/4KV23EC011/1.jpg)
├── trainer/                      # Trained biometric embeddings (encodings.pickle)
├── attendance/                   # Historical CSV records (Attendance_YYYY-MM-DD.csv)
├── templates/                    # Web Interface Templates
│   ├── dashboard.html            # Main dashboard with KPI cards and activity feed
│   ├── attendance.html           # Live CCTV monitor with real-time detection alerts
│   ├── camera.html               # CCTV & camera configuration and testing center
│   ├── register.html             # Student enrollment and face capture studio
│   ├── train.html                # Model retraining console
│   └── reports.html              # Historical attendance reports & CSV export
├── backend.py                    # Flask application and REST API controllers
├── camera_manager.py             # Multi-threaded RTSP stream grabber & discovery scanner
├── recognition_engine.py         # Face detection, 128D encoding, and auto-attendance
├── database.py                   # SQLite database operations & CSV synchronization
├── config.py                     # Centralized settings and URL builders
├── camera_config.json            # Persistent camera and recognition settings
├── attendance.py                 # Desktop OpenCV monitor CLI
├── register_student.py           # CLI student registration tool
├── train_faces.py                # CLI model training script
├── students.csv                  # Student registry flat-file
├── attendance.db                 # SQLite relational database
├── requirements.txt              # Python package dependencies
├── start_backend.bat             # One-click Windows startup script
├── launch_backend.ps1            # PowerShell launcher
└── README.md                     # Documentation
```

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- **Operating System:** Windows 10/11 (or Linux)
- **Python Version:** Python 3.10 or 3.11
- **Hardware:** Webcam or Network CCTV Camera (Hikvision, Dahua, ONVIF)

### 2. Launch the Application
Simply double-click `start_backend.bat` or run in PowerShell:
```powershell
.\launch_backend.ps1
```
The server starts in the background and opens the dashboard in your default browser at `http://127.0.0.1:5000`.

### 3. Alternative CLI Commands
You can also run individual CLI utilities:
```powershell
# Desktop camera monitor window
.\venv\Scripts\python.exe attendance.py

# CLI student enrollment
.\venv\Scripts\python.exe register_student.py

# CLI model retrain
.\venv\Scripts\python.exe train_faces.py
```

---

## 📡 REST API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/video_feed` | `GET` | MJPEG multipart video stream with face recognition overlay |
| `/api/camera/status` | `GET` | Active camera health, FPS, and detected face count |
| `/api/camera/config` | `GET / POST` | Retrieve or update camera settings (`camera_config.json`) |
| `/api/camera/test` | `POST` | Test camera connection with diagnostics |
| `/api/camera/discover` | `GET` | Scan local subnet for IP cameras and local webcams |
| `/api/attendance/events`| `GET` | Real-time queue of marked attendance events for live UI notifications |
| `/api/attendance_records`| `GET / DELETE` | Query or clear attendance records by date |
| `/api/stats` | `GET` | KPI metrics (Total, Present, Absent, Rate) for a given date |
| `/api/students` | `GET / POST` | List or create student records |
| `/students/<usn>` | `DELETE` | Remove student and purge their training images |
| `/api/register` | `POST` | Register student with base64 face images and auto-retrain |
| `/api/train` | `POST` | Trigger face model retraining |
| `/api/export/csv` | `GET` | Download attendance records for a date as a formatted CSV |
| `/health` | `GET` | System health check |

---

## 🛠 Troubleshooting & Tips

- **401 Unauthorized Error on CCTV:**
  - Verify your password by logging into the camera's web portal at `https://192.168.1.64/`.
  - In Hikvision cameras, ensure the user account has `Media Stream / RTSP` access permissions enabled in **Configuration > System > Security > User Management**.
- **CCTV Video Lag / Latency:**
  - Always use the **Sub-Stream** (`102`) preset for real-time video processing. High-resolution 4K streams take longer to decode on CPU and can introduce delay.
- **Webcam Access Issues:**
  - Close other software (Zoom, Teams, Skype, Windows Camera app) that might hold exclusive locks on the webcam.

---

## 🚀 GitHub Deployment Guide

Follow these steps to deploy this repository to GitHub:

### 1. Create a New Repository on GitHub
1. Go to [github.com/new](https://github.com/new).
2. Enter a repository name (e.g. `smart-attendance-cctv`).
3. Leave **"Initialize this repository with a README"** **UNCHECKED** (we already have a complete README).
4. Click **Create repository**.

### 2. Push Your Code to GitHub
Open PowerShell in this project folder and run:

```powershell
# Set your Git identity (if not already set)
git config --global user.name "Your Name"
git config --global user.email "your-email@example.com"

# Initialize repository and commit files
git init
git branch -M main
git add .
git commit -m "feat: smart classroom attendance system with cctv integration"

# Link your remote GitHub repository and push
git remote add origin https://github.com/<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>.git
git push -u origin main
```

---

## 📄 License
This project is licensed under the MIT License.

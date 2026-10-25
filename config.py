import os
import json
from pathlib import Path
import urllib.parse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "attendance.db"
DATASET_PATH = BASE_DIR / "dataset"
TRAINER_PATH = BASE_DIR / "trainer"
ENCODINGS_FILE = TRAINER_PATH / "encodings.pickle"
ATTENDANCE_FOLDER = BASE_DIR / "attendance"
CONFIG_FILE = BASE_DIR / "camera_config.json"
STUDENTS_CSV = BASE_DIR / "students.csv"

# Supabase optional cloud sync
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_KEY)

DEFAULT_CONFIG = {
    "source_type": "cctv",            # "cctv" or "webcam"
    "cctv_ip": "192.168.1.64",        # Pre-configured to detected Hikvision CCTV
    "cctv_port": 554,
    "cctv_username": "admin",
    "cctv_password": "",
    "cctv_channel": "102",            # 102 (Sub-stream recommended for low latency) or 101 (Main)
    "cctv_preset": "hikvision",       # "hikvision", "dahua", "generic", "custom"
    "custom_rtsp_url": "",
    "webcam_index": 0,
    "scan_mode": "manual",            # "manual" (Scan on button click) or "continuous" (Auto background scan)
    "auto_mark_enabled": True,        # Automatically mark attendance on CCTV stream
    "recognition_tolerance": 0.48,     # 0.48 is ideal balance between precision and recall
    "cooldown_seconds": 60,           # Avoid repeated logs within 60s
    "process_every_n_frames": 2,      # Process face recognition every 2 frames for silky smooth stream
    "play_sound": True
}


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as f:
                saved = json.load(f)
                config.update(saved)
        except Exception as err:
            print(f"[Config] Error reading camera_config.json: {err}")
    
    # Allow overriding via environment variable CAMERA_SOURCE if explicitly set
    env_camera = os.getenv("CAMERA_SOURCE", "").strip()
    if env_camera:
        if env_camera.isdigit():
            config["source_type"] = "webcam"
            config["webcam_index"] = int(env_camera)
        else:
            config["source_type"] = "cctv"
            config["custom_rtsp_url"] = env_camera

    return config


def save_config(config: dict) -> bool:
    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        return True
    except Exception as err:
        print(f"[Config] Error saving config: {err}")
        return False


def build_rtsp_url(config: dict) -> str:
    """Builds a formatted RTSP URL from configuration settings."""
    custom_url = config.get("custom_rtsp_url", "").strip()
    if custom_url:
        return custom_url

    ip = config.get("cctv_ip", "192.168.1.64").strip()
    port = config.get("cctv_port", 554)
    username = config.get("cctv_username", "admin").strip()
    password = config.get("cctv_password", "")
    preset = config.get("cctv_preset", "hikvision")
    channel = config.get("cctv_channel", "102").strip()

    # URL-encode credentials if needed
    auth_prefix = ""
    if username or password:
        quoted_user = urllib.parse.quote(username, safe="")
        quoted_pass = urllib.parse.quote(password, safe="")
        auth_prefix = f"{quoted_user}:{quoted_pass}@"

    if preset == "hikvision":
        # /Streaming/Channels/101 (Main) or 102 (Sub)
        return f"rtsp://{auth_prefix}{ip}:{port}/Streaming/Channels/{channel}"
    elif preset == "dahua":
        # Dahua subtype: 0 = Main stream, 1 = Sub stream
        subtype = "1" if channel in ("102", "sub", "1") else "0"
        return f"rtsp://{auth_prefix}{ip}:{port}/cam/realmonitor?channel=1&subtype={subtype}"
    elif preset == "generic":
        return f"rtsp://{auth_prefix}{ip}:{port}/live/ch0"
    else:
        return f"rtsp://{auth_prefix}{ip}:{port}/Streaming/Channels/{channel}"


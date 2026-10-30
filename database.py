import csv
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set

import config

supabase_client = None


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_supabase():
    global supabase_client
    if not config.SUPABASE_ENABLED:
        return
    try:
        from supabase import create_client
        supabase_client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        print("[Database] Supabase cloud sync enabled.")
    except Exception as exc:
        print(f"[Database] Supabase init warning: {exc}")
        supabase_client = None


def sync_student_to_supabase(usn: str, name: str):
    if not config.SUPABASE_ENABLED or supabase_client is None:
        return
    try:
        supabase_client.table("students").upsert({"usn": usn, "name": name}).execute()
    except Exception as exc:
        print(f"[Supabase] Warning: failed to sync student {usn}: {exc}")


def sync_attendance_to_supabase(usn: str, name: str, date_str: str, time_str: str, status: str):
    if not config.SUPABASE_ENABLED or supabase_client is None:
        return
    try:
        supabase_client.table("attendance").upsert({
            "usn": usn,
            "name": name,
            "date": date_str,
            "time": time_str,
            "status": status,
        }).execute()
    except Exception as exc:
        print(f"[Supabase] Warning: failed to sync attendance for {usn}: {exc}")


def load_students_from_csv() -> List[Dict[str, str]]:
    students = []
    if not config.STUDENTS_CSV.exists():
        return students

    with config.STUDENTS_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            usn = str(row.get("USN", "")).strip()
            name = str(row.get("Name", "")).strip()
            if usn and name:
                students.append({"usn": usn, "name": name})
    return students


def save_student_to_csv(usn: str, name: str):
    students = load_students_from_csv()
    exists = False
    for s in students:
        if s["usn"].lower() == usn.lower():
            s["name"] = name
            exists = True
            break
    if not exists:
        students.append({"usn": usn, "name": name})

    with config.STUDENTS_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["USN", "Name"])
        writer.writeheader()
        for s in students:
            writer.writerow({"USN": s["usn"], "Name": s["name"]})


def remove_student_from_csv(usn: str) -> bool:
    if not config.STUDENTS_CSV.exists():
        return False

    students = load_students_from_csv()
    filtered = [s for s in students if s["usn"].lower() != usn.lower()]
    if len(filtered) != len(students):
        with config.STUDENTS_CSV.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["USN", "Name"])
            writer.writeheader()
            for s in filtered:
                writer.writerow({"USN": s["usn"], "Name": s["name"]})
        return True
    return False


def get_attendance_csv_path(date_str: str) -> Path:
    config.ATTENDANCE_FOLDER.mkdir(parents=True, exist_ok=True)
    return config.ATTENDANCE_FOLDER / f"Attendance_{date_str}.csv"


def import_csv_attendance_to_db():
    """Migrate historical CSV records into SQLite attendance table."""
    conn = get_db_connection()
    try:
        for csv_file in config.ATTENDANCE_FOLDER.glob("Attendance_*.csv"):
            date_part = csv_file.stem.replace("Attendance_", "")
            with csv_file.open("r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    usn = str(row.get("USN", "")).strip()
                    name = str(row.get("Name", "")).strip()
                    r_date = str(row.get("Date", date_part)).strip()
                    r_time = str(row.get("Time", "")).strip()
                    status = str(row.get("Status", "Present")).strip()
                    if usn and r_date:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO attendance (usn, name, date, time, status, method)
                            VALUES (?, ?, ?, ?, ?, 'MIGRATED')
                            """,
                            (usn, name or usn, r_date, r_time or "00:00:00", status or "Present")
                        )
        conn.commit()
    except Exception as err:
        print(f"[Database] CSV import warning: {err}")
    finally:
        conn.close()


def init_db():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.TRAINER_PATH.mkdir(parents=True, exist_ok=True)
    config.DATASET_PATH.mkdir(parents=True, exist_ok=True)
    config.ATTENDANCE_FOLDER.mkdir(parents=True, exist_ok=True)

    conn = get_db_connection()
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            usn TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usn TEXT NOT NULL,
            name TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            status TEXT NOT NULL,
            method TEXT DEFAULT 'FACE',
            UNIQUE(usn, date),
            FOREIGN KEY(usn) REFERENCES students(usn) ON DELETE CASCADE
        )
        """
    )
    # Check if method column exists in existing attendance table
    try:
        cur = conn.execute("PRAGMA table_info(attendance);")
        columns = [row[1] for row in cur.fetchall()]
        if "method" not in columns:
            conn.execute("ALTER TABLE attendance ADD COLUMN method TEXT DEFAULT 'FACE';")
            conn.commit()
    except Exception as err:
        print(f"[Database] Migration check: {err}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(date);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attendance_usn ON attendance(usn);")
    conn.commit()
    conn.close()

    # Sync students from CSV
    csv_students = load_students_from_csv()
    if csv_students:
        conn = get_db_connection()
        for s in csv_students:
            conn.execute(
                "INSERT OR IGNORE INTO students (usn, name) VALUES (?, ?)",
                (s["usn"], s["name"])
            )
        conn.commit()
        conn.close()

    # Migrate historical CSVs into SQLite
    import_csv_attendance_to_db()

    if config.SUPABASE_ENABLED:
        init_supabase()


def resolve_student_name(usn: str, fallback_name: str = "") -> str:
    if not usn:
        return fallback_name
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT name FROM students WHERE usn = ? COLLATE NOCASE", (usn,)).fetchone()
        if row and row["name"]:
            return row["name"]
    finally:
        conn.close()
    return fallback_name or usn


def get_all_students(date_str: Optional[str] = None) -> List[Dict]:
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    marked_usns = get_marked_usns(date_str)

    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT usn, name FROM students ORDER BY usn ASC").fetchall()
        students = []
        for r in rows:
            u = r["usn"]
            students.append({
                "usn": u,
                "name": r["name"],
                "marked": u in marked_usns
            })
        return students
    finally:
        conn.close()


def save_student(usn: str, name: str) -> bool:
    usn = usn.strip()
    name = name.strip()
    if not usn or not name:
        return False

    conn = get_db_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO students (usn, name) VALUES (?, ?)", (usn, name))
        conn.commit()
    finally:
        conn.close()

    save_student_to_csv(usn, name)
    sync_student_to_supabase(usn, name)
    return True


def delete_student(usn: str) -> bool:
    usn = usn.strip()
    if not usn:
        return False

    conn = get_db_connection()
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.execute("DELETE FROM students WHERE usn = ?", (usn,))
        deleted_count = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    remove_student_from_csv(usn)

    # Delete dataset folder
    student_folder = config.DATASET_PATH / usn
    if student_folder.exists() and student_folder.is_dir():
        shutil.rmtree(student_folder, ignore_errors=True)

    return deleted_count > 0


def get_marked_usns(date_str: str) -> Set[str]:
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT usn FROM attendance WHERE date = ?", (date_str,)).fetchall()
        marked = {r["usn"] for r in rows}
    finally:
        conn.close()

    # Also check today's CSV in case written directly
    csv_path = get_attendance_csv_path(date_str)
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                u = row.get("USN", "").strip()
                if u:
                    marked.add(u)
    return marked


def mark_attendance(usn: str, name: str, date_str: Optional[str] = None, time_str: Optional[str] = None, status: str = "Present", method: str = "FACE") -> Tuple[bool, str]:
    now = datetime.now()
    if not date_str:
        date_str = now.strftime("%Y-%m-%d")
    if not time_str:
        time_str = now.strftime("%H:%M:%S")

    resolved_name = resolve_student_name(usn, name)

    # 1. Check if already marked
    marked_set = get_marked_usns(date_str)
    if usn in marked_set:
        return False, f"Attendance already marked for {resolved_name} today"

    # 2. Insert into SQLite
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO attendance (usn, name, date, time, status, method)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (usn, resolved_name, date_str, time_str, status, method)
        )
        conn.commit()
    except Exception as err:
        print(f"[Database] Error marking attendance: {err}")
    finally:
        conn.close()

    # 3. Write to CSV
    csv_path = get_attendance_csv_path(date_str)
    is_new = not csv_path.exists()
    try:
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["USN", "Name", "Date", "Time", "Status"])
            if is_new:
                writer.writeheader()
            writer.writerow({
                "USN": usn,
                "Name": resolved_name,
                "Date": date_str,
                "Time": time_str,
                "Status": status
            })
    except Exception as err:
        print(f"[Database] CSV write warning: {err}")

    # 4. Optional Supabase sync
    sync_attendance_to_supabase(usn, resolved_name, date_str, time_str, status)

    return True, f"Attendance successfully marked for {resolved_name}"


def get_absent_students(date_str: Optional[str] = None) -> List[Dict]:
    """Returns list of enrolled students who have not yet been marked present for the given date."""
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    marked_usns = get_marked_usns(date_str)
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT usn, name FROM students ORDER BY usn ASC").fetchall()
        absent = []
        for r in rows:
            u = r["usn"]
            if u not in marked_usns:
                absent.append({
                    "usn": u,
                    "name": r["name"],
                    "status": "Absent",
                    "date": date_str
                })
        return absent
    finally:
        conn.close()


def get_attendance_records(date_str: Optional[str] = None) -> List[Dict]:
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT usn, name, date, time, status, method 
            FROM attendance 
            WHERE date = ? 
            ORDER BY time DESC
            """,
            (date_str,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def clear_attendance_records(date_str: Optional[str] = None) -> Tuple[bool, str]:
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM attendance WHERE date = ?", (date_str,))
        conn.commit()
    finally:
        conn.close()

    csv_path = get_attendance_csv_path(date_str)
    if csv_path.exists():
        try:
            csv_path.unlink()
        except Exception as err:
            print(f"[Database] Warning unlinking CSV: {err}")

    return True, f"Attendance records for {date_str} cleared successfully."


def get_dashboard_stats(date_str: Optional[str] = None) -> Dict:
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    conn = get_db_connection()
    try:
        total_students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        present_count = conn.execute(
            "SELECT COUNT(DISTINCT usn) FROM attendance WHERE date = ? AND LOWER(status) = 'present'",
            (date_str,)
        ).fetchone()[0]
        total_classes = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM attendance"
        ).fetchone()[0]
    finally:
        conn.close()

    absent_count = max(0, total_students - present_count)
    rate = round((present_count / total_students * 100), 1) if total_students > 0 else 0.0

    return {
        "date": date_str,
        "total_students": total_students,
        "present_count": present_count,
        "absent_count": absent_count,
        "attendance_rate": rate,
        "total_classes": total_classes,
    }


def get_student_attendance_summary(date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict:
    """Calculates overall total number of classes and attendance metrics for each student separately."""
    conn = get_db_connection()
    try:
        # Determine unique class dates
        date_query = "SELECT DISTINCT date FROM attendance"
        params: List[str] = []
        conditions = []
        if date_from:
            conditions.append("date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("date <= ?")
            params.append(date_to)
        if conditions:
            date_query += " WHERE " + " AND ".join(conditions)
        date_query += " ORDER BY date ASC"

        class_dates = [r[0] for r in conn.execute(date_query, tuple(params)).fetchall()]
        total_classes = len(class_dates)

        # Get all distinct enrolled students (combine students table with historical attendance)
        students_rows = conn.execute("""
            SELECT DISTINCT usn, name FROM (
                SELECT usn, name FROM students
                UNION
                SELECT usn, name FROM attendance WHERE usn IS NOT NULL AND TRIM(usn) != ''
            ) ORDER BY usn ASC
        """).fetchall()

        student_list = []
        for s in students_rows:
            u = s["usn"]
            # Look up clean student name
            resolved_name = resolve_student_name(u, s["name"] or u)

            # Query number of present classes for this student
            pq = "SELECT COUNT(DISTINCT date) FROM attendance WHERE usn = ? AND LOWER(status) = 'present'"
            pparams: List[str] = [u]
            if date_from:
                pq += " AND date >= ?"
                pparams.append(date_from)
            if date_to:
                pq += " AND date <= ?"
                pparams.append(date_to)

            present_count = conn.execute(pq, tuple(pparams)).fetchone()[0]
            absent_count = max(0, total_classes - present_count)
            pct = round((present_count / total_classes * 100), 1) if total_classes > 0 else 0.0

            if pct >= 75.0:
                badge = "Safe"
            elif pct >= 50.0:
                badge = "Shortage"
            else:
                badge = "Critical"

            student_list.append({
                "usn": u,
                "name": resolved_name,
                "total_classes": total_classes,
                "present_classes": present_count,
                "absent_classes": absent_count,
                "attendance_percentage": pct,
                "status_badge": badge
            })

        avg_attendance = round(
            sum(s["attendance_percentage"] for s in student_list) / len(student_list), 1
        ) if student_list else 0.0

        shortage_count = sum(1 for s in student_list if s["attendance_percentage"] < 75.0)

        return {
            "total_classes": total_classes,
            "total_students": len(student_list),
            "average_attendance": avg_attendance,
            "shortage_count": shortage_count,
            "class_dates": class_dates,
            "students": student_list
        }
    finally:
        conn.close()


def get_student_attendance_detail(usn: str) -> Optional[Dict]:
    """Retrieves full day-by-day attendance history and class breakdown for a specific student."""
    usn = usn.strip()
    if not usn:
        return None

    conn = get_db_connection()
    try:
        # All class dates
        class_dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM attendance ORDER BY date DESC"
        ).fetchall()]
        total_classes = len(class_dates)

        # Student info
        resolved_name = resolve_student_name(usn, usn)

        # Attended dates map
        records_rows = conn.execute(
            "SELECT date, time, status, method FROM attendance WHERE usn = ? COLLATE NOCASE",
            (usn,)
        ).fetchall()
        attended_map = {r["date"]: dict(r) for r in records_rows}

        history = []
        present_count = 0
        for d in class_dates:
            if d in attended_map:
                rec = attended_map[d]
                is_present = rec.get("status", "Present").lower() == "present"
                if is_present:
                    present_count += 1
                history.append({
                    "date": d,
                    "status": "Present" if is_present else rec.get("status", "Present"),
                    "time": rec.get("time", "--:--:--"),
                    "method": rec.get("method", "FACE")
                })
            else:
                history.append({
                    "date": d,
                    "status": "Absent",
                    "time": "--:--:--",
                    "method": "Not Detected"
                })

        absent_count = max(0, total_classes - present_count)
        pct = round((present_count / total_classes * 100), 1) if total_classes > 0 else 0.0

        return {
            "usn": usn,
            "name": resolved_name,
            "total_classes": total_classes,
            "present_classes": present_count,
            "absent_classes": absent_count,
            "attendance_percentage": pct,
            "history": history
        }
    finally:
        conn.close()

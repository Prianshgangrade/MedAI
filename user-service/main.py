import os
import time
import sqlite3
import logging
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import mysql.connector
from mysql.connector import Error as MySQLError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("user-service")

LOCAL_MODE = os.getenv("LOCAL_MODE", "false").lower() == "true"
LOCAL_DB_PATH = os.getenv("LOCAL_DB_PATH", "medpulse_users_local.db")

MYSQL_HOST = os.getenv("MYSQL_HOST", "mysql")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "rootpass")
MYSQL_DB = os.getenv("MYSQL_DB", "medpulse_db")

app = FastAPI(
    title="MedPulse AI - User Service",
    description="Manages Doctors, Patients, and Appointments in MySQL.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class UserCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    email: str = Field(..., min_length=5, max_length=150)
    role: str = Field(..., pattern="^(Doctor|Patient)$")
    specialization: Optional[str] = Field(default="", max_length=120)
    phone: Optional[str] = Field(default="", max_length=30)


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    specialization: str
    phone: str
    created_at: str


class AppointmentCreate(BaseModel):
    patient_id: int = Field(..., gt=0)
    doctor_id: int = Field(..., gt=0)
    appointment_date: str = Field(..., min_length=4, max_length=50)
    symptoms: str = Field(default="", max_length=1000)
    status: str = Field(default="Scheduled", max_length=40)


class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    doctor_id: int
    patient_name: str
    doctor_name: str
    doctor_specialization: str
    appointment_date: str
    symptoms: str
    status: str
    created_at: str


SEED_USERS = [
    (
        "Dr. Aarav Sharma",
        "aarav.sharma@medpulse.in",
        "Doctor",
        "Infectious Diseases & Internal Medicine",
        "+91-9810011223",
    ),
    (
        "Dr. Priya Nair",
        "priya.nair@medpulse.in",
        "Doctor",
        "Cardiology & Public Health",
        "+91-9820044556",
    ),
    (
        "Rohan Verma",
        "rohan.verma@patient.in",
        "Patient",
        "",
        "+91-9830077889",
    ),
]


def get_sqlite_connection():
    conn = sqlite3.connect(LOCAL_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_sqlite() -> None:
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL CHECK(role IN ('Doctor', 'Patient')),
            specialization TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER NOT NULL,
            appointment_date TEXT NOT NULL,
            symptoms TEXT,
            status TEXT DEFAULT 'Scheduled',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (doctor_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    user_count = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count == 0:
        cur.executemany(
            "INSERT INTO users (name, email, role, specialization, phone) VALUES (?, ?, ?, ?, ?)",
            SEED_USERS,
        )
        conn.commit()
        logger.info("Seeded 3 sample doctors/patients into local SQLite.")

    appt_count = cur.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    if appt_count == 0:
        patient_row = cur.execute("SELECT id FROM users WHERE role = 'Patient' LIMIT 1").fetchone()
        doctor_row = cur.execute("SELECT id FROM users WHERE role = 'Doctor' LIMIT 1").fetchone()
        if patient_row and doctor_row:
            cur.execute(
                "INSERT INTO appointments (patient_id, doctor_id, appointment_date, symptoms, status) VALUES (?, ?, ?, ?, ?)",
                (
                    patient_row[0],
                    doctor_row[0],
                    "2026-11-15 10:30",
                    "High fever for 3 days, retro-orbital pain, joint ache",
                    "Scheduled",
                ),
            )
            conn.commit()
    conn.close()
    logger.info(f"Local SQLite initialization complete ({LOCAL_DB_PATH}).")


def get_connection(use_db: bool = True):
    kwargs = {
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "user": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "connection_timeout": 10,
    }
    if use_db:
        kwargs["database"] = MYSQL_DB
    return mysql.connector.connect(**kwargs)


def init_db_with_retry(max_retries: int = 20, delay_seconds: int = 3) -> None:
    if LOCAL_MODE:
        init_sqlite()
        return

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            conn = get_connection(use_db=False)
            cursor = conn.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DB}`")
            cursor.execute(f"USE `{MYSQL_DB}`")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(120) NOT NULL,
                    email VARCHAR(150) NOT NULL UNIQUE,
                    role ENUM('Doctor', 'Patient') NOT NULL,
                    specialization VARCHAR(120) DEFAULT '',
                    phone VARCHAR(30) DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS appointments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    patient_id INT NOT NULL,
                    doctor_id INT NOT NULL,
                    appointment_date VARCHAR(50) NOT NULL,
                    symptoms TEXT,
                    status VARCHAR(40) DEFAULT 'Scheduled',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT fk_patient FOREIGN KEY (patient_id) REFERENCES users(id) ON DELETE CASCADE,
                    CONSTRAINT fk_doctor FOREIGN KEY (doctor_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )

            cursor.execute("SELECT COUNT(*) FROM users")
            user_count = cursor.fetchone()[0]
            if user_count == 0:
                cursor.executemany(
                    """
                    INSERT INTO users (name, email, role, specialization, phone)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    SEED_USERS,
                )
                conn.commit()
                logger.info("Seeded 3 sample doctors/patients into MySQL.")

            cursor.execute("SELECT COUNT(*) FROM appointments")
            appt_count = cursor.fetchone()[0]
            if appt_count == 0:
                cursor.execute("SELECT id FROM users WHERE role = 'Patient' LIMIT 1")
                patient_row = cursor.fetchone()
                cursor.execute("SELECT id FROM users WHERE role = 'Doctor' LIMIT 1")
                doctor_row = cursor.fetchone()
                if patient_row and doctor_row:
                    cursor.execute(
                        """
                        INSERT INTO appointments (patient_id, doctor_id, appointment_date, symptoms, status)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            patient_row[0],
                            doctor_row[0],
                            "2026-11-15 10:30",
                            "High fever for 3 days, retro-orbital pain, joint ache",
                            "Scheduled",
                        ),
                    )
                    conn.commit()

            cursor.close()
            conn.close()
            logger.info("MySQL initialization complete.")
            return
        except MySQLError as exc:
            last_error = exc
            logger.warning(
                f"MySQL connection attempt {attempt}/{max_retries} failed: {exc}. "
                f"Retrying in {delay_seconds}s..."
            )
            time.sleep(delay_seconds)

    raise RuntimeError(f"Could not connect to MySQL after {max_retries} attempts: {last_error}")


@app.on_event("startup")
def on_startup():
    init_db_with_retry()


@app.get("/health")
def health_check():
    if LOCAL_MODE:
        conn = get_sqlite_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        return {"status": "healthy", "service": "user-service", "database": "sqlite-local"}
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
        conn.close()
        return {"status": "healthy", "service": "user-service", "database": "connected"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")


@app.get("/users", response_model=List[UserResponse])
def list_users(role: Optional[str] = Query(default=None, pattern="^(Doctor|Patient)$")):
    if LOCAL_MODE:
        conn = get_sqlite_connection()
        if role:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, name, email, role, specialization, phone, created_at FROM users WHERE role = ? ORDER BY id ASC",
                    (role,),
                ).fetchall()
            ]
        else:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, name, email, role, specialization, phone, created_at FROM users ORDER BY id ASC"
                ).fetchall()
            ]
        conn.close()
        for row in rows:
            row["created_at"] = str(row["created_at"])
            row["specialization"] = row["specialization"] or ""
            row["phone"] = row["phone"] or ""
        return rows

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        if role:
            cursor.execute(
                "SELECT id, name, email, role, specialization, phone, created_at FROM users WHERE role = %s ORDER BY id ASC",
                (role,),
            )
        else:
            cursor.execute(
                "SELECT id, name, email, role, specialization, phone, created_at FROM users ORDER BY id ASC"
            )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        for row in rows:
            row["created_at"] = str(row["created_at"])
            row["specialization"] = row["specialization"] or ""
            row["phone"] = row["phone"] or ""
        return rows
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"MySQL query error: {exc}")


@app.post("/users", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreate):
    if LOCAL_MODE:
        conn = get_sqlite_connection()
        try:
            cur = conn.execute(
                "INSERT INTO users (name, email, role, specialization, phone) VALUES (?, ?, ?, ?, ?)",
                (
                    payload.name.strip(),
                    payload.email.strip().lower(),
                    payload.role,
                    (payload.specialization or "").strip(),
                    (payload.phone or "").strip(),
                ),
            )
            conn.commit()
            user = dict(
                conn.execute(
                    "SELECT id, name, email, role, specialization, phone, created_at FROM users WHERE id = ?",
                    (cur.lastrowid,),
                ).fetchone()
            )
            user["created_at"] = str(user["created_at"])
            user["specialization"] = user["specialization"] or ""
            user["phone"] = user["phone"] or ""
            return user
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=f"User with email already exists: {exc}")
        finally:
            conn.close()

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            INSERT INTO users (name, email, role, specialization, phone)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                payload.name.strip(),
                payload.email.strip().lower(),
                payload.role,
                (payload.specialization or "").strip(),
                (payload.phone or "").strip(),
            ),
        )
        conn.commit()
        new_id = cursor.lastrowid
        cursor.execute(
            "SELECT id, name, email, role, specialization, phone, created_at FROM users WHERE id = %s",
            (new_id,),
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        user["created_at"] = str(user["created_at"])
        user["specialization"] = user["specialization"] or ""
        user["phone"] = user["phone"] or ""
        return user
    except mysql.connector.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"User with email already exists: {exc}")
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"MySQL insert error: {exc}")


@app.get("/appointments", response_model=List[AppointmentResponse])
def list_appointments():
    query = """
        SELECT
            a.id,
            a.patient_id,
            a.doctor_id,
            p.name AS patient_name,
            d.name AS doctor_name,
            d.specialization AS doctor_specialization,
            a.appointment_date,
            a.symptoms,
            a.status,
            a.created_at
        FROM appointments a
        JOIN users p ON a.patient_id = p.id
        JOIN users d ON a.doctor_id = d.id
        ORDER BY a.id DESC
    """
    if LOCAL_MODE:
        conn = get_sqlite_connection()
        rows = [dict(r) for r in conn.execute(query).fetchall()]
        conn.close()
        for row in rows:
            row["created_at"] = str(row["created_at"])
            row["symptoms"] = row["symptoms"] or ""
            row["doctor_specialization"] = row["doctor_specialization"] or ""
        return rows

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        for row in rows:
            row["created_at"] = str(row["created_at"])
            row["symptoms"] = row["symptoms"] or ""
            row["doctor_specialization"] = row["doctor_specialization"] or ""
        return rows
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"MySQL query error: {exc}")


@app.post("/appointments", response_model=AppointmentResponse, status_code=201)
def create_appointment(payload: AppointmentCreate):
    select_query = """
        SELECT
            a.id,
            a.patient_id,
            a.doctor_id,
            p.name AS patient_name,
            d.name AS doctor_name,
            d.specialization AS doctor_specialization,
            a.appointment_date,
            a.symptoms,
            a.status,
            a.created_at
        FROM appointments a
        JOIN users p ON a.patient_id = p.id
        JOIN users d ON a.doctor_id = d.id
        WHERE a.id = {placeholder}
    """
    if LOCAL_MODE:
        conn = get_sqlite_connection()
        try:
            patient = conn.execute("SELECT id, role FROM users WHERE id = ?", (payload.patient_id,)).fetchone()
            if not patient or patient["role"] != "Patient":
                raise HTTPException(status_code=400, detail="Invalid patient_id: must reference a Patient.")

            doctor = conn.execute("SELECT id, role FROM users WHERE id = ?", (payload.doctor_id,)).fetchone()
            if not doctor or doctor["role"] != "Doctor":
                raise HTTPException(status_code=400, detail="Invalid doctor_id: must reference a Doctor.")

            cur = conn.execute(
                "INSERT INTO appointments (patient_id, doctor_id, appointment_date, symptoms, status) VALUES (?, ?, ?, ?, ?)",
                (
                    payload.patient_id,
                    payload.doctor_id,
                    payload.appointment_date.strip(),
                    payload.symptoms.strip(),
                    payload.status.strip(),
                ),
            )
            conn.commit()
            appt = dict(conn.execute(select_query.format(placeholder="?"), (cur.lastrowid,)).fetchone())
            appt["created_at"] = str(appt["created_at"])
            appt["symptoms"] = appt["symptoms"] or ""
            appt["doctor_specialization"] = appt["doctor_specialization"] or ""
            return appt
        finally:
            conn.close()

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT id, role FROM users WHERE id = %s", (payload.patient_id,))
        patient = cursor.fetchone()
        if not patient or patient["role"] != "Patient":
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid patient_id: must reference a Patient.")

        cursor.execute("SELECT id, role FROM users WHERE id = %s", (payload.doctor_id,))
        doctor = cursor.fetchone()
        if not doctor or doctor["role"] != "Doctor":
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid doctor_id: must reference a Doctor.")

        cursor.execute(
            """
            INSERT INTO appointments (patient_id, doctor_id, appointment_date, symptoms, status)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                payload.patient_id,
                payload.doctor_id,
                payload.appointment_date.strip(),
                payload.symptoms.strip(),
                payload.status.strip(),
            ),
        )
        conn.commit()
        new_id = cursor.lastrowid

        cursor.execute(select_query.format(placeholder="%s"), (new_id,))
        appt = cursor.fetchone()
        cursor.close()
        conn.close()
        appt["created_at"] = str(appt["created_at"])
        appt["symptoms"] = appt["symptoms"] or ""
        appt["doctor_specialization"] = appt["doctor_specialization"] or ""
        return appt
    except HTTPException:
        raise
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"MySQL insert error: {exc}")

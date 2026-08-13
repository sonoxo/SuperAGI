from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("RIGHT_TRACK_DATA_DIR", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("RIGHT_TRACK_DB_PATH", DATA_DIR / "right_track.db"))
SECRET = os.getenv("RIGHT_TRACK_SECRET", "")
ENV = os.getenv("RIGHT_TRACK_ENV", "development")
if ENV == "production" and len(SECRET) < 32:
    raise RuntimeError("RIGHT_TRACK_SECRET must be at least 32 characters in production")
if not SECRET:
    SECRET = secrets.token_urlsafe(48)

app = FastAPI(title="Right Track API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("RIGHT_TRACK_ALLOWED_ORIGINS", "*").split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

PROVIDERS = {
    "copyright": {"mode": "official_handoff", "url": "https://www.copyright.gov/registration/", "label": "U.S. Copyright Office"},
    "mlc": {"mode": "authorized_account", "url": "https://www.themlc.com/", "label": "The MLC"},
    "soundexchange": {"mode": "authorized_account", "url": "https://www.soundexchange.com/", "label": "SoundExchange"},
    "bmi": {"mode": "authorized_account", "url": "https://www.bmi.com/", "label": "BMI"},
    "ascap": {"mode": "authorized_account", "url": "https://www.ascap.com/", "label": "ASCAP"},
}

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          artist TEXT NOT NULL DEFAULT '',
          isrc TEXT NOT NULL DEFAULT '',
          upc TEXT NOT NULL DEFAULT '',
          composition_split REAL NOT NULL DEFAULT 0,
          master_split REAL NOT NULL DEFAULT 0,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS evidence(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          provider TEXT NOT NULL,
          receipt_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          note TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL,
          UNIQUE(project_id, provider, receipt_id)
        );
        CREATE TABLE IF NOT EXISTS royalty_imports(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER,
          user_id INTEGER NOT NULL,
          source TEXT NOT NULL,
          row_count INTEGER NOT NULL,
          total_amount REAL NOT NULL,
          created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          project_id INTEGER,
          action TEXT NOT NULL,
          payload TEXT NOT NULL,
          created_at INTEGER NOT NULL
        );
        """)

init_db()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return base64.urlsafe_b64encode(salt + digest).decode()


def verify_password(password: str, stored: str) -> bool:
    raw = base64.urlsafe_b64decode(stored.encode())
    salt, expected = raw[:16], raw[16:]
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return hmac.compare_digest(actual, expected)


def issue_token(user_id: int) -> str:
    exp = int(time.time()) + 60 * 60 * 24 * 7
    body = f"{user_id}.{exp}"
    sig = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{body}.{sig}".encode()).decode()


def token_user(token: str) -> int:
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        uid, exp, sig = decoded.split(".", 2)
        body = f"{uid}.{exp}"
        expected = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected) or int(exp) < int(time.time()):
            raise ValueError
        return int(uid)
    except Exception:
        raise HTTPException(401, "Invalid or expired token")


def current_user(authorization: Optional[str] = Header(default=None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    return token_user(authorization[7:])


def audit(user_id: int, action: str, payload: dict, project_id: Optional[int] = None):
    with db() as c:
        c.execute("INSERT INTO audit_log(user_id,project_id,action,payload,created_at) VALUES(?,?,?,?,?)",
                  (user_id, project_id, action, json.dumps(payload, separators=(",", ":")), int(time.time())))


class AuthBody(BaseModel):
    email: str
    password: str = Field(min_length=10, max_length=200)

class ProjectBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    artist: str = Field(default="", max_length=200)
    isrc: str = Field(default="", max_length=32)
    upc: str = Field(default="", max_length=32)
    composition_split: float = Field(default=0, ge=0, le=100)
    master_split: float = Field(default=0, ge=0, le=100)

class ReceiptBody(BaseModel):
    project_id: int
    provider: str
    receipt_id: str = Field(min_length=2, max_length=200)
    note: str = Field(default="", max_length=1000)

@app.get("/api/health")
def health():
    return {"ok": True, "service": "right-track", "environment": ENV}

@app.post("/api/auth/register")
def register(body: AuthBody):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Valid email required")
    with db() as c:
        try:
            cur = c.execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)",
                            (email, hash_password(body.password), int(time.time())))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Account already exists")
    return {"token": issue_token(cur.lastrowid)}

@app.post("/api/auth/login")
def login(body: AuthBody):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (body.email.strip().lower(),)).fetchone()
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    return {"token": issue_token(row["id"])}

@app.get("/api/projects")
def list_projects(user_id: int = Depends(current_user)):
    with db() as c:
        rows = c.execute("SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/projects")
def create_project(body: ProjectBody, user_id: int = Depends(current_user)):
    now = int(time.time())
    with db() as c:
        cur = c.execute("""INSERT INTO projects(user_id,title,artist,isrc,upc,composition_split,master_split,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (user_id, body.title, body.artist, body.isrc, body.upc, body.composition_split, body.master_split, now, now))
    pid = cur.lastrowid
    audit(user_id, "project.created", body.model_dump(), pid)
    return {"id": pid, **body.model_dump(), "rights_ready": body.composition_split == 100 and body.master_split == 100}

@app.put("/api/projects/{project_id}")
def update_project(project_id: int, body: ProjectBody, user_id: int = Depends(current_user)):
    with db() as c:
        exists = c.execute("SELECT id FROM projects WHERE id=? AND user_id=?", (project_id, user_id)).fetchone()
        if not exists:
            raise HTTPException(404, "Project not found")
        c.execute("""UPDATE projects SET title=?,artist=?,isrc=?,upc=?,composition_split=?,master_split=?,updated_at=?
                     WHERE id=? AND user_id=?""",
                  (body.title, body.artist, body.isrc, body.upc, body.composition_split, body.master_split, int(time.time()), project_id, user_id))
    audit(user_id, "project.updated", body.model_dump(), project_id)
    return {"ok": True, "rights_ready": body.composition_split == 100 and body.master_split == 100}

@app.get("/api/providers")
def providers():
    return PROVIDERS

@app.get("/api/providers/{provider}/handoff")
def provider_handoff(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    return PROVIDERS[provider]

@app.post("/api/evidence")
def add_evidence(body: ReceiptBody, user_id: int = Depends(current_user)):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, "Unsupported provider")
    with db() as c:
        project = c.execute("SELECT id FROM projects WHERE id=? AND user_id=?", (body.project_id, user_id)).fetchone()
        if not project:
            raise HTTPException(404, "Project not found")
        try:
            cur = c.execute("INSERT INTO evidence(project_id,user_id,provider,receipt_id,status,note,created_at) VALUES(?,?,?,?,?,?,?)",
                            (body.project_id, user_id, body.provider, body.receipt_id.strip(), "recorded", body.note, int(time.time())))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Receipt already recorded")
    audit(user_id, "evidence.recorded", body.model_dump(), body.project_id)
    return {"id": cur.lastrowid, "status": "recorded", "verified_by_provider": False,
            "message": "Receipt stored. Provider verification remains pending unless an authorized provider connection confirms it."}

@app.get("/api/projects/{project_id}/evidence")
def project_evidence(project_id: int, user_id: int = Depends(current_user)):
    with db() as c:
        rows = c.execute("SELECT provider,receipt_id,status,note,created_at FROM evidence WHERE project_id=? AND user_id=? ORDER BY created_at DESC",
                         (project_id, user_id)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/projects/{project_id}/documents")
async def upload_document(project_id: int, file: UploadFile = File(...), category: str = Form("evidence"), user_id: int = Depends(current_user)):
    with db() as c:
        if not c.execute("SELECT id FROM projects WHERE id=? AND user_id=?", (project_id, user_id)).fetchone():
            raise HTTPException(404, "Project not found")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "File exceeds 20 MB")
    safe_name = Path(file.filename or "upload.bin").name
    digest = hashlib.sha256(data).hexdigest()
    dest = UPLOAD_DIR / f"u{user_id}_p{project_id}_{digest[:16]}_{safe_name}"
    dest.write_bytes(data)
    audit(user_id, "document.uploaded", {"category": category, "filename": safe_name, "sha256": digest}, project_id)
    return {"filename": safe_name, "sha256": digest, "size": len(data), "category": category}

@app.post("/api/royalties/import")
async def import_royalties(file: UploadFile = File(...), source: str = Form("csv"), project_id: Optional[int] = Form(default=None), user_id: int = Depends(current_user)):
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "CSV exceeds 10 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "CSV must be UTF-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    total = 0.0
    for row in rows:
        for key in ("amount", "royalty", "earnings", "net"):
            if row.get(key):
                try:
                    total += float(str(row[key]).replace("$", "").replace(",", ""))
                    break
                except ValueError:
                    pass
    with db() as c:
        cur = c.execute("INSERT INTO royalty_imports(project_id,user_id,source,row_count,total_amount,created_at) VALUES(?,?,?,?,?,?)",
                        (project_id, user_id, source[:100], len(rows), total, int(time.time())))
    audit(user_id, "royalties.imported", {"source": source, "row_count": len(rows), "total_amount": total}, project_id)
    return {"id": cur.lastrowid, "rows": len(rows), "total_amount": round(total, 2), "verified": False}

@app.get("/api/audit")
def get_audit(user_id: int = Depends(current_user)):
    with db() as c:
        rows = c.execute("SELECT id,project_id,action,payload,created_at FROM audit_log WHERE user_id=? ORDER BY id DESC LIMIT 500", (user_id,)).fetchall()
    return [dict(r) for r in rows]

# Static frontend is served by the same origin in production.
app.mount("/assets", StaticFiles(directory=ROOT), name="assets")

@app.get("/")
def root():
    return FileResponse(ROOT / "index.html")

@app.get("/{path:path}")
def static_fallback(path: str):
    candidate = (ROOT / path).resolve()
    if ROOT in candidate.parents and candidate.is_file() and candidate.name not in {"backend.py"}:
        return FileResponse(candidate)
    return FileResponse(ROOT / "index.html")

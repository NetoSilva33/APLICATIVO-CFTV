"""Segura Gestão: app local de operações para segurança eletrônica."""
import base64
import calendar
import getpass
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("CFTV_DB", str(ROOT / "data" / "gestao.db")))
COOKIE = "segura_session"
PLATFORM_COOKIE = "segura_platform_session"
CLIENT_COOKIE = "segura_client_session"
ROLES = {"admin", "supervisor", "technician", "warehouse"}
KINDS = {"clients", "assets", "orders", "maintenance", "stockRequests", "purchaseRequests", "inventoryItems", "manuals", "products", "quotes", "company"}
SESSION_HOURS = 12
PBKDF2_ROUNDS = 310_000
PLAN_LIMITS = {"starter": 10, "pro": 50, "business": 500}
BILLING_PERIOD_MONTHS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}
_schema_lock = threading.Lock()
_initialized_databases = set()


def connect():
    path = Path(DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    key = str(path.resolve())
    with _schema_lock:
        if key not in _initialized_databases:
            initialize_schema(db)
            _initialized_databases.add(key)
    return db


def _table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(db, name):
    return {row[1] for row in db.execute(f"PRAGMA table_info({name})")}


def slugify(value):
    normalized = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "empresa"


def add_billing_period(start, period):
    months = BILLING_PERIOD_MONTHS[period]
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def add_months(start, months):
    month_index = start.month - 1 + int(months)
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def expiration_countdown(value, now=None):
    if not value:
        return None, False
    try:
        expiration = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None, False
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    seconds = (expiration - now).total_seconds()
    return math.ceil(seconds / 86400), expiration <= now


def initialize_schema(db):
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    current = (_table_exists(db, "organizations") and _table_exists(db, "memberships")
               and _table_exists(db, "users") and _table_exists(db, "sessions")
               and _table_exists(db, "records") and "organization_id" in _columns(db, "records"))
    if current:
        organization_columns = _columns(db, "organizations")
        if "billing_period" not in organization_columns:
            db.execute("ALTER TABLE organizations ADD COLUMN billing_period TEXT NOT NULL DEFAULT 'monthly'")
        if "subscription_ends_at" not in organization_columns:
            db.execute("ALTER TABLE organizations ADD COLUMN subscription_ends_at TEXT")
        db.execute("""CREATE TABLE IF NOT EXISTS platform_users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            salt BLOB NOT NULL, password_hash BLOB NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS platform_sessions (
            token_hash TEXT PRIMARY KEY, platform_user_id TEXT NOT NULL, expires TEXT NOT NULL)""")
        db.execute("UPDATE users SET is_platform_admin=0 WHERE is_platform_admin<>0")
        db.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
            id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, user_id TEXT,
            actor_name TEXT NOT NULL, action TEXT NOT NULL, kind TEXT NOT NULL,
            record_id TEXT, summary TEXT NOT NULL, created_at TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS client_portal_tokens (
            organization_id TEXT NOT NULL, client_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
            PRIMARY KEY(organization_id,client_id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS client_portal_sessions (
            token_hash TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
            client_id TEXT NOT NULL, expires TEXT NOT NULL)""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_records_org_kind ON records(organization_id,kind)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_memberships_org ON memberships(organization_id,role,active)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_org_time ON audit_logs(organization_id,created_at)")
        db.commit()
        return

    old_users = _table_exists(db, "users") and "username" in _columns(db, "users")
    old_sessions = _table_exists(db, "sessions") and "user_id" in _columns(db, "sessions")
    old_records = _table_exists(db, "records") and "organization_id" not in _columns(db, "records")
    if old_users:
        db.execute("ALTER TABLE users RENAME TO legacy_users_v1")
    if old_sessions:
        db.execute("ALTER TABLE sessions RENAME TO legacy_sessions_v1")
    if old_records:
        db.execute("ALTER TABLE records RENAME TO legacy_records_v1")

    db.execute("""CREATE TABLE organizations (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
        document TEXT NOT NULL DEFAULT '', plan TEXT NOT NULL DEFAULT 'starter',
        status TEXT NOT NULL DEFAULT 'trial', trial_ends_at TEXT,
        created_at TEXT NOT NULL, billing_period TEXT NOT NULL DEFAULT 'monthly',
        subscription_ends_at TEXT)""")
    db.execute("""CREATE TABLE users (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
        is_platform_admin INTEGER NOT NULL DEFAULT 0)""")
    db.execute("""CREATE TABLE memberships (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, user_id TEXT NOT NULL,
        username TEXT NOT NULL, role TEXT NOT NULL, salt BLOB NOT NULL,
        password_hash BLOB NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, UNIQUE(organization_id,username),
        UNIQUE(organization_id,user_id))""")
    db.execute("""CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY, membership_id TEXT NOT NULL, expires TEXT NOT NULL)""")
    db.execute("""CREATE TABLE platform_users (
        id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
        salt BLOB NOT NULL, password_hash BLOB NOT NULL,
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE platform_sessions (
        token_hash TEXT PRIMARY KEY, platform_user_id TEXT NOT NULL, expires TEXT NOT NULL)""")
    db.execute("""CREATE TABLE records (
        organization_id TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
        data TEXT NOT NULL, PRIMARY KEY(organization_id,kind,id))""")
    db.execute("""CREATE TABLE audit_logs (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, user_id TEXT,
        actor_name TEXT NOT NULL, action TEXT NOT NULL, kind TEXT NOT NULL,
        record_id TEXT, summary TEXT NOT NULL, created_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE client_portal_tokens (
        organization_id TEXT NOT NULL, client_id TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
        PRIMARY KEY(organization_id,client_id))""")
    db.execute("""CREATE TABLE client_portal_sessions (
        token_hash TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
        client_id TEXT NOT NULL, expires TEXT NOT NULL)""")

    if old_users:
        profile = {}
        if old_records:
            row = db.execute("SELECT data FROM legacy_records_v1 WHERE kind='company' ORDER BY rowid LIMIT 1").fetchone()
            if row:
                try:
                    profile = json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    profile = {}
        organization_id = str(uuid4())
        company_name = str(profile.get("name") or "Empresa principal").strip()[:180]
        base_slug, slug, suffix = slugify(company_name), slugify(company_name), 2
        while db.execute("SELECT 1 FROM organizations WHERE slug=?", (slug,)).fetchone():
            slug, suffix = f"{base_slug}-{suffix}", suffix + 1
        now = datetime.now(timezone.utc).isoformat()
        db.execute("""INSERT INTO organizations(id,name,slug,document,plan,status,trial_ends_at,created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
                   (organization_id, company_name, slug, str(profile.get("document", ""))[:40], "starter", "active", None, now))
        for row in db.execute("SELECT id,username,name,role,salt,password_hash,active,created FROM legacy_users_v1"):
            db.execute("INSERT INTO users VALUES (?,?,?,?)",
                       (row[0], row[2], row[7], 0))
            db.execute("INSERT INTO memberships VALUES (?,?,?,?,?,?,?,?,?)",
                       (row[0], organization_id, row[0], row[1], row[3], row[4], row[5], row[6], row[7]))
        if old_sessions:
            db.execute("""INSERT INTO sessions(token_hash,membership_id,expires)
                SELECT s.token_hash,s.user_id,s.expires FROM legacy_sessions_v1 s
                JOIN memberships m ON m.id=s.user_id""")
        if old_records:
            db.execute("INSERT INTO records(organization_id,kind,id,data) SELECT ?,kind,id,data FROM legacy_records_v1",
                       (organization_id,))
        if old_sessions:
            db.execute("DROP TABLE legacy_sessions_v1")
        db.execute("DROP TABLE legacy_users_v1")
        if old_records:
            db.execute("DROP TABLE legacy_records_v1")
    db.execute("CREATE INDEX idx_records_org_kind ON records(organization_id,kind)")
    db.execute("CREATE INDEX idx_memberships_org ON memberships(organization_id,role,active)")
    db.execute("CREATE INDEX idx_audit_org_time ON audit_logs(organization_id,created_at)")
    db.commit()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)


def create_platform_admin(name, username, password):
    name=str(name).strip()
    username=str(username).strip().lower()
    password=str(password)
    if not username or len(username)>80 or not name or len(name)>150 or not 12<=len(password)<=1024:
        raise ValueError("Informe nome, usuário e senha comercial com pelo menos 12 caracteres.")
    now=datetime.now(timezone.utc).isoformat()
    salt=secrets.token_bytes(16)
    platform_id=str(uuid4())
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT EXISTS(SELECT 1 FROM platform_users)").fetchone()[0]:
            raise RuntimeError("O login comercial já foi criado. Não é possível criar outro pelo assistente inicial.")
        db.execute("INSERT INTO platform_users VALUES (?,?,?,?,?,1,?)",
                   (platform_id,username,name,salt,password_hash(password,salt),now))
    return {"id":platform_id,"username":username,"name":name}


def setup_platform_admin_interactive():
    print("Configuração inicial do acesso comercial")
    name=input("Seu nome: ").strip()
    username=input("Usuário comercial: ").strip().lower()
    password=getpass.getpass("Senha comercial (mínimo 12 caracteres): ")
    confirmation=getpass.getpass("Confirme a senha: ")
    if password!=confirmation:
        raise ValueError("As senhas não coincidem.")
    admin=create_platform_admin(name,username,password)
    print(f"Login comercial criado para {admin['username']}. Acesse /plataforma ao iniciar o servidor.")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "static"), **kwargs)

    def json_response(self, code, data, extra_headers=()):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra_headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 5_000_000:
                raise ValueError("A solicitação está vazia ou excede o limite de 5 MB.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Dados inválidos.")
            return data
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("O conteúdo enviado não é um JSON válido.") from None

    def current_user(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            raw = cookie[COOKIE].value
        except Exception:
            return None
        digest = hashlib.sha256(raw.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with connect() as db:
            row = db.execute("""SELECT u.id AS account_id,m.id AS id,m.username,u.name,m.role,
                m.organization_id,o.name AS organization_name,o.slug AS organization_slug,
                o.document AS organization_document,o.plan AS organization_plan,
                o.status AS organization_status,o.trial_ends_at
                FROM sessions s JOIN memberships m ON m.id=s.membership_id
                JOIN users u ON u.id=m.user_id JOIN organizations o ON o.id=m.organization_id
                WHERE s.token_hash=? AND s.expires>? AND m.active=1
                  AND o.status IN ('active','trial')
                  AND (o.status!='trial' OR o.trial_ends_at IS NULL OR o.trial_ends_at>?)
                  AND (o.status!='active' OR o.subscription_ends_at IS NULL OR o.subscription_ends_at>?)""",
                (digest, now, now, now)).fetchone()
        if not row:
            return None
        return dict(row)

    def require_user(self):
        user = self.current_user()
        if not user:
            self.json_response(401, {"error": "Sua sessão terminou ou a empresa está suspensa. Entre novamente."})
            return None
        return user

    @staticmethod
    def public_company_user(user):
        if not user:
            return None
        allowed = ("id", "account_id", "username", "name", "role", "organization_name")
        return {key: user[key] for key in allowed if key in user}

    def current_platform_user(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            raw = cookie[PLATFORM_COOKIE].value
        except Exception:
            return None
        digest = hashlib.sha256(raw.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with connect() as db:
            row = db.execute("""SELECT u.id,u.username,u.name FROM platform_sessions s
                JOIN platform_users u ON u.id=s.platform_user_id
                WHERE s.token_hash=? AND s.expires>? AND u.active=1""", (digest,now)).fetchone()
        return dict(row) if row else None

    def current_client_portal(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            raw = cookie[CLIENT_COOKIE].value
        except Exception:
            return None
        digest = hashlib.sha256(raw.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with connect() as db:
            row = db.execute("""SELECT s.organization_id,s.client_id FROM client_portal_sessions s
                JOIN organizations o ON o.id=s.organization_id
                JOIN client_portal_tokens t ON t.organization_id=s.organization_id AND t.client_id=s.client_id
                WHERE s.token_hash=? AND s.expires>? AND o.status IN ('active','trial')
                  AND (o.status!='trial' OR o.trial_ends_at IS NULL OR o.trial_ends_at>?)
                  AND (o.status!='active' OR o.subscription_ends_at IS NULL OR o.subscription_ends_at>?)""",
                (digest, now, now, now)).fetchone()
        return dict(row) if row else None

    def require_client_portal(self):
        client_session = self.current_client_portal()
        if not client_session:
            self.json_response(401, {"error": "Acesse pelo link de cliente fornecido pela empresa."})
            return None
        return client_session

    def require_platform_user(self):
        user = self.current_platform_user()
        if not user:
            self.json_response(401, {"error": "Entre com a conta comercial da plataforma."})
            return None
        return user

    def do_GET(self):
        path = urlparse(self.path).path
        if path in {"/cliente", "/cliente/"}:
            self.path = "/client.html"
            return super().do_GET()
        if path in {"/plataforma", "/plataforma/", "/platform"}:
            self.path = "/platform.html"
            return super().do_GET()
        if path == "/api/session":
            with connect() as db:
                has_users = db.execute("SELECT EXISTS(SELECT 1 FROM users)").fetchone()[0]
            return self.json_response(200, {"setupRequired": not bool(has_users), "user": self.public_company_user(self.current_user())})
        if path == "/api/platform/session":
            with connect() as db:
                has_platform_users = db.execute("SELECT EXISTS(SELECT 1 FROM platform_users)").fetchone()[0]
            return self.json_response(200, {"setupRequired": not bool(has_platform_users), "user": self.current_platform_user()})
        if path == "/api/client-portal/data":
            portal = self.require_client_portal()
            if not portal:
                return
            with connect() as db:
                client_row = db.execute("SELECT data FROM records WHERE organization_id=? AND kind='clients' AND id=?",
                                         (portal["organization_id"], portal["client_id"])).fetchone()
                if not client_row:
                    return self.json_response(404, {"error": "O cadastro deste cliente não está mais disponível."})
                records = {kind: [] for kind in ("orders", "assets", "maintenance")}
                public_fields = {
                    "orders": ("id","number","title","scheduled","scheduled_time","duration_minutes","technician","status","summary","work","materials","signature_name","signature","signature_at","checklist","photos","created_at"),
                    "assets": ("id","name","brand","model","serial","last_service","next_service"),
                    "maintenance": ("id","equipment","performed","due","notes"),
                }
                for kind in records:
                    rows = db.execute("SELECT data FROM records WHERE organization_id=? AND kind=?",
                                      (portal["organization_id"], kind)).fetchall()
                    records[kind] = [{key: record[key] for key in public_fields[kind] if key in record}
                                     for row in rows if (record := json.loads(row[0])).get("client_id") == portal["client_id"]]
                organization = db.execute("SELECT name FROM organizations WHERE id=?", (portal["organization_id"],)).fetchone()
            client_record=json.loads(client_row[0])
            return self.json_response(200, {"client": {"id":portal["client_id"],"name":client_record.get("name","Cliente"),"contact":client_record.get("contact","")}, "organization": organization["name"], **records})
        if path == "/api/platform/companies":
            user = self.require_platform_user()
            if not user:
                return
            with connect() as db:
                rows = db.execute("""SELECT o.*,(SELECT COUNT(*) FROM memberships m WHERE m.organization_id=o.id) AS user_count
                    FROM organizations o ORDER BY o.created_at DESC""").fetchall()
            companies = []
            for row in rows:
                company = dict(row)
                company["user_count"] = int(company["user_count"])
                company["user_limit"] = PLAN_LIMITS.get(company["plan"], PLAN_LIMITS["starter"])
                company["expires_at"] = company["trial_ends_at"] if company["status"] == "trial" else company["subscription_ends_at"]
                company["days_remaining"], company["expired"] = expiration_countdown(company["expires_at"])
                companies.append(company)
            return self.json_response(200, {"companies": companies})
        if path == "/api/data":
            user = self.require_user()
            if not user:
                return
            self.generate_preventive_orders(user)
            with connect() as db:
                records = {key: [] for key in KINDS}
                for kind, data in db.execute("SELECT kind,data FROM records WHERE organization_id=? ORDER BY rowid", (user["organization_id"],)):
                    if kind in records:
                        records[kind].append(json.loads(data))
                reserved = {}
                for request in records["stockRequests"]:
                    if request.get("status") in {"pending", "approved", "separated"}:
                        for item in request.get("items", []):
                            item_id = item.get("item_id")
                            reserved[item_id] = reserved.get(item_id, 0) + float(item.get("quantity", 0))
                records["inventoryItems"] = [
                    {**item,"reserved_quantity":reserved.get(item["id"],0),
                     "available_quantity":max(0,float(item.get("quantity",0))-reserved.get(item["id"],0))}
                    for item in records["inventoryItems"]
                ]
                result = {key: [] for key in KINDS}
                if user["role"] == "warehouse":
                    result["stockRequests"] = records["stockRequests"]
                    result["purchaseRequests"] = records["purchaseRequests"]
                    result["inventoryItems"] = records["inventoryItems"]
                elif user["role"] == "technician":
                    orders = [o for o in records["orders"] if o.get("technician_id") == user["id"]]
                    client_ids = {o.get("client_id") for o in orders}
                    asset_ids = {o.get("asset_id") for o in orders if o.get("asset_id")}
                    result["orders"] = orders
                    result["clients"] = [c for c in records["clients"] if c.get("id") in client_ids]
                    result["assets"] = [a for a in records["assets"] if a.get("id") in asset_ids]
                    result["maintenance"] = [m for m in records["maintenance"] if m.get("asset_id") in asset_ids]
                    result["stockRequests"] = [r for r in records["stockRequests"] if r.get("requester_id") == user["id"]]
                    result["purchaseRequests"] = [r for r in records["purchaseRequests"] if r.get("requester_id") == user["id"]]
                    result["inventoryItems"] = records["inventoryItems"]
                    result["manuals"] = records["manuals"]
                else:
                    result = records
                result["organization"] = {"name":user["organization_name"]}
                if user["role"] == "admin":
                    result["users"] = [dict(id=r[0],username=r[1],name=r[2],role=r[3],active=bool(r[4]))
                        for r in db.execute("""SELECT m.id,m.username,u.name,m.role,m.active FROM memberships m
                            JOIN users u ON u.id=m.user_id WHERE m.organization_id=? ORDER BY u.name""",
                            (user["organization_id"],))]
                if user["role"] in {"admin","supervisor"}:
                    result["team"] = [dict(id=r[0],name=r[1],role=r[2])
                        for r in db.execute("""SELECT m.id,u.name,m.role FROM memberships m JOIN users u ON u.id=m.user_id
                            WHERE m.organization_id=? AND m.active=1 AND m.role='technician' ORDER BY u.name""",
                            (user["organization_id"],))]
            alerts=[]
            today=datetime.now(timezone.utc).date()
            for asset in result.get("assets",[]):
                due=asset.get("next_service")
                if due:
                    try:
                        days=(datetime.strptime(due,"%Y-%m-%d").date()-today).days
                        if days<=30:
                            alerts.append({"kind":"maintenance","title":asset.get("name","Equipamento"),"detail":"Atrasada" if days<0 else ("Vence hoje" if days==0 else f"Vence em {days} dias"),"severity":"late" if days<0 else "soon"})
                    except ValueError:
                        pass
            for item in result.get("inventoryItems",[]):
                if float(item.get("available_quantity",0))<=float(item.get("minimum_quantity",0)):
                    alerts.append({"kind":"stock","title":item.get("name","Material"),"detail":"Estoque baixo","severity":"late"})
            if user["role"] in {"admin","supervisor","warehouse"}:
                for request in result.get("stockRequests",[]):
                    if request.get("status")=="pending":
                        alerts.append({"kind":"stockRequest","title":request.get("item","Pedido de material"),"detail":"Solicitação aguardando análise","severity":"soon"})
            if user["role"] in {"admin","supervisor"}:
                for request in result.get("purchaseRequests",[]):
                    if request.get("status")=="pending":
                        alerts.append({"kind":"purchase","title":request.get("item","Compra"),"detail":"Compra aguardando aprovação","severity":"soon"})
            result["alerts"]=alerts
            return self.json_response(200,result)
        if path.startswith("/api/"):
            return self.json_response(404, {"error": "Endereço não encontrado."})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/api/") and self.headers.get("Origin"):
            if self.headers.get("Origin") != "http://" + self.headers.get("Host", "") and self.headers.get("Origin") != "https://" + self.headers.get("Host", ""):
                return self.json_response(403, {"error": "Origem não permitida."})
        try:
            data = self.read_body()
        except ValueError as error:
            return self.json_response(400, {"error": str(error)})

        if path == "/api/setup":
            username = str(data.get("username", "")).strip().lower()
            name, password = str(data.get("name", "")).strip(), str(data.get("password", ""))
            company_name = str(data.get("company_name", "")).strip()
            if not username or len(username) > 80 or not name or len(name) > 150 or not company_name or len(company_name) > 180 or len(password) < 12:
                return self.json_response(400, {"error": "Informe empresa, usuário e nome; a senha inicial precisa ter pelo menos 12 caracteres."})
            salt = secrets.token_bytes(16)
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT EXISTS(SELECT 1 FROM users)").fetchone()[0]:
                    return self.json_response(409, {"error": "O administrador inicial já foi criado."})
                organization_id, user_id, membership_id = str(uuid4()), str(uuid4()), str(uuid4())
                now=datetime.now(timezone.utc)
                slug=slugify(company_name)
                if db.execute("SELECT 1 FROM organizations WHERE slug=?", (slug,)).fetchone():
                    slug=f"{slug}-{secrets.token_hex(2)}"
                db.execute("INSERT INTO organizations(id,name,slug,plan,status,created_at) VALUES (?,?,?,?,?,?)",
                           (organization_id,company_name,slug,"starter","active",now.isoformat()))
                db.execute("INSERT INTO users VALUES (?,?,?,0)", (user_id,name,now.isoformat()))
                db.execute("INSERT INTO memberships VALUES (?,?,?,?,?,?,?,?,?)",
                           (membership_id,organization_id,user_id,username,"admin",salt,password_hash(password,salt),1,now.isoformat()))
                org={"id":organization_id,"name":company_name,"slug":slug,"plan":"starter","status":"active","trial_ends_at":None}
                self.log_audit(db,{"organization_id":organization_id,"id":membership_id,"name":name},"create","organization",organization_id,"Empresa criada")
            return self.login_user(membership_id,user_id,organization_id,username,name,"admin",org)

        if path == "/api/login":
            username = str(data.get("username", "")).strip().lower()
            password = str(data.get("password", ""))
            with connect() as db:
                company_slug=str(data.get("company", "")).strip().lower()
                query="""SELECT m.id AS membership_id,u.id AS user_id,m.organization_id,m.username,u.name,m.role,
                    m.salt,m.password_hash,m.active,o.name AS organization_name,
                    o.slug AS organization_slug,o.document AS organization_document,o.plan AS organization_plan,
                    o.status AS organization_status,o.trial_ends_at FROM memberships m JOIN users u ON u.id=m.user_id
                    JOIN organizations o ON o.id=m.organization_id WHERE m.username=? AND m.active=1
                    AND o.status IN ('active','trial')
                    AND (o.status!='trial' OR o.trial_ends_at IS NULL OR o.trial_ends_at>?)
                    AND (o.status!='active' OR o.subscription_ends_at IS NULL OR o.subscription_ends_at>?)"""
                now=datetime.now(timezone.utc).isoformat()
                params=[username,now,now]
                if company_slug:
                    query+=" AND o.slug=?"
                    params.append(company_slug)
                rows=db.execute(query,params).fetchall()
            if not rows:
                return self.json_response(401, {"error": "Usuário ou senha incorretos."})
            if len(rows)>1:
                return self.json_response(400,{"error":"Informe o código da empresa para identificar seu acesso."})
            row=rows[0]
            if not hmac.compare_digest(password_hash(password,row["salt"]),row["password_hash"]):
                return self.json_response(401,{"error":"Usuário ou senha incorretos."})
            org={"id":row["organization_id"],"name":row["organization_name"],"slug":row["organization_slug"],
                 "document":row["organization_document"],"plan":row["organization_plan"],"status":row["organization_status"],"trial_ends_at":row["trial_ends_at"]}
            return self.login_user(row["membership_id"],row["user_id"],row["organization_id"],row["username"],row["name"],row["role"],org)

        if path == "/api/logout":
            user = self.require_user()
            if not user:
                return
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie[COOKIE].value if COOKIE in cookie else ""
            with connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
            return self.json_response(200, {"ok": True}, (("Set-Cookie", f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"),))

        if path == "/api/platform/setup":
            return self.json_response(403,{"error":"A conta comercial inicial deve ser criada localmente no servidor."})

        if path == "/api/platform/login":
            username=str(data.get("username", "")).strip().lower()
            password=str(data.get("password", ""))
            with connect() as db:
                row=db.execute("SELECT id,username,name,salt,password_hash FROM platform_users WHERE username=? AND active=1",(username,)).fetchone()
            if not row or not hmac.compare_digest(password_hash(password,row["salt"]),row["password_hash"]):
                return self.json_response(401,{"error":"Usuário ou senha comercial incorretos."})
            return self.login_platform_user(row)

        if path == "/api/platform/logout":
            platform_user=self.require_platform_user()
            if not platform_user:
                return
            cookie=SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            token=cookie[PLATFORM_COOKIE].value if PLATFORM_COOKIE in cookie else ""
            with connect() as db:
                db.execute("DELETE FROM platform_sessions WHERE token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),))
            return self.json_response(200,{"ok":True},(("Set-Cookie",f"{PLATFORM_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"),))

        if path == "/api/client-portal/session":
            token = str(data.get("token", ""))
            if not 32 <= len(token) <= 128:
                return self.json_response(401, {"error": "O link de acesso do cliente é inválido ou expirou."})
            digest = hashlib.sha256(token.encode()).hexdigest()
            with connect() as db:
                row = db.execute("SELECT organization_id,client_id FROM client_portal_tokens WHERE token_hash=?", (digest,)).fetchone()
                if not row:
                    return self.json_response(401, {"error": "O link de acesso do cliente é inválido ou foi substituído."})
                session_token = secrets.token_urlsafe(32)
                expires = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
                db.execute("INSERT INTO client_portal_sessions VALUES (?,?,?,?)",
                           (hashlib.sha256(session_token.encode()).hexdigest(), row["organization_id"], row["client_id"], expires))
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            cookie = f"{CLIENT_COOKIE}={session_token}; HttpOnly; SameSite=Strict; Path=/api/client-portal; Max-Age=28800{secure}"
            return self.json_response(200, {"ok": True}, (("Set-Cookie", cookie),))

        if path == "/api/client-portal/logout":
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            raw = cookie[CLIENT_COOKIE].value if CLIENT_COOKIE in cookie else ""
            if raw:
                with connect() as db:
                    db.execute("DELETE FROM client_portal_sessions WHERE token_hash=?", (hashlib.sha256(raw.encode()).hexdigest(),))
            return self.json_response(200, {"ok": True}, (("Set-Cookie", f"{CLIENT_COOKIE}=; HttpOnly; SameSite=Strict; Path=/api/client-portal; Max-Age=0"),))

        platform_routes={"/api/platform/companies","/api/platform/companies/update"}
        user = self.require_platform_user() if path in platform_routes else self.require_user()
        if not user:
            return
        if path == "/api/client-portal/create-link":
            if user["role"] not in {"admin", "supervisor"}:
                return self.json_response(403, {"error": "Somente a administração ou supervisão pode criar links de cliente."})
            client_id = str(data.get("client_id", ""))
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode()).hexdigest()
            now = datetime.now(timezone.utc).isoformat()
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                exists = db.execute("SELECT 1 FROM records WHERE organization_id=? AND kind='clients' AND id=?",
                                    (user["organization_id"], client_id)).fetchone()
                if not exists:
                    return self.json_response(404, {"error": "Cliente não encontrado nesta empresa."})
                db.execute("DELETE FROM client_portal_sessions WHERE organization_id=? AND client_id=?",
                           (user["organization_id"], client_id))
                db.execute("INSERT OR REPLACE INTO client_portal_tokens VALUES (?,?,?,?)",
                           (user["organization_id"], client_id, digest, now))
                self.log_audit(db, user, "create", "client_portal", client_id, "Link de acesso do cliente renovado")
            return self.json_response(201, {"token": token})
        if path == "/api/platform/companies":
            name=str(data.get("name","")).strip()
            slug=slugify(data.get("slug") or name)
            document=str(data.get("document","")).strip()[:40]
            plan=str(data.get("plan","starter"))
            admin_name=str(data.get("admin_name","")).strip()
            admin_username=str(data.get("admin_username","")).strip().lower()
            admin_password=str(data.get("admin_password",""))
            if not name or len(name)>180 or not admin_name or len(admin_name)>150 or not admin_username or len(admin_username)>80 or not 12<=len(admin_password)<=1024 or plan not in PLAN_LIMITS:
                return self.json_response(400,{"error":"Informe empresa, plano e os dados do primeiro administrador; a senha precisa ter 12 caracteres ou mais."})
            now=datetime.now(timezone.utc)
            org_id,user_id,membership_id=str(uuid4()),str(uuid4()),str(uuid4())
            salt=secrets.token_bytes(16)
            try:
                with connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("INSERT INTO organizations(id,name,slug,document,plan,status,trial_ends_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
                               (org_id,name,slug,document,plan,"trial",(now+timedelta(days=14)).isoformat(),now.isoformat()))
                    db.execute("INSERT INTO users VALUES (?,?,?,0)",(user_id,admin_name,now.isoformat()))
                    db.execute("INSERT INTO memberships VALUES (?,?,?,?,?,?,?,?,?)",
                               (membership_id,org_id,user_id,admin_username,"admin",salt,password_hash(admin_password,salt),1,now.isoformat()))
                    self.log_audit(db,{"organization_id":org_id,"id":user["id"],"name":"Plataforma"},"create","organization",org_id,"Empresa e administrador inicial cadastrados")
            except sqlite3.IntegrityError:
                return self.json_response(409,{"error":"O código da empresa ou nome de usuário já existe nessa empresa."})
            return self.json_response(201,{"company":{"id":org_id,"name":name,"slug":slug,"document":document,"plan":plan,"status":"trial","trial_ends_at":(now+timedelta(days=14)).isoformat()},"admin":{"username":admin_username,"name":admin_name}})

        if path == "/api/platform/companies/update":
            org_id=str(data.get("id",""))
            plan=str(data.get("plan",""))
            status=str(data.get("status",""))
            if plan not in PLAN_LIMITS or status not in {"trial","active","suspended"}:
                return self.json_response(400,{"error":"Plano ou situação inválidos."})
            requested_period=data.get("billing_period")
            if requested_period is not None and str(requested_period) not in BILLING_PERIOD_MONTHS:
                return self.json_response(400,{"error":"Escolha um período mensal, trimestral, semestral ou anual."})
            renew=data.get("renew") is True
            if renew and status!="active":
                return self.json_response(400,{"error":"Para iniciar ou renovar um prazo, selecione a situação Ativa."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                target=db.execute("""SELECT id,name,slug,document,plan,status,trial_ends_at,
                    billing_period,subscription_ends_at FROM organizations WHERE id=?""",(org_id,)).fetchone()
                if not target:
                    return self.json_response(404,{"error":"Empresa não encontrada."})
                billing_period=str(requested_period if requested_period is not None else target["billing_period"] or "monthly")
                users_count=db.execute("SELECT COUNT(*) FROM memberships WHERE organization_id=?",(org_id,)).fetchone()[0]
                if users_count>PLAN_LIMITS[plan]:
                    return self.json_response(400,{"error":f"Esse plano permite até {PLAN_LIMITS[plan]} acessos; a empresa já tem {users_count}."})
                name=str(data.get("name",target["name"])).strip()
                document=str(data.get("document",target["document"])).strip()[:40]
                code=str(data.get("slug",target["slug"])).strip()
                if not name or len(name)>180 or not code or len(code)>80:
                    return self.json_response(400,{"error":"Informe um nome e um código de empresa válidos."})
                slug=slugify(code)
                duplicate=db.execute("SELECT 1 FROM organizations WHERE slug=? AND id<>?",(slug,org_id)).fetchone()
                if duplicate:
                    return self.json_response(409,{"error":"Esse código já está sendo usado por outra empresa."})
                now=datetime.now(timezone.utc)
                subscription_end=target["subscription_ends_at"]
                days_left,expired=expiration_countdown(subscription_end,now)
                if status=="active" and (not subscription_end or expired) and not renew:
                    return self.json_response(400,{"error":"Marque iniciar/renovar prazo para ativar uma empresa sem vigência ou com o prazo vencido."})
                if renew:
                    base=now
                    if subscription_end and days_left is not None and not expired:
                        base=datetime.fromisoformat(subscription_end)
                        if base.tzinfo is None:
                            base=base.replace(tzinfo=timezone.utc)
                    subscription_end=add_billing_period(base,billing_period).isoformat()
                db.execute("""UPDATE organizations SET name=?,slug=?,document=?,plan=?,status=?,
                    billing_period=?,subscription_ends_at=? WHERE id=?""",
                           (name,slug,document,plan,status,billing_period,subscription_end,org_id))
                platform_actor={**user,"organization_id":org_id,"name":"Plataforma"}
                self.log_audit(db,platform_actor,"update","organization",org_id,
                               f"Empresa {name}; código {target['slug']} → {slug}; plano {target['plan']} → {plan}; situação {target['status']} → {status}; período {billing_period}; vigência {subscription_end or 'não definida'}")
            return self.json_response(200,{"ok":True})

        if path == "/api/organization/update":
            return self.json_response(403,{"error":"Os dados cadastrais da empresa são gerenciados pelo portal comercial."})

        if path == "/api/users":
            if user["role"] != "admin":
                return self.json_response(403, {"error": "Somente administradores da empresa podem gerenciar acessos."})
            username,name=str(data.get("username","")).strip().lower(),str(data.get("name","")).strip()
            password,role=str(data.get("password","")),str(data.get("role",""))
            if not username or len(username)>80 or not name or len(name)>150 or not 12<=len(password)<=1024 or role not in ROLES:
                return self.json_response(400,{"error":"Informe usuário, nome, perfil válido e senha com pelo menos 12 caracteres."})
            salt,user_id,membership_id=secrets.token_bytes(16),str(uuid4()),str(uuid4())
            now=datetime.now(timezone.utc).isoformat()
            try:
                with connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    org=db.execute("SELECT plan,status FROM organizations WHERE id=?",(user["organization_id"],)).fetchone()
                    count=db.execute("SELECT COUNT(*) FROM memberships WHERE organization_id=?",(user["organization_id"],)).fetchone()[0]
                    if count>=PLAN_LIMITS.get(org["plan"],PLAN_LIMITS["starter"]):
                        return self.json_response(400,{"error":"O limite de acessos deste plano foi atingido."})
                    db.execute("INSERT INTO users VALUES (?,?,?,0)",(user_id,name,now))
                    db.execute("INSERT INTO memberships VALUES (?,?,?,?,?,?,?,?,?)",
                               (membership_id,user["organization_id"],user_id,username,role,salt,password_hash(password,salt),1,now))
                    self.log_audit(db,user,"create","user",membership_id,f"Acesso criado para {name} ({role})")
            except sqlite3.IntegrityError:
                return self.json_response(409,{"error":"Esse nome de usuário já está cadastrado nesta empresa."})
            return self.json_response(201,{"id":membership_id,"username":username,"name":name,"role":role,"active":True})

        if path in {"/api/users/reset-password","/api/users/update"}:
            if user["role"]!="admin":
                return self.json_response(403,{"error":"Somente administradores da empresa podem editar contas."})
            target_id=str(data.get("user_id",""))
            current_password_reset=path=="/api/users/reset-password"
            if current_password_reset:
                username=name=new_role=None
                password=str(data.get("password",""))
                active=None
                if not target_id or not 12<=len(password)<=1024:
                    return self.json_response(400,{"error":"Informe uma nova senha com pelo menos 12 caracteres."})
            else:
                username=str(data.get("username","")).strip().lower()
                name=str(data.get("name","")).strip()
                new_role=str(data.get("role",""))
                password=str(data.get("password",""))
                active=data.get("active")
                if not target_id or not username or len(username)>80 or not name or len(name)>150 or new_role not in ROLES or not isinstance(active,bool):
                    return self.json_response(400,{"error":"Informe nome, usuário, perfil e situação válidos."})
                if password and not 12<=len(password)<=1024:
                    return self.json_response(400,{"error":"A nova senha deve ter entre 12 e 1024 caracteres."})
            cookie=SimpleCookie()
            cookie.load(self.headers.get("Cookie",""))
            current_token=cookie[COOKIE].value if COOKIE in cookie else ""
            current_digest=hashlib.sha256(current_token.encode()).hexdigest()
            try:
                with connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    query="""SELECT m.id,m.user_id,m.username,m.role,m.active,u.name FROM memberships m JOIN users u ON u.id=m.user_id
                        WHERE m.id=? AND m.organization_id=?"""
                    current=db.execute(query,(target_id,user["organization_id"])).fetchone()
                    if not current:
                        return self.json_response(404,{"error":"Conta não encontrada nesta empresa."})
                    is_self=target_id==user["id"]
                    if current_password_reset:
                        salt=secrets.token_bytes(16)
                        db.execute("UPDATE memberships SET salt=?,password_hash=? WHERE id=?",(salt,password_hash(password,salt),target_id))
                        saved={"id":target_id,"username":current["username"],"name":current["name"],"role":current["role"],"active":bool(current["active"])}
                    else:
                        if is_self and (new_role!=current["role"] or active!=bool(current["active"])):
                            return self.json_response(400,{"error":"Para mudar seu próprio perfil ou situação, outro administrador precisa fazer a alteração."})
                        if current["role"]=="admin" and current["active"] and (new_role!="admin" or not active):
                            remaining=db.execute("SELECT COUNT(*) FROM memberships WHERE organization_id=? AND role='admin' AND active=1 AND id<>?",
                                                 (user["organization_id"],target_id)).fetchone()[0]
                            if remaining==0:
                                return self.json_response(400,{"error":"Mantenha pelo menos um administrador ativo nesta empresa."})
                        if password:
                            salt=secrets.token_bytes(16)
                            db.execute("UPDATE memberships SET username=?,role=?,active=?,salt=?,password_hash=? WHERE id=?",
                                       (username,new_role,int(active),salt,password_hash(password,salt),target_id))
                        else:
                            db.execute("UPDATE memberships SET username=?,role=?,active=? WHERE id=?",
                                       (username,new_role,int(active),target_id))
                        db.execute("UPDATE users SET name=? WHERE id=?",(name,current["user_id"]))
                        saved={"id":target_id,"username":username,"name":name,"role":new_role,"active":active}
                    db.execute("DELETE FROM sessions WHERE membership_id=? AND token_hash<>?",(target_id,current_digest))
                    self.log_audit(db,user,"update","user",target_id,f"Acesso atualizado para {saved['name']}")
            except sqlite3.IntegrityError:
                return self.json_response(409,{"error":"Esse nome de usuário já está cadastrado nesta empresa."})
            return self.json_response(200,{"user":saved,"message":"Conta atualizada. As outras sessões foram encerradas."})

        if path == "/api/users/delete":
            if user["role"]!="admin":
                return self.json_response(403,{"error":"Somente administradores da empresa podem excluir contas."})
            target_id=str(data.get("user_id",""))
            if not target_id:
                return self.json_response(400,{"error":"Selecione uma conta para excluir."})
            if target_id==user["id"]:
                return self.json_response(400,{"error":"Você não pode excluir a própria conta."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                target=db.execute("""SELECT m.user_id,m.role,m.active,u.name FROM memberships m JOIN users u ON u.id=m.user_id
                    WHERE m.id=? AND m.organization_id=?""",(target_id,user["organization_id"])).fetchone()
                if not target:
                    return self.json_response(404,{"error":"Conta não encontrada nesta empresa."})
                if target["role"]=="admin" and target["active"]:
                    remaining=db.execute("SELECT COUNT(*) FROM memberships WHERE organization_id=? AND role='admin' AND active=1 AND id<>?",
                                         (user["organization_id"],target_id)).fetchone()[0]
                    if remaining==0:
                        return self.json_response(400,{"error":"Não é possível excluir o último administrador ativo desta empresa."})
                db.execute("DELETE FROM sessions WHERE membership_id=?",(target_id,))
                db.execute("DELETE FROM memberships WHERE id=?",(target_id,))
                db.execute("DELETE FROM users WHERE id=? AND NOT EXISTS (SELECT 1 FROM memberships WHERE user_id=?)",
                           (target["user_id"],target["user_id"]))
                self.log_audit(db,user,"delete","user",target_id,f"Acesso removido para {target['name']}; dados operacionais mantidos")
            return self.json_response(200,{"ok":True})

        if not path.startswith("/api/"):
            return self.json_response(404, {"error": "Endereço não encontrado."})
        kind = path.removeprefix("/api/")
        if kind not in KINDS:
            return self.json_response(404, {"error": "Cadastro não encontrado."})
        role = user["role"]
        writable = {
            "admin": KINDS,
            "supervisor": KINDS,
            "technician": {"orders", "maintenance", "stockRequests", "purchaseRequests"},
            "warehouse": {"stockRequests", "purchaseRequests", "inventoryItems"},
        }
        if kind not in writable.get(role, set()):
            return self.json_response(403, {"error": "Seu perfil não tem permissão para alterar esse cadastro."})
        if kind == "inventoryItems" and role not in {"admin", "warehouse"}:
            return self.json_response(403, {"error": "Somente administradores e almoxarifado podem cadastrar ou ajustar o estoque."})
        if kind == "inventoryItems":
            try:
                saved = self.save_inventory_item(data,user)
                return self.json_response(200, saved)
            except (ValueError, TypeError, OverflowError) as error:
                return self.json_response(400, {"error": str(error)})
            except sqlite3.Error:
                return self.json_response(500, {"error": "Não foi possível atualizar o saldo do estoque."})
        if kind == "stockRequests" and not data.get("id"):
            try:
                if not data.get("items"):
                    return self.json_response(400, {"error": "Escolha os materiais cadastrados no almoxarifado antes de enviar."})
                saved = self.create_stock_request(data, user)
                return self.json_response(201, saved)
            except (ValueError, TypeError, OverflowError) as error:
                return self.json_response(400, {"error": str(error)})
            except sqlite3.Error:
                return self.json_response(500, {"error": "Não foi possível reservar os materiais solicitados."})
        if kind == "stockRequests" and data.get("id"):
            if role not in {"admin", "supervisor", "warehouse"}:
                return self.json_response(403, {"error": "Somente a supervisão, administração ou almoxarifado podem atualizar pedidos."})
            try:
                saved = self.update_stock_request(str(data["id"]), str(data.get("status", "")), user)
                return self.json_response(200, saved)
            except PermissionError as error:
                return self.json_response(403, {"error": str(error)})
            except LookupError as error:
                return self.json_response(404, {"error": str(error)})
            except (ValueError, TypeError, OverflowError) as error:
                return self.json_response(400, {"error": str(error)})
            except sqlite3.Error:
                return self.json_response(500, {"error": "Não foi possível atualizar o pedido de material."})
        try:
            if kind == "orders" and role == "technician":
                if not data.get("id"):
                    return self.json_response(403, {"error": "Ordens novas precisam ser atribuídas pela supervisão."})
                with connect() as db:
                    row = db.execute("SELECT data FROM records WHERE organization_id=? AND kind='orders' AND id=?", (user["organization_id"],data["id"])).fetchone()
                if not row:
                    return self.json_response(404, {"error": "Ordem de serviço não encontrada."})
                saved = json.loads(row[0])
                if saved.get("technician_id") != user["id"]:
                    return self.json_response(403, {"error": "Você só pode atualizar ordens atribuídas à sua conta."})
                for key in ("client_id", "asset_id", "technician_id", "technician", "title", "summary", "number", "created_at"):
                    if key in data and data[key] != saved.get(key):
                        return self.json_response(403, {"error": "Não é permitido alterar os dados de atribuição da ordem."})
                updated = dict(saved)
                for key in ("work", "materials", "status", "signature_name", "signature", "signature_at", "checklist", "photos"):
                    if key in data:
                        updated[key] = data[key]
                data = updated
            elif kind == "orders":
                technician_id = str(data.get("technician_id", ""))
                with connect() as db:
                    technician = db.execute("""SELECT m.id,u.name FROM memberships m JOIN users u ON u.id=m.user_id
                        WHERE m.id=? AND m.organization_id=? AND m.role='technician' AND m.active=1""",
                        (technician_id,user["organization_id"])).fetchone()
                if not technician:
                    return self.json_response(400, {"error": "Atribua a ordem a uma conta ativa de técnico."})
                data["technician_id"] = technician["id"]
                data["technician"] = technician["name"]
                data["assigned_by"] = user["id"]
            if kind == "maintenance" and role == "technician":
                asset_id = str(data.get("asset_id", ""))
                with connect() as db:
                    rows = db.execute("SELECT data FROM records WHERE organization_id=? AND kind='orders'", (user["organization_id"],)).fetchall()
                    asset_row = db.execute("SELECT data FROM records WHERE organization_id=? AND kind='assets' AND id=?", (user["organization_id"],asset_id)).fetchone()
                assigned = any((o := json.loads(row[0])).get("technician_id") == user["id"] and o.get("asset_id") == asset_id for row in rows)
                if not asset_id or not asset_row or not assigned:
                    return self.json_response(403, {"error": "A manutenção só pode ser registrada em equipamentos das suas ordens atribuídas."})
                asset = json.loads(asset_row[0])
                data["client_id"] = asset["client_id"]
                data["equipment"] = asset["name"]
            if kind == "stockRequests" and role == "technician":
                if data.get("id") or data.get("status", "pending") != "pending":
                    return self.json_response(403, {"error": "Técnicos podem criar solicitações; almoxarifado ou supervisão atualizam o andamento."})
                data["requester_id"] = user["id"]
                data["requester"] = user["name"]
            elif kind == "stockRequests" and not data.get("id"):
                data["requester_id"] = user["id"]
                data["requester"] = user["name"]
            if kind == "purchaseRequests":
                if data.get("id") and role == "technician":
                    return self.json_response(403, {"error": "Seu perfil pode criar solicitações, mas não atualizar o andamento."})
                if not data.get("id"):
                    data["status"] = "pending"
                    data["requester_id"] = user["id"]
                    data["requester"] = user["name"]
                elif role in {"admin", "supervisor"}:
                    if data.get("status") not in {"approved", "ordered", "received", "rejected"}:
                        return self.json_response(400, {"error": "Situação de compra inválida."})
                    with connect() as db:
                        current = db.execute("SELECT data FROM records WHERE organization_id=? AND kind='purchaseRequests' AND id=?", (user["organization_id"],data["id"])).fetchone()
                    if not current:
                        return self.json_response(404, {"error": "Solicitação de compra não encontrada."})
                    saved = json.loads(current[0])
                    saved["status"] = data["status"]
                    saved["handled_by"] = user["name"]
                    data = saved
            if kind in {"assets", "orders", "maintenance"}:
                client_id = str(data.get("client_id", ""))
                with connect() as db:
                    client = db.execute("SELECT 1 FROM records WHERE organization_id=? AND kind='clients' AND id=?", (user["organization_id"],client_id)).fetchone()
                    asset_id = str(data.get("asset_id", ""))
                    asset_row = db.execute("SELECT data FROM records WHERE organization_id=? AND kind='assets' AND id=?", (user["organization_id"],asset_id)).fetchone() if asset_id else None
                if not client:
                    return self.json_response(400, {"error": "O cliente selecionado não existe."})
                if kind == "orders" and asset_id and (not asset_row or json.loads(asset_row[0]).get("client_id") != client_id):
                    return self.json_response(400, {"error": "O equipamento precisa pertencer ao cliente selecionado."})
                if kind == "maintenance" and asset_id and (not asset_row or json.loads(asset_row[0]).get("client_id") != client_id):
                    return self.json_response(400, {"error": "O equipamento precisa pertencer ao cliente selecionado."})
            if kind == "assets" and data.get("maintenance_interval_months"):
                tech_id = str(data.get("maintenance_technician_id", ""))
                with connect() as db:
                    tech = db.execute("SELECT 1 FROM memberships WHERE id=? AND organization_id=? AND role='technician' AND active=1",
                                      (tech_id,user["organization_id"])).fetchone()
                if not tech:
                    return self.json_response(400, {"error": "Selecione um técnico ativo para a manutenção recorrente."})
            if role == "warehouse" and kind == "purchaseRequests" and data.get("id"):
                if data.get("status") not in {"ordered", "received"}:
                    return self.json_response(403, {"error": "O almoxarifado pode atualizar pedidos aprovados para realizado ou recebido."})
                with connect() as db:
                    current = db.execute("SELECT data FROM records WHERE organization_id=? AND kind='purchaseRequests' AND id=?", (user["organization_id"],data["id"])).fetchone()
                if not current:
                    return self.json_response(404, {"error": "Solicitação de compra não encontrada."})
                saved = json.loads(current[0])
                allowed_status = {"approved": {"ordered"}, "ordered": {"received"}}
                if data["status"] not in allowed_status.get(saved.get("status"), set()):
                    return self.json_response(403, {"error": "A compra precisa ser aprovada antes do pedido e marcada como realizado antes de recebida."})
                saved["status"] = data["status"]
                saved["handled_by"] = user["name"]
                data = saved
            elif role == "warehouse" and kind == "stockRequests":
                if not data.get("id") or data.get("status") not in {"approved", "separated", "delivered", "rejected"}:
                    return self.json_response(403, {"error": "O almoxarifado só pode atualizar o andamento de uma solicitação existente."})
                with connect() as db:
                    current = db.execute("SELECT data FROM records WHERE organization_id=? AND kind='stockRequests' AND id=?", (user["organization_id"],data["id"])).fetchone()
                if not current:
                    return self.json_response(404, {"error": "Solicitação não encontrada."})
                saved = json.loads(current[0])
                saved["status"] = data["status"]
                saved["handled_by"] = user["name"]
                data = saved
            self.validate_record(kind, data)
            data["id"] = str(data.get("id") or uuid4())
            data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            with connect() as db:
                existing=db.execute("SELECT 1 FROM records WHERE organization_id=? AND kind=? AND id=?",
                                    (user["organization_id"],kind,data["id"])).fetchone()
                db.execute("INSERT OR REPLACE INTO records(organization_id,kind,id,data) VALUES (?,?,?,?)",
                           (user["organization_id"],kind,data["id"],json.dumps(data,ensure_ascii=False,allow_nan=False)))
                if kind == "maintenance" and data.get("asset_id"):
                    row = db.execute("SELECT data FROM records WHERE organization_id=? AND kind='assets' AND id=?", (user["organization_id"],data["asset_id"])).fetchone()
                    if row:
                        asset = json.loads(row[0])
                        asset["last_service"] = data.get("performed") or data.get("due")
                        asset["next_service"] = data.get("due")
                        db.execute("UPDATE records SET data=? WHERE organization_id=? AND kind='assets' AND id=?",
                                   (json.dumps(asset,ensure_ascii=False),user["organization_id"],asset["id"]))
                if kind == "orders" and data.get("status") == "completed" and data.get("preventive_key") and data.get("asset_id"):
                    row = db.execute("SELECT data FROM records WHERE organization_id=? AND kind='assets' AND id=?",
                                     (user["organization_id"],data["asset_id"])).fetchone()
                    if row:
                        asset = json.loads(row[0])
                        interval = asset.get("maintenance_interval_months")
                        if interval:
                            performed = datetime.now(timezone.utc).date()
                            asset["last_service"] = performed.isoformat()
                            asset["next_service"] = add_months(datetime.combine(performed, datetime.min.time()),interval).date().isoformat()
                            db.execute("UPDATE records SET data=? WHERE organization_id=? AND kind='assets' AND id=?",
                                       (json.dumps(asset,ensure_ascii=False),user["organization_id"],asset["id"]))
                self.log_audit(db,user,"update" if existing else "create",kind,data["id"],f"Registro {kind} salvo")
            return self.json_response(200, data)
        except (ValueError, TypeError, AttributeError, OverflowError) as error:
            return self.json_response(400, {"error": str(error) or "Dados inválidos."})
        except sqlite3.Error:
            return self.json_response(500, {"error": "Não foi possível salvar no banco de dados."})

    def login_user(self,membership_id,user_id,organization_id,username,name,role,organization):
        token=secrets.token_urlsafe(32)
        digest=hashlib.sha256(token.encode()).hexdigest()
        expires=datetime.now(timezone.utc)+timedelta(hours=SESSION_HOURS)
        with connect() as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?)",(digest,membership_id,expires.isoformat()))
        secure="; Secure" if self.headers.get("X-Forwarded-Proto")=="https" else ""
        cookie=f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_HOURS*3600}{secure}"
        session_user={"id":membership_id,"account_id":user_id,"username":username,"name":name,"role":role,
                      "organization_name":organization.get("name")}
        return self.json_response(200,{"user":session_user},(("Set-Cookie",cookie),))

    def login_platform_user(self,platform_user):
        token=secrets.token_urlsafe(32)
        digest=hashlib.sha256(token.encode()).hexdigest()
        expires=datetime.now(timezone.utc)+timedelta(hours=SESSION_HOURS)
        with connect() as db:
            db.execute("INSERT INTO platform_sessions VALUES (?,?,?)",
                       (digest,platform_user["id"],expires.isoformat()))
        secure="; Secure" if self.headers.get("X-Forwarded-Proto")=="https" else ""
        cookie=f"{PLATFORM_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_HOURS*3600}{secure}"
        session_user={"id":platform_user["id"],"username":platform_user["username"],
                      "name":platform_user["name"]}
        return self.json_response(200,{"user":session_user},(("Set-Cookie",cookie),))

    def generate_preventive_orders(self, user):
        today = datetime.now(timezone.utc).date().isoformat()
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            assets = db.execute("SELECT id,data FROM records WHERE organization_id=? AND kind='assets'",
                                (user["organization_id"],)).fetchall()
            for row in assets:
                asset = json.loads(row["data"])
                interval = asset.get("maintenance_interval_months")
                due = asset.get("next_service")
                technician_id = asset.get("maintenance_technician_id")
                if not interval or not due or due > today or not technician_id:
                    continue
                key = f"{asset['id']}:{due}"
                if db.execute("SELECT 1 FROM records WHERE organization_id=? AND kind='orders' AND json_extract(data,'$.preventive_key')=?",
                              (user["organization_id"],key)).fetchone():
                    continue
                technician = db.execute("""SELECT m.id,u.name FROM memberships m JOIN users u ON u.id=m.user_id
                    WHERE m.id=? AND m.organization_id=? AND m.role='technician' AND m.active=1""",
                    (technician_id,user["organization_id"])).fetchone()
                if not technician:
                    continue
                client_row = db.execute("SELECT data FROM records WHERE organization_id=? AND kind='clients' AND id=?",
                                        (user["organization_id"],asset.get("client_id"))).fetchone()
                if not client_row:
                    continue
                client = json.loads(client_row[0])
                order_id = str(uuid4())
                now = datetime.now(timezone.utc)
                order = {"id":order_id,"number":f"PREV-{now.year}-{order_id[:6].upper()}",
                         "client_id":asset["client_id"],"technician_id":technician["id"],"technician":technician["name"],
                         "asset_id":asset["id"],"title":f"Manutenção preventiva — {asset['name']}",
                         "scheduled":due,"scheduled_time":"","duration_minutes":60,
                         "summary":f"Manutenção preventiva recorrente para {client.get('name','cliente')}.",
                         "status":"open","work":"","materials":"","signature":"","checklist_type":"preventive",
                         "checklist":[{"label":label,"done":False} for label in
                                       ("Inspecionar funcionamento geral","Verificar conexões e fixações","Testar operação e segurança","Registrar recomendações")],
                         "photos":[],"preventive_key":key,"created_at":now.isoformat()}
                db.execute("INSERT INTO records VALUES (?,?,?,?)",
                           (user["organization_id"],"orders",order_id,json.dumps(order,ensure_ascii=False)))
                self.log_audit(db,user,"create","orders",order_id,f"Ordem preventiva gerada para {asset['name']} ({due})")

    @staticmethod
    def log_audit(db,user,action,kind,record_id,summary):
        db.execute("INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?,?,?)",
                   (str(uuid4()),user["organization_id"],user.get("id"),str(user.get("name","")),
                    action,kind,str(record_id) if record_id is not None else None,str(summary)[:500],
                    datetime.now(timezone.utc).isoformat()))

    @staticmethod
    def reserved_quantity(db,organization_id,inventory_id):
        reserved=0.0
        rows=db.execute("SELECT data FROM records WHERE organization_id=? AND kind='stockRequests'",(organization_id,)).fetchall()
        for row in rows:
            request=json.loads(row[0])
            if request.get("status") in {"pending","approved","separated"}:
                reserved+=sum(float(item.get("quantity",0)) for item in request.get("items",[]) if item.get("item_id")==inventory_id)
        return reserved

    def save_inventory_item(self,item,user):
        self.validate_record("inventoryItems",item)
        organization_id=user["organization_id"]
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            saved=dict(item)
            saved["id"]=str(saved.get("id") or uuid4())
            existing=db.execute("SELECT 1 FROM records WHERE organization_id=? AND kind='inventoryItems' AND id=?",
                                (organization_id,saved["id"])).fetchone()
            reserved=self.reserved_quantity(db,organization_id,saved["id"])
            if float(saved.get("quantity",0))<reserved:
                raise ValueError(f"Há {reserved:g} unidade(s) reservada(s) em solicitações ainda não entregues.")
            saved.setdefault("created_at",datetime.now(timezone.utc).isoformat())
            db.execute("INSERT OR REPLACE INTO records(organization_id,kind,id,data) VALUES (?,?,?,?)",
                       (organization_id,"inventoryItems",saved["id"],json.dumps(saved,ensure_ascii=False,allow_nan=False)))
            self.log_audit(db,user,"update" if existing else "create","inventoryItems",saved["id"],f"Estoque de {saved['name']} atualizado")
            return saved

    def create_stock_request(self,request,user):
        raw_items=request.get("items")
        if not isinstance(raw_items,list) or not raw_items or len(raw_items)>20:
            raise ValueError("Selecione de 1 a 20 materiais cadastrados no almoxarifado.")
        quantities={}
        for item in raw_items:
            if not isinstance(item,dict):
                raise ValueError("Seleção de material inválida.")
            item_id=str(item.get("item_id",""))
            quantity=float(item.get("quantity",0))
            if not item_id or not 0<quantity<=1_000_000:
                raise ValueError("Informe materiais cadastrados e uma quantidade válida.")
            quantities[item_id]=quantities.get(item_id,0)+quantity
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            selected=[]
            for item_id,quantity in quantities.items():
                row=db.execute("SELECT data FROM records WHERE organization_id=? AND kind='inventoryItems' AND id=?",
                               (user["organization_id"],item_id)).fetchone()
                if not row:
                    raise ValueError("Um dos materiais selecionados não existe mais no estoque desta empresa.")
                inventory=json.loads(row[0])
                reserved=self.reserved_quantity(db,user["organization_id"],item_id)
                available=float(inventory.get("quantity",0))-reserved
                if quantity>available:
                    raise ValueError(f"Estoque disponível insuficiente para {inventory['name']}: restam {max(0,available):g} {inventory.get('unit','un')}.")
                selected.append({"item_id":item_id,"name":inventory["name"],"unit":inventory.get("unit","un"),"quantity":quantity})
            total=sum(item["quantity"] for item in selected)
            saved={"id":str(uuid4()),"items":selected,"item":f"Solicitação com {len(selected)} {('item' if len(selected)==1 else 'itens')}",
                   "quantity":total,"priority":str(request.get("priority","Normal")),"needed_for":str(request.get("needed_for",""))[:180],
                   "notes":str(request.get("notes",""))[:3000],"status":"pending","requester_id":user["id"],
                   "requester":user["name"],"created_at":datetime.now(timezone.utc).isoformat()}
            self.validate_record("stockRequests",saved)
            db.execute("INSERT INTO records(organization_id,kind,id,data) VALUES (?,?,?,?)",
                       (user["organization_id"],"stockRequests",saved["id"],json.dumps(saved,ensure_ascii=False,allow_nan=False)))
            self.log_audit(db,user,"create","stockRequests",saved["id"],"Solicitação criada e saldo reservado")
            return saved

    def update_stock_request(self,request_id,status,user):
        valid={"approved","separated","delivered","rejected"}
        if status not in valid:
            raise ValueError("Situação do pedido inválida.")
        organization_id=user["organization_id"]
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT data FROM records WHERE organization_id=? AND kind='stockRequests' AND id=?",
                           (organization_id,request_id)).fetchone()
            if not row:
                raise LookupError("Solicitação não encontrada nesta empresa.")
            saved=json.loads(row[0])
            if saved.get("items"):
                transitions={"pending":{"approved","rejected"},"approved":{"separated","rejected"},"separated":{"delivered","rejected"}}
                if status not in transitions.get(saved.get("status"),set()):
                    raise PermissionError("Siga o fluxo: aprovar, separar e depois entregar; também é possível recusar antes da entrega.")
                if status=="delivered":
                    for item in saved["items"]:
                        item_row=db.execute("SELECT data FROM records WHERE organization_id=? AND kind='inventoryItems' AND id=?",
                                            (organization_id,item["item_id"])).fetchone()
                        if not item_row:
                            raise ValueError(f"O material {item['name']} não existe mais no estoque.")
                        inventory=json.loads(item_row[0])
                        quantity=float(inventory.get("quantity",0))-float(item["quantity"])
                        if quantity < -1e-9:
                            raise ValueError(f"Estoque físico insuficiente para entregar {item['name']}.")
                        inventory["quantity"]=max(0,quantity)
                        db.execute("UPDATE records SET data=? WHERE organization_id=? AND kind='inventoryItems' AND id=?",
                                   (json.dumps(inventory,ensure_ascii=False),organization_id,inventory["id"]))
            saved["status"]=status
            saved["handled_by"]=user["name"]
            db.execute("UPDATE records SET data=? WHERE organization_id=? AND kind='stockRequests' AND id=?",
                       (json.dumps(saved,ensure_ascii=False),organization_id,request_id))
            self.log_audit(db,user,"update","stockRequests",request_id,f"Solicitação de materiais: {status}")
            return saved

    @staticmethod
    def validate_record(kind, data):
        if kind == "clients" and not str(data.get("name", "")).strip():
            raise ValueError("Informe o nome do cliente.")
        if kind == "assets":
            if not data.get("client_id") or not str(data.get("name", "")).strip():
                raise ValueError("Selecione o cliente e informe o equipamento.")
            for key in ("last_service", "next_service"):
                if data.get(key):
                    datetime.strptime(data[key], "%Y-%m-%d")
            interval = data.get("maintenance_interval_months")
            if interval not in (None, ""):
                numeric_interval = float(interval)
                interval = int(numeric_interval)
                if numeric_interval != interval or not 1 <= interval <= 36 or not data.get("next_service") or not data.get("maintenance_technician_id"):
                    raise ValueError("Para ativar a recorrência, informe próxima data, intervalo de 1 a 36 meses e técnico responsável.")
                data["maintenance_interval_months"] = interval
        if kind == "orders":
            if not data.get("client_id"):
                raise ValueError("Selecione o cliente da ordem de serviço.")
            if not str(data.get("title", "")).strip() or len(str(data.get("title", ""))) > 180:
                raise ValueError("Informe uma descrição de serviço de até 180 caracteres.")
            if data.get("status", "open") not in {"open", "in_progress", "completed"}:
                raise ValueError("Situação da ordem de serviço inválida.")
            if data.get("signature") and not data.get("signature_name"):
                raise ValueError("Informe o nome de quem assinou.")
            if data.get("status") == "completed" and (not data.get("signature", "").startswith("data:image/png;base64,") or not data.get("signature_name")):
                raise ValueError("Uma ordem concluída precisa da assinatura PNG do cliente e do nome de quem assinou.")
            for key in ("scheduled",):
                if data.get(key):
                    datetime.strptime(data[key], "%Y-%m-%d")
            if data.get("scheduled_time"):
                datetime.strptime(data["scheduled_time"], "%H:%M")
            if data.get("duration_minutes") not in (None, "") and not 15 <= int(data["duration_minutes"]) <= 1440:
                raise ValueError("A duração do atendimento deve ficar entre 15 minutos e 24 horas.")
            checklist = data.get("checklist", [])
            if not isinstance(checklist, list) or len(checklist) > 30 or any(not isinstance(item,dict) or not str(item.get("label","" )).strip() or len(str(item.get("label","")))>180 for item in checklist):
                raise ValueError("A lista de verificação desta ordem está inválida.")
            photos = data.get("photos", [])
            if not isinstance(photos,list) or len(photos)>3:
                raise ValueError("Anexe no máximo três fotos à ordem.")
            signatures = {"image/png":b"\x89PNG\r\n\x1a\n","image/jpeg":b"\xff\xd8\xff","image/webp":None}
            for photo in photos:
                if not isinstance(photo,dict) or photo.get("mime") not in signatures or len(str(photo.get("name","")))>180:
                    raise ValueError("Formato de foto inválido. Use PNG, JPEG ou WebP.")
                prefix=f"data:{photo['mime']};base64,"
                source=str(photo.get("data",""))
                if not source.startswith(prefix):
                    raise ValueError("A foto não corresponde ao formato informado.")
                try:
                    payload=base64.b64decode(source[len(prefix):],validate=True)
                except ValueError:
                    raise ValueError("Uma das fotos está corrompida.") from None
                if not 0<len(payload)<=750_000:
                    raise ValueError("Cada foto da ordem deve ter no máximo 750 KB.")
                magic=signatures[photo["mime"]]
                if magic and not payload.startswith(magic):
                    raise ValueError("O conteúdo da foto não corresponde ao formato informado.")
                if photo["mime"]=="image/webp" and (len(payload)<12 or payload[:4]!=b"RIFF" or payload[8:12]!=b"WEBP"):
                    raise ValueError("O conteúdo da foto não corresponde ao formato WebP.")
        if kind == "maintenance":
            datetime.strptime(data.get("due", ""), "%Y-%m-%d")
            if not data.get("client_id") or not data.get("equipment"):
                raise ValueError("Informe cliente e equipamento para a manutenção.")
        if kind == "stockRequests":
            quantity = float(data.get("quantity", 0))
            if not str(data.get("item", "")).strip() or not 0 < quantity <= 1_000_000:
                raise ValueError("Informe o material e uma quantidade válida.")
            if data.get("status", "pending") not in {"pending", "approved", "separated", "delivered", "rejected"}:
                raise ValueError("Status do pedido inválido.")
            items = data.get("items")
            if not data.get("id") and not items:
                raise ValueError("Selecione materiais cadastrados no almoxarifado.")
            if items is not None:
                if not isinstance(items, list) or not items or len(items) > 20:
                    raise ValueError("O pedido deve conter de 1 a 20 materiais.")
                for item in items:
                    if not isinstance(item, dict) or not item.get("item_id") or not str(item.get("name", "")).strip():
                        raise ValueError("Um dos materiais do pedido é inválido.")
                    amount = float(item.get("quantity", 0))
                    if not math.isfinite(amount) or not 0 < amount <= 1_000_000:
                        raise ValueError("A quantidade de um dos materiais é inválida.")
        if kind == "inventoryItems":
            if not str(data.get("name", "")).strip() or len(str(data.get("name", ""))) > 180:
                raise ValueError("Informe o nome do material.")
            quantity = float(data.get("quantity", 0))
            minimum = float(data.get("minimum_quantity", 0) or 0)
            if not math.isfinite(quantity) or quantity < 0 or quantity > 1_000_000_000:
                raise ValueError("Informe uma quantidade física válida.")
            if not math.isfinite(minimum) or minimum < 0 or minimum > 1_000_000_000:
                raise ValueError("Informe uma quantidade mínima válida.")
            if not str(data.get("unit", "")).strip() or len(str(data.get("unit", ""))) > 24:
                raise ValueError("Informe a unidade do material, como un, m ou caixa.")
        if kind == "purchaseRequests":
            if not str(data.get("item", "")).strip() or len(str(data.get("item", ""))) > 180:
                raise ValueError("Informe o material ou ferramenta que deseja comprar.")
            quantity = float(data.get("quantity", 0))
            if not 0 < quantity <= 1_000_000:
                raise ValueError("Informe uma quantidade válida.")
            if data.get("estimated_cost") not in (None, "") and not 0 <= float(data["estimated_cost"]) <= 1_000_000_000:
                raise ValueError("O valor estimado é inválido.")
            if data.get("url") and not str(data["url"]).startswith(("https://", "http://")):
                raise ValueError("O link de compra deve começar com https:// ou http://.")
            if data.get("status", "pending") not in {"pending", "approved", "ordered", "received", "rejected"}:
                raise ValueError("Situação da compra inválida.")
            images = data.get("images", [])
            if not isinstance(images, list) or len(images) > 3:
                raise ValueError("Anexe no máximo três fotos.")
            signatures = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": None}
            for image in images:
                if not isinstance(image, dict) or image.get("mime") not in signatures or len(str(image.get("name", ""))) > 180:
                    raise ValueError("Formato de foto inválido. Use PNG, JPEG ou WebP.")
                data_url = str(image.get("data", ""))
                prefix = f"data:{image['mime']};base64,"
                if not data_url.startswith(prefix):
                    raise ValueError("Os dados da foto não correspondem ao formato informado.")
                try:
                    payload = base64.b64decode(data_url[len(prefix):], validate=True)
                except ValueError:
                    raise ValueError("Uma das fotos está corrompida.") from None
                if not 0 < len(payload) <= 1_000_000:
                    raise ValueError("Cada foto deve ter no máximo 1 MB.")
                magic = signatures[image["mime"]]
                if magic and not payload.startswith(magic):
                    raise ValueError("O conteúdo de uma foto não corresponde ao seu formato.")
                if image["mime"] == "image/webp" and (len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WEBP"):
                    raise ValueError("O conteúdo da foto não corresponde ao formato WebP.")
        if kind == "manuals":
            if not str(data.get("title", "")).strip():
                raise ValueError("Informe o título do manual.")
            url = str(data.get("url", ""))
            if url and not url.startswith(("https://", "http://")):
                raise ValueError("O endereço do manual deve começar com https:// ou http://.")
        if kind == "products":
            if not str(data.get("name", "")).strip() or float(data.get("price", -1)) < 0:
                raise ValueError("Informe o nome e um preço válido.")

    def log_message(self, fmt, *args):
        # Avoid logging request bodies or credentials; request paths contain no secrets.
        super().log_message(fmt, *args)


if __name__ == "__main__":
    if sys.argv[1:] == ["--create-platform-admin"]:
        try:
            setup_platform_admin_interactive()
        except (ValueError,RuntimeError,sqlite3.Error) as error:
            print(f"Não foi possível criar o login comercial: {error}",file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(0)
    if sys.argv[1:]:
        print("Uso: python server.py [--create-platform-admin]", file=sys.stderr)
        raise SystemExit(2)
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE expires<?", (datetime.now(timezone.utc).isoformat(),))
    port = int(os.environ.get("PORT", "8000"))
    print(f"Segura Gestão disponível em http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

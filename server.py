"""Segura Gestão: app local de operações para segurança eletrônica."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("CFTV_DB", str(ROOT / "data" / "gestao.db")))
COOKIE = "segura_session"
ROLES = {"admin", "supervisor", "technician", "warehouse"}
KINDS = {"clients", "assets", "orders", "maintenance", "stockRequests", "manuals", "products", "quotes", "company"}
SESSION_HOURS = 12
PBKDF2_ROUNDS = 310_000


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(kind,id))")
    db.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
        role TEXT NOT NULL, salt BLOB NOT NULL, password_hash BLOB NOT NULL,
        active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL)""")
    db.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires TEXT NOT NULL)")
    db.commit()
    return db


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)


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
        with connect() as db:
            query = "SELECT u.id,u.username,u.name,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.active=1"
            row = db.execute(query, (digest, datetime.now(timezone.utc).isoformat())).fetchone()
        return dict(row) if row else None

    def require_user(self):
        user = self.current_user()
        if not user:
            self.json_response(401, {"error": "Sua sessão terminou. Entre novamente."})
            return None
        return user

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/session":
            with connect() as db:
                has_users = db.execute("SELECT EXISTS(SELECT 1 FROM users)").fetchone()[0]
            return self.json_response(200, {"setupRequired": not bool(has_users), "user": self.current_user()})
        if path == "/api/data":
            user = self.require_user()
            if not user:
                return
            with connect() as db:
                result = {key: [] for key in KINDS}
                if user["role"] == "warehouse":
                    allowed = {"stockRequests", "products"}
                else:
                    allowed = KINDS
                for kind, data in db.execute("SELECT kind,data FROM records ORDER BY rowid"):
                    if kind in allowed:
                        result[kind].append(json.loads(data))
                if user["role"] == "admin":
                    result["users"] = [dict(id=r[0], username=r[1], name=r[2], role=r[3], active=bool(r[4]))
                                       for r in db.execute("SELECT id,username,name,role,active FROM users ORDER BY name")]
            return self.json_response(200, result)
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
            if not username or len(username) > 80 or not name or len(name) > 150 or len(password) < 12:
                return self.json_response(400, {"error": "Informe usuário e nome; a senha inicial precisa ter pelo menos 12 caracteres."})
            salt = secrets.token_bytes(16)
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT EXISTS(SELECT 1 FROM users)").fetchone()[0]:
                    return self.json_response(409, {"error": "O administrador inicial já foi criado."})
                user_id = str(uuid4())
                db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,1,?)",
                           (user_id, username, name, "admin", salt, password_hash(password, salt), datetime.now(timezone.utc).isoformat()))
            return self.login_user(user_id, username, name, "admin")

        if path == "/api/login":
            username = str(data.get("username", "")).strip().lower()
            password = str(data.get("password", ""))
            with connect() as db:
                row = db.execute("SELECT id,username,name,role,salt,password_hash,active FROM users WHERE username=?", (username,)).fetchone()
            if not row or not row[6] or not hmac.compare_digest(password_hash(password, row[4]), row[5]):
                return self.json_response(401, {"error": "Usuário ou senha incorretos."})
            return self.login_user(row[0], row[1], row[2], row[3])

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

        user = self.require_user()
        if not user:
            return
        if path == "/api/users":
            if user["role"] != "admin":
                return self.json_response(403, {"error": "Somente administradores podem gerenciar acessos."})
            username, name = str(data.get("username", "")).strip().lower(), str(data.get("name", "")).strip()
            password, role = str(data.get("password", "")), str(data.get("role", ""))
            if not username or not name or len(password) < 12 or role not in ROLES:
                return self.json_response(400, {"error": "Informe usuário, nome, perfil válido e senha com pelo menos 12 caracteres."})
            salt, user_id = secrets.token_bytes(16), str(uuid4())
            try:
                with connect() as db:
                    db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,1,?)",
                               (user_id, username, name, role, salt, password_hash(password, salt), datetime.now(timezone.utc).isoformat()))
            except sqlite3.IntegrityError:
                return self.json_response(409, {"error": "Esse nome de usuário já está cadastrado."})
            return self.json_response(201, {"id": user_id, "username": username, "name": name, "role": role, "active": True})

        if not path.startswith("/api/"):
            return self.json_response(404, {"error": "Endereço não encontrado."})
        kind = path.removeprefix("/api/")
        if kind not in KINDS:
            return self.json_response(404, {"error": "Cadastro não encontrado."})
        role = user["role"]
        if role == "warehouse" and kind != "stockRequests":
            return self.json_response(403, {"error": "Este perfil não pode alterar esse cadastro."})
        if kind == "stockRequests":
            if role not in {"admin", "supervisor", "technician", "warehouse"}:
                return self.json_response(403, {"error": "Este perfil não pode solicitar material."})
            if role == "technician" and data.get("status", "pending") != "pending":
                return self.json_response(403, {"error": "Somente o almoxarifado e a supervisão podem atualizar o pedido."})
        elif role == "technician" and kind not in {"orders", "maintenance"}:
            return self.json_response(403, {"error": "Este perfil não pode alterar esse cadastro."})
        elif role == "supervisor" and kind == "users":
            return self.json_response(403, {"error": "Somente administradores podem gerenciar acessos."})
        try:
            if role == "warehouse":
                if not data.get("id") or data.get("status") not in {"approved", "separated", "delivered", "rejected"}:
                    return self.json_response(403, {"error": "O almoxarifado só pode atualizar o andamento de uma solicitação existente."})
                with connect() as db:
                    current = db.execute("SELECT data FROM records WHERE kind='stockRequests' AND id=?", (data["id"],)).fetchone()
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
                db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)",
                           (kind, data["id"], json.dumps(data, ensure_ascii=False, allow_nan=False)))
                if kind == "maintenance" and data.get("asset_id"):
                    row = db.execute("SELECT data FROM records WHERE kind='assets' AND id=?", (data["asset_id"],)).fetchone()
                    if row:
                        asset = json.loads(row[0])
                        asset["last_service"] = data.get("performed") or data.get("due")
                        asset["next_service"] = data.get("due")
                        db.execute("UPDATE records SET data=? WHERE kind='assets' AND id=?",
                                   (json.dumps(asset, ensure_ascii=False), asset["id"]))
            return self.json_response(200, data)
        except (ValueError, TypeError, AttributeError, OverflowError) as error:
            return self.json_response(400, {"error": str(error) or "Dados inválidos."})
        except sqlite3.Error:
            return self.json_response(500, {"error": "Não foi possível salvar no banco de dados."})

    def login_user(self, user_id, username, name, role):
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
        with connect() as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (digest, user_id, expires.isoformat()))
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        cookie = f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_HOURS * 3600}{secure}"
        return self.json_response(200, {"user": {"id": user_id, "username": username, "name": name, "role": role}}, (("Set-Cookie", cookie),))

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
        if kind == "orders":
            if not data.get("client_id"):
                raise ValueError("Selecione o cliente da ordem de serviço.")
            if data.get("signature") and not data.get("signature_name"):
                raise ValueError("Informe o nome de quem assinou.")
            if data.get("status") == "completed" and (not data.get("signature", "").startswith("data:image/png;base64,") or not data.get("signature_name")):
                raise ValueError("Uma ordem concluída precisa da assinatura PNG do cliente e do nome de quem assinou.")
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
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE expires<?", (datetime.now(timezone.utc).isoformat(),))
    port = int(os.environ.get("PORT", "8000"))
    print(f"Segura Gestão disponível em http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

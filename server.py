"""Segura Gestão: app local de operações para segurança eletrônica."""
import base64
import hashlib
import hmac
import json
import math
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
KINDS = {"clients", "assets", "orders", "maintenance", "stockRequests", "purchaseRequests", "inventoryItems", "manuals", "products", "quotes", "company"}
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
                records = {key: [] for key in KINDS}
                for kind, data in db.execute("SELECT kind,data FROM records ORDER BY rowid"):
                    if kind in records:
                        records[kind].append(json.loads(data))
                reserved = {}
                for request in records["stockRequests"]:
                    if request.get("status") in {"pending", "approved", "separated"}:
                        for item in request.get("items", []):
                            item_id = item.get("item_id")
                            reserved[item_id] = reserved.get(item_id, 0) + float(item.get("quantity", 0))
                records["inventoryItems"] = [
                    {**item,
                     "reserved_quantity": reserved.get(item["id"], 0),
                     "available_quantity": max(0, float(item.get("quantity", 0)) - reserved.get(item["id"], 0))}
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
                if user["role"] == "admin":
                    result["users"] = [dict(id=r[0], username=r[1], name=r[2], role=r[3], active=bool(r[4]))
                                       for r in db.execute("SELECT id,username,name,role,active FROM users ORDER BY name")]
                if user["role"] in {"admin", "supervisor"}:
                    result["team"] = [dict(id=r[0], name=r[1], role=r[2])
                                      for r in db.execute("SELECT id,name,role FROM users WHERE active=1 AND role='technician' ORDER BY name")]
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

        if path == "/api/users/reset-password":
            if user["role"] != "admin":
                return self.json_response(403, {"error": "Somente administradores podem redefinir senhas."})
            target_id = str(data.get("user_id", ""))
            password = str(data.get("password", ""))
            if not target_id or len(password) < 12 or len(password) > 1024:
                return self.json_response(400, {"error": "Informe uma nova senha com pelo menos 12 caracteres."})
            salt = secrets.token_bytes(16)
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            current_token = cookie[COOKIE].value if COOKIE in cookie else ""
            current_digest = hashlib.sha256(current_token.encode()).hexdigest()
            with connect() as db:
                changed = db.execute("UPDATE users SET salt=?,password_hash=? WHERE id=?",
                                     (salt, password_hash(password, salt), target_id)).rowcount
                if not changed:
                    return self.json_response(404, {"error": "Conta não encontrada."})
                db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (target_id, current_digest))
            return self.json_response(200, {"ok": True, "message": "Senha redefinida. A pessoa precisará entrar novamente."})

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
                saved = self.save_inventory_item(data)
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
                    row = db.execute("SELECT data FROM records WHERE kind='orders' AND id=?", (data["id"],)).fetchone()
                if not row:
                    return self.json_response(404, {"error": "Ordem de serviço não encontrada."})
                saved = json.loads(row[0])
                if saved.get("technician_id") != user["id"]:
                    return self.json_response(403, {"error": "Você só pode atualizar ordens atribuídas à sua conta."})
                for key in ("client_id", "asset_id", "technician_id", "technician", "title", "summary", "number", "created_at"):
                    if key in data and data[key] != saved.get(key):
                        return self.json_response(403, {"error": "Não é permitido alterar os dados de atribuição da ordem."})
                updated = dict(saved)
                for key in ("work", "materials", "status", "signature_name", "signature", "signature_at"):
                    if key in data:
                        updated[key] = data[key]
                data = updated
            elif kind == "orders":
                technician_id = str(data.get("technician_id", ""))
                with connect() as db:
                    technician = db.execute("SELECT id,name FROM users WHERE id=? AND role='technician' AND active=1", (technician_id,)).fetchone()
                if not technician:
                    return self.json_response(400, {"error": "Atribua a ordem a uma conta ativa de técnico."})
                data["technician_id"] = technician["id"]
                data["technician"] = technician["name"]
                data["assigned_by"] = user["id"]
            if kind == "maintenance" and role == "technician":
                asset_id = str(data.get("asset_id", ""))
                with connect() as db:
                    rows = db.execute("SELECT data FROM records WHERE kind='orders'").fetchall()
                    asset_row = db.execute("SELECT data FROM records WHERE kind='assets' AND id=?", (asset_id,)).fetchone()
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
                        current = db.execute("SELECT data FROM records WHERE kind='purchaseRequests' AND id=?", (data["id"],)).fetchone()
                    if not current:
                        return self.json_response(404, {"error": "Solicitação de compra não encontrada."})
                    saved = json.loads(current[0])
                    saved["status"] = data["status"]
                    saved["handled_by"] = user["name"]
                    data = saved
            if kind in {"assets", "orders", "maintenance"}:
                client_id = str(data.get("client_id", ""))
                with connect() as db:
                    client = db.execute("SELECT 1 FROM records WHERE kind='clients' AND id=?", (client_id,)).fetchone()
                    asset_id = str(data.get("asset_id", ""))
                    asset_row = db.execute("SELECT data FROM records WHERE kind='assets' AND id=?", (asset_id,)).fetchone() if asset_id else None
                if not client:
                    return self.json_response(400, {"error": "O cliente selecionado não existe."})
                if kind == "orders" and asset_id and (not asset_row or json.loads(asset_row[0]).get("client_id") != client_id):
                    return self.json_response(400, {"error": "O equipamento precisa pertencer ao cliente selecionado."})
                if kind == "maintenance" and asset_id and (not asset_row or json.loads(asset_row[0]).get("client_id") != client_id):
                    return self.json_response(400, {"error": "O equipamento precisa pertencer ao cliente selecionado."})
            if role == "warehouse" and kind == "purchaseRequests" and data.get("id"):
                if data.get("status") not in {"ordered", "received"}:
                    return self.json_response(403, {"error": "O almoxarifado pode atualizar pedidos aprovados para realizado ou recebido."})
                with connect() as db:
                    current = db.execute("SELECT data FROM records WHERE kind='purchaseRequests' AND id=?", (data["id"],)).fetchone()
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
    def reserved_quantity(db, inventory_id):
        reserved = 0.0
        rows = db.execute("SELECT data FROM records WHERE kind='stockRequests'").fetchall()
        for row in rows:
            request = json.loads(row[0])
            if request.get("status") in {"pending", "approved", "separated"}:
                reserved += sum(float(item.get("quantity", 0)) for item in request.get("items", [])
                                if item.get("item_id") == inventory_id)
        return reserved

    def save_inventory_item(self, item):
        self.validate_record("inventoryItems", item)
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            saved = dict(item)
            saved["id"] = str(saved.get("id") or uuid4())
            reserved = self.reserved_quantity(db, saved["id"])
            if float(saved.get("quantity", 0)) < reserved:
                raise ValueError(f"Há {reserved:g} unidade(s) reservada(s) em solicitações ainda não entregues.")
            saved.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)",
                       ("inventoryItems", saved["id"], json.dumps(saved, ensure_ascii=False, allow_nan=False)))
            return saved

    def create_stock_request(self, request, user):
        raw_items = request.get("items")
        if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 20:
            raise ValueError("Selecione de 1 a 20 materiais cadastrados no almoxarifado.")
        quantities = {}
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValueError("Seleção de material inválida.")
            item_id = str(item.get("item_id", ""))
            quantity = float(item.get("quantity", 0))
            if not item_id or not 0 < quantity <= 1_000_000:
                raise ValueError("Informe materiais cadastrados e uma quantidade válida.")
            quantities[item_id] = quantities.get(item_id, 0) + quantity

        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            selected = []
            for item_id, quantity in quantities.items():
                row = db.execute("SELECT data FROM records WHERE kind='inventoryItems' AND id=?", (item_id,)).fetchone()
                if not row:
                    raise ValueError("Um dos materiais selecionados não existe mais no almoxarifado.")
                inventory = json.loads(row[0])
                reserved = self.reserved_quantity(db, item_id)
                available = float(inventory.get("quantity", 0)) - reserved
                if quantity > available:
                    raise ValueError(f"Estoque disponível insuficiente para {inventory['name']}: restam {max(0, available):g} {inventory.get('unit', 'un')}.")
                selected.append({"item_id": item_id, "name": inventory["name"],
                                 "unit": inventory.get("unit", "un"), "quantity": quantity})
            total = sum(item["quantity"] for item in selected)
            data = {
                "id": str(uuid4()), "items": selected,
                "item": f"Solicitação com {len(selected)} {('item' if len(selected) == 1 else 'itens')}",
                "quantity": total, "priority": str(request.get("priority", "Normal")),
                "needed_for": str(request.get("needed_for", ""))[:180],
                "notes": str(request.get("notes", ""))[:3000], "status": "pending",
                "requester_id": user["id"], "requester": user["name"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self.validate_record("stockRequests", data)
            db.execute("INSERT INTO records VALUES (?,?,?)",
                       ("stockRequests", data["id"], json.dumps(data, ensure_ascii=False, allow_nan=False)))
            return data

    def update_stock_request(self, request_id, status, user):
        valid = {"approved", "separated", "delivered", "rejected"}
        if status not in valid:
            raise ValueError("Situação do pedido inválida.")
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM records WHERE kind='stockRequests' AND id=?", (request_id,)).fetchone()
            if not row:
                raise LookupError("Solicitação não encontrada.")
            saved = json.loads(row[0])
            if saved.get("items"):
                transitions = {"pending": {"approved", "rejected"},
                               "approved": {"separated", "rejected"},
                               "separated": {"delivered", "rejected"}}
                if status not in transitions.get(saved.get("status"), set()):
                    raise PermissionError("Siga o fluxo: aprovar, separar e depois entregar; também é possível recusar antes da entrega.")
                if status == "delivered":
                    for item in saved["items"]:
                        item_row = db.execute("SELECT data FROM records WHERE kind='inventoryItems' AND id=?", (item["item_id"],)).fetchone()
                        if not item_row:
                            raise ValueError(f"O material {item['name']} não existe mais no estoque.")
                        inventory = json.loads(item_row[0])
                        quantity = float(inventory.get("quantity", 0)) - float(item["quantity"])
                        if quantity < -1e-9:
                            raise ValueError(f"Estoque físico insuficiente para entregar {item['name']}.")
                        inventory["quantity"] = max(0, quantity)
                        db.execute("UPDATE records SET data=? WHERE kind='inventoryItems' AND id=?",
                                   (json.dumps(inventory, ensure_ascii=False), inventory["id"]))
            elif user["role"] == "warehouse" and status not in {"approved", "separated", "delivered", "rejected"}:
                raise PermissionError("O almoxarifado não pode atualizar essa situação.")
            saved["status"] = status
            saved["handled_by"] = user["name"]
            db.execute("UPDATE records SET data=? WHERE kind='stockRequests' AND id=?",
                       (json.dumps(saved, ensure_ascii=False), request_id))
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
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE expires<?", (datetime.now(timezone.utc).isoformat(),))
    port = int(os.environ.get("PORT", "8000"))
    print(f"Segura Gestão disponível em http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

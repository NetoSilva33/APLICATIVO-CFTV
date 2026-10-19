"""Segura Gestão: servidor local sem dependências externas. Python 3.10+."""
import json
import os
import sqlite3
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('CFTV_DB', str(ROOT / 'data' / 'gestao.db')))
COLLECTIONS = {'products', 'clients', 'quotes', 'maintenance', 'company'}

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(kind,id))')
    return db

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'static'), **kwargs)

    def send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/data':
            with connect() as db:
                result = {key: [] for key in COLLECTIONS}
                for kind, _, data in db.execute('SELECT kind,id,data FROM records ORDER BY rowid'):
                    result[kind].append(json.loads(data))
            return self.send_json(200, result)
        if path.startswith('/api/'):
            return self.send_json(404, {'error': 'Endereço não encontrado.'})
        return super().do_GET()

    def do_POST(self):
        # Bloqueia gravações iniciadas por páginas de outra origem.
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.send_json(403, {'error': 'Origem não permitida.'})
        parts = urlparse(self.path).path.strip('/').split('/')
        if len(parts) != 2 or parts[0] != 'api' or parts[1] not in COLLECTIONS:
            return self.send_json(404, {'error': 'Cadastro não encontrado.'})
        kind = parts[1]
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 3_000_000:
                raise ValueError('Arquivo muito grande ou vazio.')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('Dados inválidos.')
            if kind in {'products', 'clients', 'company'} and not str(data.get('name', '')).strip():
                raise ValueError('Informe o nome.')
            if kind == 'products' and (not isinstance(data.get('price'), (int, float)) or not 0 <= data['price'] <= 1e9):
                raise ValueError('Preço inválido.')
            if kind == 'quotes' and (not data.get('client', {}).get('name') or not data.get('items')):
                raise ValueError('Selecione um cliente e inclua itens.')
            if kind == 'maintenance':
                from datetime import date
                date.fromisoformat(data.get('due', ''))
                if not data.get('client') or not data.get('equipment'):
                    raise ValueError('Informe cliente e equipamento.')
            data['id'] = 'company' if kind == 'company' else str(data.get('id') or uuid4())
            with connect() as db:
                db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (kind, data['id'], json.dumps(data, ensure_ascii=False, allow_nan=False)))
            self.send_json(200, data)
        except (ValueError, TypeError, AttributeError) as error:
            self.send_json(400, {'error': str(error)})
        except sqlite3.Error:
            self.send_json(500, {'error': 'Não foi possível salvar no banco de dados.'})

if __name__ == '__main__':
    connect().close()
    port = int(os.environ.get('PORT', '8000'))
    print(f'Segura Gestão disponível em http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()

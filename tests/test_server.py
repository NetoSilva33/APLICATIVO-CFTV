import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor
import server

class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        server.DB = Path(cls.temp.name) / 'test.db'
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.base = 'http://127.0.0.1:' + str(cls.http.server_port)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.client = build_opener(HTTPCookieProcessor(CookieJar()))
        setup = Request(cls.base + '/api/setup', json.dumps({'name':'Admin','username':'admin','password':'test-password-123'}).encode(), {'Content-Type':'application/json'})
        with cls.client.open(setup) as response:
            assert response.status == 200
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.temp.cleanup()
    def post(self, kind, data, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin: headers['Origin'] = origin
        req = Request(self.base + '/api/' + kind, json.dumps(data).encode(), headers)
        with self.client.open(req) as response: return json.load(response)
    def test_persistence_and_update(self):
        p = self.post('products', {'name': 'Câmera', 'price': 120})
        p['price'] = 150
        self.post('products', p)
        with urlopen(self.base + '/api/data') as response: data = json.load(response)
        self.assertEqual(next(x for x in data['products'] if x['id'] == p['id'])['price'], 150)
    def test_invalid_price(self):
        with self.assertRaises(HTTPError) as e: self.post('products', {'name':'Câmera', 'price':-1})
        self.assertEqual(e.exception.code, 400)
    def test_invalid_due_date(self):
        with self.assertRaises(HTTPError) as e: self.post('maintenance', {'client':'a','equipment':'Motor','due':'2026-02-30'})
        self.assertEqual(e.exception.code, 400)
    def test_cross_origin_blocked(self):
        with self.assertRaises(HTTPError) as e: self.post('clients', {'name':'Cliente'}, 'https://example.org')
        self.assertEqual(e.exception.code, 403)
    def test_quote_snapshot(self):
        company = self.post('company', {'name':'Empresa original'})
        q = self.post('quotes', {'client':{'name':'Cliente'},'company':company,'items':[{'price':100,'qty':2}]})
        self.post('company', {'name':'Empresa alterada'})
        with urlopen(self.base + '/api/data') as response: data = json.load(response)
        self.assertEqual(next(x for x in data['quotes'] if x['id']==q['id'])['company']['name'], 'Empresa original')
    def test_home(self):
        with urlopen(self.base) as response: self.assertIn('Segura Gestão',response.read().decode())

if __name__ == '__main__': unittest.main()

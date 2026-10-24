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
    def get(self, path, client=None):
        with (client or self.client).open(self.base + path) as response: return json.load(response)
    def login(self, client, username, password):
        req = Request(self.base + '/api/login', json.dumps({'username':username,'password':password}).encode(), {'Content-Type':'application/json'})
        with client.open(req) as response: return json.load(response)
    def test_persistence_and_update(self):
        p = self.post('products', {'name': 'Câmera', 'price': 120})
        p['price'] = 150
        self.post('products', p)
        data = self.get('/api/data')
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
        data = self.get('/api/data')
        self.assertEqual(next(x for x in data['quotes'] if x['id']==q['id'])['company']['name'], 'Empresa original')
    def test_inventory_request_reserves_and_deducts_stock(self):
        cable = self.post('inventoryItems', {'name':'Cabo de teste','category':'Cabeamento','unit':'m','quantity':10,'minimum_quantity':2})
        camera = self.post('inventoryItems', {'name':'Câmera de teste','category':'CFTV','unit':'un','quantity':4,'minimum_quantity':1})
        request = self.post('stockRequests', {'items':[{'item_id':cable['id'],'quantity':4},{'item_id':camera['id'],'quantity':2}], 'priority':'Normal'})
        data = self.get('/api/data')
        cable_row = next(item for item in data['inventoryItems'] if item['id']==cable['id'])
        camera_row = next(item for item in data['inventoryItems'] if item['id']==camera['id'])
        self.assertEqual(cable_row['reserved_quantity'], 4)
        self.assertEqual(cable_row['available_quantity'], 6)
        self.assertEqual(camera_row['available_quantity'], 2)
        with self.assertRaises(HTTPError) as error:
            self.post('inventoryItems', {**cable,'quantity':3})
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.post('stockRequests', {'items':[{'item_id':cable['id'],'quantity':7}]})
        self.assertEqual(error.exception.code, 400)
        for status in ('approved','separated','delivered'):
            request = self.post('stockRequests', {'id':request['id'],'status':status})
        data = self.get('/api/data')
        cable_row = next(item for item in data['inventoryItems'] if item['id']==cable['id'])
        camera_row = next(item for item in data['inventoryItems'] if item['id']==camera['id'])
        self.assertEqual(cable_row['quantity'], 6)
        self.assertEqual(cable_row['reserved_quantity'], 0)
        self.assertEqual(camera_row['quantity'], 2)
        with self.assertRaises(HTTPError) as error:
            self.post('stockRequests', {'id':request['id'],'status':'delivered'})
        self.assertEqual(error.exception.code, 403)
    def test_admin_can_edit_and_delete_accounts(self):
        created = self.post('users', {'name':'Operador de teste','username':'operator-test','password':'operator-old-123','role':'technician'})
        old_session = build_opener(HTTPCookieProcessor(CookieJar()))
        self.login(old_session, 'operator-test', 'operator-old-123')
        edited = self.post('users/update', {'user_id':created['id'],'name':'Almoxarife de teste','username':'store-test','role':'warehouse','active':False,'password':'operator-new-456','confirm_password':'operator-new-456'})
        self.assertEqual(edited['user']['name'], 'Almoxarife de teste')
        self.assertEqual(edited['user']['role'], 'warehouse')
        with self.assertRaises(HTTPError) as error: old_session.open(self.base + '/api/data')
        self.assertEqual(error.exception.code, 401)
        blocked = build_opener(HTTPCookieProcessor(CookieJar()))
        with self.assertRaises(HTTPError) as error:
            self.login(blocked, 'store-test', 'operator-new-456')
        self.assertEqual(error.exception.code, 401)
        self.post('users/update', {'user_id':created['id'],'name':'Almoxarife de teste','username':'store-test','role':'warehouse','active':True,'password':''})
        self.login(blocked, 'store-test', 'operator-new-456')
        self.assertIn('inventoryItems', self.get('/api/data', blocked))
        admin_id = next(item['id'] for item in self.get('/api/data')['users'] if item['username']=='admin')
        with self.assertRaises(HTTPError) as error:
            self.post('users/delete', {'user_id':admin_id})
        self.assertEqual(error.exception.code, 400)
        self.post('users/delete', {'user_id':created['id']})
        with self.assertRaises(HTTPError) as error: blocked.open(self.base + '/api/data')
        self.assertEqual(error.exception.code, 401)
        self.assertFalse(any(item['id']==created['id'] for item in self.get('/api/data')['users']))
    def test_home(self):
        with urlopen(self.base) as response: self.assertIn('Segura Gestão',response.read().decode())

if __name__ == '__main__': unittest.main()

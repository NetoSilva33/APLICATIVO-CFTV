import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
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
        server.create_platform_admin('Vendedora','seller','seller-pass-987654')
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.base = 'http://127.0.0.1:' + str(cls.http.server_port)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.client = build_opener(HTTPCookieProcessor(CookieJar()))
        cls.platform_client = build_opener(HTTPCookieProcessor(CookieJar()))
        setup = Request(cls.base + '/api/setup', json.dumps({'company_name':'Empresa A','name':'Admin','username':'admin','password':'test-password-123'}).encode(), {'Content-Type':'application/json'})
        with cls.client.open(setup) as response:
            assert response.status == 200
        seller_login = Request(cls.base + '/api/platform/login', json.dumps({'username':'seller','password':'seller-pass-987654'}).encode(), {'Content-Type':'application/json'})
        with cls.platform_client.open(seller_login) as response:
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
    def post_as(self, client, kind, data):
        req = Request(self.base + '/api/' + kind, json.dumps(data).encode(), {'Content-Type':'application/json'})
        with client.open(req) as response: return json.load(response)
    def platform_post(self, path, data, client=None):
        req = Request(self.base + '/api/platform/' + path, json.dumps(data).encode(), {'Content-Type':'application/json'})
        with (client or self.platform_client).open(req) as response: return json.load(response)
    def get(self, path, client=None):
        with (client or self.client).open(self.base + path) as response: return json.load(response)
    def login(self, client, username, password, company=''):
        req = Request(self.base + '/api/login', json.dumps({'username':username,'password':password,'company':company}).encode(), {'Content-Type':'application/json'})
        with client.open(req) as response: return json.load(response)
    def platform_login(self, client, username, password):
        req = Request(self.base + '/api/platform/login', json.dumps({'username':username,'password':password}).encode(), {'Content-Type':'application/json'})
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
    def test_seller_login_is_separate_from_company_accounts(self):
        self.assertEqual(self.get('/api/platform/session',self.platform_client)['user']['username'],'seller')
        self.assertIsNone(self.get('/api/platform/session',self.client)['user'])
        with self.assertRaises(HTTPError) as error:
            self.get('/api/platform/companies',self.client)
        self.assertEqual(error.exception.code,401)
        with self.assertRaises(HTTPError) as error:
            self.get('/api/data',self.platform_client)
        self.assertEqual(error.exception.code,401)
        wrong_platform=build_opener(HTTPCookieProcessor(CookieJar()))
        with self.assertRaises(HTTPError) as error:
            self.platform_login(wrong_platform,'admin','test-password-123')
        self.assertEqual(error.exception.code,401)
        wrong_company=build_opener(HTTPCookieProcessor(CookieJar()))
        with self.assertRaises(HTTPError) as error:
            self.login(wrong_company,'seller','seller-pass-987654')
        self.assertEqual(error.exception.code,401)
        with self.assertRaises(HTTPError) as error:
            self.platform_post('setup',{'name':'Duplicado','username':'other-seller','password':'another-seller-pass-123'})
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(RuntimeError):
            server.create_platform_admin('Duplicado','other-seller','another-seller-pass-123')
    def test_tenant_data_and_members_are_isolated(self):
        client_a = self.post('clients', {'name':'Cliente exclusivo A'})
        company = self.platform_post('companies', {'name':'Empresa B','slug':'empresa-b','document':'123','plan':'starter','admin_name':'Admin B','admin_username':'admin','admin_password':'company-b-pass-123'})
        self.assertEqual(company['company']['slug'],'empresa-b')
        tenant_b = build_opener(HTTPCookieProcessor(CookieJar()))
        login_result=self.login(tenant_b,'admin','company-b-pass-123','empresa-b')
        self.assertNotIn('organization_slug',login_result['user'])
        self.assertNotIn('organization_plan',login_result['user'])
        data_b = self.get('/api/data',tenant_b)
        self.assertEqual(data_b['organization'],{'name':'Empresa B'})
        self.assertNotIn('auditLogs',data_b)
        self.assertNotIn('document',data_b['organization'])
        self.assertNotIn('slug',data_b['organization'])
        self.assertNotIn('plan',data_b['organization'])
        session_user=self.get('/api/session',tenant_b)['user']
        self.assertNotIn('organization_slug',session_user)
        self.assertNotIn('organization_document',session_user)
        self.assertNotIn('organization_plan',session_user)
        with self.assertRaises(HTTPError) as error:
            self.post_as(tenant_b,'organization/update',{'name':'Nome alterado','document':'999'})
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(HTTPError) as error:
            tenant_b.open(self.base+'/api/organization/export')
        self.assertEqual(error.exception.code,404)
        self.assertFalse(any(item['name']=='Cliente exclusivo A' for item in data_b['clients']))
        client_b = self.post_as(tenant_b,'clients',{'name':'Cliente exclusivo B'})
        for number in range(9):
            self.post_as(tenant_b,'users',{'name':f'Operador B {number}','username':f'operador-b-{number}','password':'operator-b-pass-123','role':'technician'})
        with self.assertRaises(HTTPError) as error:
            self.post_as(tenant_b,'users',{'name':'Limite','username':'operador-b-limite','password':'operator-b-pass-123','role':'technician'})
        self.assertEqual(error.exception.code,400)
        self.platform_post('companies/update',{'id':company['company']['id'],'plan':'pro','status':'trial'})
        self.post_as(tenant_b,'users',{'name':'Operador B Pro','username':'operador-b-pro','password':'operator-b-pass-123','role':'technician'})
        data_a = self.get('/api/data')
        self.assertTrue(any(item['id']==client_a['id'] for item in data_a['clients']))
        self.assertFalse(any(item['id']==client_b['id'] for item in data_a['clients']))
        with self.assertRaises(HTTPError) as error:
            self.post_as(tenant_b,'users/delete',{'user_id':next(u['id'] for u in data_a['users'] if u['username']=='admin')})
        self.assertEqual(error.exception.code,404)
        with self.assertRaises(HTTPError) as error:
            self.get('/api/platform/companies',tenant_b)
        self.assertEqual(error.exception.code,401)
        with self.assertRaises(HTTPError) as error:
            self.login(build_opener(HTTPCookieProcessor(CookieJar())),'admin','test-password-123')
        self.assertEqual(error.exception.code,400)
        companies=self.get('/api/platform/companies',self.platform_client)
        self.assertEqual(sum(x['id']==company['company']['id'] for x in companies['companies']),1)
        self.platform_post('companies/update',{'id':company['company']['id'],'plan':'pro','status':'suspended'})
        with self.assertRaises(HTTPError) as error: tenant_b.open(self.base+'/api/data')
        self.assertEqual(error.exception.code,401)
        self.platform_post('companies/update',{'id':company['company']['id'],'plan':'pro','status':'active','billing_period':'monthly','renew':True})
        self.login(tenant_b,'admin','company-b-pass-123','empresa-b')
        self.assertEqual(self.get('/api/data',tenant_b)['organization']['name'],'Empresa B')
        with self.assertRaises(HTTPError) as error:
            self.platform_post('companies/update',{'id':company['company']['id'],'slug':'empresa-a','plan':'pro','status':'active'})
        self.assertEqual(error.exception.code,409)
        self.platform_post('companies/update',{'id':company['company']['id'],'name':'Empresa B Comercial','slug':'empresa-b-comercial','document':'456','plan':'pro','status':'active'})
        updated_company=next(row for row in self.get('/api/platform/companies',self.platform_client)['companies'] if row['id']==company['company']['id'])
        self.assertEqual(updated_company['name'],'Empresa B Comercial')
        self.assertEqual(updated_company['document'],'456')
        self.assertEqual(updated_company['slug'],'empresa-b-comercial')
        updated_tenant_data=self.get('/api/data',tenant_b)
        self.assertEqual(updated_tenant_data['organization']['name'],'Empresa B Comercial')
        self.assertNotIn('auditLogs',updated_tenant_data)
        tenant_b_new=build_opener(HTTPCookieProcessor(CookieJar()))
        self.login(tenant_b_new,'admin','company-b-pass-123','empresa-b-comercial')
        self.assertEqual(self.get('/api/data',tenant_b_new)['organization'],{'name':'Empresa B Comercial'})
        root_org=next(row['id'] for row in self.get('/api/platform/companies',self.platform_client)['companies'] if row['name']=='Empresa A')
        self.platform_post('companies/update',{'id':root_org,'plan':'starter','status':'suspended'})
        with self.assertRaises(HTTPError) as error: self.get('/api/data')
        self.assertEqual(error.exception.code,401)
        self.assertTrue(any(row['id']==root_org for row in self.get('/api/platform/companies',self.platform_client)['companies']))
        self.platform_post('companies/update',{'id':root_org,'plan':'starter','status':'active','billing_period':'monthly','renew':True})

    def test_subscription_countdown_and_renewal(self):
        created=self.platform_post('companies',{'name':'Assinante','slug':'assinante','plan':'starter','admin_name':'Admin Assinante','admin_username':'admin-assinante','admin_password':'subscriber-pass-123'})
        org_id=created['company']['id']
        company=next(row for row in self.get('/api/platform/companies',self.platform_client)['companies'] if row['id']==org_id)
        self.assertEqual(company['billing_period'],'monthly')
        self.assertEqual(company['expires_at'],company['trial_ends_at'])
        self.assertGreaterEqual(company['days_remaining'],13)
        self.assertLessEqual(company['days_remaining'],14)
        tenant=build_opener(HTTPCookieProcessor(CookieJar()))
        self.login(tenant,'admin-assinante','subscriber-pass-123','assinante')
        with self.assertRaises(HTTPError) as error:
            self.platform_post('companies/update',{'id':org_id,'plan':'starter','status':'active','billing_period':'monthly'})
        self.assertEqual(error.exception.code,400)
        self.platform_post('companies/update',{'id':org_id,'plan':'starter','status':'active','billing_period':'monthly','renew':True})
        company=next(row for row in self.get('/api/platform/companies',self.platform_client)['companies'] if row['id']==org_id)
        first_expiration=datetime.fromisoformat(company['subscription_ends_at'])
        self.assertEqual(company['expires_at'],company['subscription_ends_at'])
        self.assertGreaterEqual(company['days_remaining'],28)
        self.assertLessEqual(company['days_remaining'],31)
        self.platform_post('companies/update',{'id':org_id,'plan':'starter','status':'active','billing_period':'annual','renew':True})
        renewed=next(row for row in self.get('/api/platform/companies',self.platform_client)['companies'] if row['id']==org_id)
        annual_expiration=datetime.fromisoformat(renewed['subscription_ends_at'])
        self.assertEqual(renewed['billing_period'],'annual')
        self.assertGreaterEqual(annual_expiration-first_expiration,timedelta(days=360))
        expired_at=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        with server.connect() as db:
            db.execute('UPDATE organizations SET subscription_ends_at=? WHERE id=?',(expired_at,org_id))
        with self.assertRaises(HTTPError) as error:
            tenant.open(self.base+'/api/data')
        self.assertEqual(error.exception.code,401)
        with self.assertRaises(HTTPError) as error:
            self.login(build_opener(HTTPCookieProcessor(CookieJar())),'admin-assinante','subscriber-pass-123','assinante')
        self.assertEqual(error.exception.code,401)
        expired=next(row for row in self.get('/api/platform/companies',self.platform_client)['companies'] if row['id']==org_id)
        self.assertTrue(expired['expired'])
        self.assertLess(expired['days_remaining'],0)
        self.platform_post('companies/update',{'id':org_id,'plan':'starter','status':'active','billing_period':'quarterly','renew':True})
        renewed=next(row for row in self.get('/api/platform/companies',self.platform_client)['companies'] if row['id']==org_id)
        self.assertEqual(renewed['billing_period'],'quarterly')
        self.assertGreaterEqual(renewed['days_remaining'],89)
        self.assertLessEqual(renewed['days_remaining'],92)
        reactivated=build_opener(HTTPCookieProcessor(CookieJar()))
        self.login(reactivated,'admin-assinante','subscriber-pass-123','assinante')
        self.assertEqual(self.get('/api/data',reactivated)['organization']['name'],'Assinante')
    def test_legacy_database_migrates_without_losing_data(self):
        previous=server.DB
        legacy_path=Path(self.temp.name)/'legacy.db'
        db=sqlite3.connect(legacy_path)
        db.execute('CREATE TABLE users (id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,name TEXT NOT NULL,role TEXT NOT NULL,salt BLOB NOT NULL,password_hash BLOB NOT NULL,active INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL)')
        db.execute('CREATE TABLE sessions (token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires TEXT NOT NULL)')
        db.execute('CREATE TABLE records (kind TEXT,id TEXT,data TEXT NOT NULL,PRIMARY KEY(kind,id))')
        db.execute('INSERT INTO users VALUES (?,?,?,?,?,?,1,?)',('legacy-user','admin','Legacy Admin','admin',b'1234567890123456',b'hash','2025-01-01T00:00:00+00:00'))
        db.execute('INSERT INTO records VALUES (?,?,?)',('company','profile','{"name":"Legacy Company"}'))
        db.execute('INSERT INTO records VALUES (?,?,?)',('clients','client-1','{"id":"client-1","name":"Legacy Client"}'))
        db.execute('INSERT INTO sessions VALUES (?,?,?)',('token-hash','legacy-user','2099-01-01T00:00:00+00:00'))
        db.commit();db.close()
        try:
            server.DB=legacy_path
            with server.connect() as migrated:
                company=migrated.execute('SELECT name,slug FROM organizations').fetchone()
                member=migrated.execute('SELECT username,role FROM memberships').fetchone()
                record=migrated.execute('SELECT data FROM records WHERE kind=? AND id=?',('clients','client-1')).fetchone()
                session=migrated.execute('SELECT membership_id FROM sessions WHERE token_hash=?',('token-hash',)).fetchone()
                self.assertEqual(company['name'],'Legacy Company')
                self.assertEqual(company['slug'],'legacy-company')
                self.assertEqual(tuple(member),('admin','admin'))
                self.assertEqual(json.loads(record['data'])['name'],'Legacy Client')
                self.assertEqual(session['membership_id'],'legacy-user')
        finally:
            server.DB=previous
    def test_existing_company_admin_is_not_reused_as_seller(self):
        previous=server.DB
        existing_path=Path(self.temp.name)/'existing-multitenant.db'
        db=sqlite3.connect(existing_path)
        db.execute('CREATE TABLE organizations (id TEXT PRIMARY KEY,name TEXT NOT NULL,slug TEXT NOT NULL UNIQUE,document TEXT NOT NULL DEFAULT "",plan TEXT NOT NULL DEFAULT "starter",status TEXT NOT NULL DEFAULT "active",trial_ends_at TEXT,created_at TEXT NOT NULL)')
        db.execute('CREATE TABLE users (id TEXT PRIMARY KEY,name TEXT NOT NULL,created_at TEXT NOT NULL,is_platform_admin INTEGER NOT NULL DEFAULT 0)')
        db.execute('CREATE TABLE memberships (id TEXT PRIMARY KEY,organization_id TEXT NOT NULL,user_id TEXT NOT NULL,username TEXT NOT NULL,role TEXT NOT NULL,salt BLOB NOT NULL,password_hash BLOB NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)')
        db.execute('CREATE TABLE sessions (token_hash TEXT PRIMARY KEY,membership_id TEXT NOT NULL,expires TEXT NOT NULL)')
        db.execute('CREATE TABLE records (organization_id TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(organization_id,kind,id))')
        db.execute('INSERT INTO organizations VALUES (?,?,?,?,?,?,?,?)',('org-1','Empresa Legada','empresa-legada','','starter','active',None,'2025-01-01'))
        db.execute('INSERT INTO users VALUES (?,?,?,1)',('old-owner','Administrador','2025-01-01'))
        db.execute('INSERT INTO memberships VALUES (?,?,?,?,?,?,?,?,?)',('member-1','org-1','old-owner','admin','admin',b'salt',b'hash',1,'2025-01-01'))
        db.execute('INSERT INTO records VALUES (?,?,?,?)',('org-1','clients','client-1','{"name":"Cliente preservado"}'))
        db.commit();db.close()
        try:
            server.DB=existing_path
            with server.connect() as migrated:
                self.assertEqual(migrated.execute('SELECT is_platform_admin FROM users WHERE id=?',('old-owner',)).fetchone()[0],0)
                self.assertEqual(migrated.execute('SELECT COUNT(*) FROM platform_users').fetchone()[0],0)
                self.assertEqual(json.loads(migrated.execute('SELECT data FROM records WHERE id=?',('client-1',)).fetchone()[0])['name'],'Cliente preservado')
        finally:
            server.DB=previous
    def test_home(self):
        with urlopen(self.base) as response: self.assertIn('Segura Gestão',response.read().decode())
        with urlopen(self.base+'/plataforma') as response:
            portal=response.read().decode()
            self.assertIn('PORTAL COMERCIAL',portal)
            self.assertIn('platform.js',portal)

if __name__ == '__main__': unittest.main()

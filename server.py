"""Independent preview WSGI backend. No school credentials or outbound requests."""
from pathlib import Path
import json,os,hashlib,hmac,secrets,time,sqlite3
from http.cookies import SimpleCookie
from urllib.parse import urlparse
from wsgiref.simple_server import make_server
from security import RateLimiter
ROOT=Path(__file__).parent
DATA=ROOT/'data'; DATA.mkdir(exist_ok=True)
DB=DATA/'my-chuannong.db'
PRODUCT='my-chuannong'
ORIGIN='http://127.0.0.1:8791'
BUILTIN=json.loads((ROOT/'school.json').read_text(encoding='utf-8'))
SECRETS=Path(os.environ.get('MY_CHUANNONG_BACKEND_CONFIG',r'D:\Codex-Local-Secrets\MyChuannong\backend-local.json'))
limiter=RateLimiter()
def connect():
 db=sqlite3.connect(DB);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db

def initialize():
 c=json.loads(SECRETS.read_text(encoding='utf-8'))
 with connect() as d:
  d.executescript('''CREATE TABLE IF NOT EXISTS admins(username TEXT PRIMARY KEY,salt TEXT NOT NULL,password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,username TEXT,expires INTEGER);
CREATE TABLE IF NOT EXISTS configs(version INTEGER PRIMARY KEY,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,action TEXT NOT NULL,at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS announcements(id INTEGER PRIMARY KEY,title TEXT,body TEXT);
CREATE TABLE IF NOT EXISTS lost(id INTEGER PRIMARY KEY,title TEXT,body TEXT);
CREATE TABLE IF NOT EXISTS resources(id INTEGER PRIMARY KEY,title TEXT,body TEXT);''')
  salt=secrets.token_hex(16)
  digest=hashlib.pbkdf2_hmac('sha256',c['adminPassword'].encode(),bytes.fromhex(salt),300000).hex()
  d.execute('INSERT OR IGNORE INTO admins VALUES(?,?,?)',(c['adminUsername'],salt,digest))
  d.execute('INSERT OR IGNORE INTO configs VALUES(?,?)',(1,json.dumps(BUILTIN,ensure_ascii=False)))

def current():
 with connect() as d:return json.loads(d.execute('SELECT body FROM configs ORDER BY version DESC LIMIT 1').fetchone()[0])

def validate_config(c):
 assert set(c)==set(BUILTIN)
 for key in set(BUILTIN)-{'services','configVersion'}:assert c[key]==BUILTIN[key]
 assert c['productId']==PRODUCT and c['schoolId']=='sicau' and c['applicationId']=='com.mychuannong.app' and c['schemaVersion']==1
 base={v['id']:v for v in BUILTIN['services']};ids=[v['id'] for v in c['services']]
 assert len(ids)==len(set(ids))==len(base) and set(ids)==set(base)
 assert c.get('website') is None and c.get('forceUpdate') is False
 for v in c['services']:
  old=base[v['id']]
  assert set(v)==set(old)
  for key in set(old)-{'name','visible','note','verification'}:assert v[key]==old[key]
  assert isinstance(v['note'],str) and len(v['note'])<=2000
  assert isinstance(v['verification'],str) and len(v['verification'])<=500
  assert v['url']==old['url'] and v['mode']==old['mode'] and v['group']==old['group']
  assert isinstance(v['name'],str) and 1<=len(v['name'])<=20 and type(v['visible']) is bool
 for id in ['auth','school','academic']:
  assert next(v for v in c['services'] if v['id']==id)['visible']

def app(env,start):
 path=env.get('PATH_INFO','');method=env['REQUEST_METHOD'];extra=[]
 def response(status,payload,ctype='application/json; charset=utf-8'):
  body=payload if isinstance(payload,bytes) else json.dumps(payload,ensure_ascii=False).encode()
  start(status,[('Content-Type',ctype),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"),*extra]);return [body]
 try:
  if env.get('HTTP_HOST') not in ['127.0.0.1:8791','localhost:8791']:return response('403 Forbidden',{'error':'Invalid host'})
  if method=='POST' and env.get('HTTP_ORIGIN')!=ORIGIN:return response('403 Forbidden',{'error':'Origin rejected'})
  if path in ['/health','/ready']:return response('200 OK',{'productId':PRODUCT,'status':'ok','database':'independent-local'})
  if method=='GET' and path in ['/admin/','/admin/index.html','/admin/styles.css','/admin/app.js']:
   f=ROOT/('admin/index.html' if path=='/admin/' else path.lstrip('/'));ctype='text/html; charset=utf-8' if f.suffix=='.html' else 'text/css; charset=utf-8' if f.suffix=='.css' else 'application/javascript; charset=utf-8'
   return response('200 OK',f.read_bytes(),ctype)
  if path=='/api/v1/public/config' and method=='GET':return response('200 OK',current())
  if path=='/api/v1/public/version' and method=='GET':return response('200 OK',{'productId':PRODUCT,'applicationId':'com.mychuannong.app','decision':'NONE','forceUpdate':False,'release':None})
  if path.startswith('/api/v1/public/') and method=='GET':
   table=path.rsplit('/',1)[-1]
   if table in ['lost','resources','announcements']:
    with connect() as d:items=[dict(x) for x in d.execute('SELECT * FROM '+table+' ORDER BY id DESC LIMIT 50')]
    return response('200 OK',{'productId':PRODUCT,'items':items})
  if path not in ['/api/v1/admin/login','/api/v1/admin/logout','/api/v1/admin/state','/api/v1/admin/config','/api/v1/admin/rollback']:return response('404 Not Found',{'error':'Not found'})
  data={}
  if method=='POST':
   length=int(env.get('CONTENT_LENGTH') or 0)
   if length>131072:return response('413 Payload Too Large',{'error':'Too large'})
   data=json.loads(env['wsgi.input'].read(length) or b'{}')
  if path=='/api/v1/admin/login' and method=='POST':
   if limiter.check('admin-login',10,300):return response('429 Too Many Requests',{'error':'Retry later'})
   if set(data)!={'username','password'}:return response('400 Bad Request',{'error':'Invalid fields'})
   with connect() as d:
    row=d.execute('SELECT * FROM admins WHERE username=?',(data['username'],)).fetchone()
    if not row or not hmac.compare_digest(row['password_hash'],hashlib.pbkdf2_hmac('sha256',str(data['password']).encode(),bytes.fromhex(row['salt']),300000).hex()):return response('401 Unauthorized',{'error':'Invalid credentials'})
    token=secrets.token_urlsafe(32);d.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['username'],int(time.time())+3600))
   extra.append(('Set-Cookie','my_chuannong_admin='+token+'; HttpOnly; SameSite=Strict; Path=/api/v1/admin; Max-Age=3600'))
   return response('200 OK',{'productId':PRODUCT,'username':row['username']})
  cookie=SimpleCookie();cookie.load(env.get('HTTP_COOKIE',''));token=cookie.get('my_chuannong_admin');hashed=hashlib.sha256((token.value if token else '').encode()).hexdigest()
  with connect() as d:session=d.execute('SELECT * FROM sessions WHERE token_hash=? AND expires>?',(hashed,int(time.time()))).fetchone()
  if not session:return response('401 Unauthorized',{'error':'Login required'})
  if path=='/api/v1/admin/logout' and method=='POST':
   with connect() as d:d.execute('DELETE FROM sessions WHERE token_hash=?',(hashed,))
   extra.append(('Set-Cookie','my_chuannong_admin=; HttpOnly; SameSite=Strict; Path=/api/v1/admin; Max-Age=0'));return response('200 OK',{'ok':True})
  if path=='/api/v1/admin/state' and method=='GET':
   with connect() as d:
    counts={t:d.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['lost','resources','announcements']}
    audits=[dict(x) for x in d.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 20')]
    versions=[x[0] for x in d.execute('SELECT version FROM configs ORDER BY version DESC')]
   return response('200 OK',{'productId':PRODUCT,'config':current(),'counts':counts,'audit':audits,'versions':versions,'username':session['username']})
  if path in ['/api/v1/admin/config','/api/v1/admin/rollback'] and method=='POST':
   with connect() as d:
    d.execute('BEGIN IMMEDIATE');version=d.execute('SELECT max(version) FROM configs').fetchone()[0]+1
    if path.endswith('rollback'):
     assert set(data)=={'version'};row=d.execute('SELECT body FROM configs WHERE version=?',(int(data['version']),)).fetchone();assert row;c=json.loads(row[0])
    else:c=data;validate_config(c)
    c['configVersion']=version;validate_config(c)
    d.execute('INSERT INTO configs VALUES(?,?)',(version,json.dumps(c,ensure_ascii=False)));d.execute('INSERT INTO audit(action,at) VALUES(?,?)',('rollback' if path.endswith('rollback') else 'config',int(time.time())))
   return response('200 OK',{'productId':PRODUCT,'version':version})
  return response('404 Not Found',{'error':'Not found'})
 except (AssertionError,ValueError,KeyError,TypeError):return response('400 Bad Request',{'error':'Invalid request or cross-product configuration'})
 except Exception:return response('500 Internal Server Error',{'error':'Local backend error'})

if __name__=='__main__':
 initialize()
 # Loopback only. No production network, scheduler, mail, SMS or push worker.
 with make_server('127.0.0.1',8791,app) as http:http.serve_forever()

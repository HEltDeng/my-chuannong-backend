from pathlib import Path
import urllib.request,urllib.error,json,http.cookiejar,copy
P=Path(__file__).resolve().parent;base='http://127.0.0.1:8791';jar=http.cookiejar.CookieJar();op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar));tests=[]
def req(path,data=None,origin=base):
 rq=urllib.request.Request(base+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json','Origin':origin})
 try:
  with op.open(rq,timeout=10) as r:return r.status,json.load(r)
 except urllib.error.HTTPError as e:return e.code,json.load(e)
def check(name,condition):
 tests.append({'name':name,'pass':bool(condition)});assert condition,name
check('health',req('/health')[0]==200)
check('admin requires auth',req('/api/v1/admin/state')[0]==401)
secret=json.loads(Path(r'D:\Codex-Local-Secrets\MyChuannong\backend-local.json').read_text())
login={'username':secret['adminUsername'],'password':secret['adminPassword']}
check('CSRF origin rejection',req('/api/v1/admin/login',login,'https://example.com')[0]==403)
check('independent admin login',req('/api/v1/admin/login',login)[0]==200)
code,c=req('/api/v1/public/config');check('product identity',c['productId']=='my-chuannong' and c['applicationId']=='com.mychuannong.app')
bad=copy.deepcopy(c);bad['productId']='other-product';check('cross product rejected',req('/api/v1/admin/config',bad)[0]==400)
bad=copy.deepcopy(c);bad['services'][0]['url']='https://example.com/';check('arbitrary URL rejected',req('/api/v1/admin/config',bad)[0]==400)
bad=copy.deepcopy(c);bad['services'][0]['mode']='JAVASCRIPT';check('mode injection rejected',req('/api/v1/admin/config',bad)[0]==400)
v2=copy.deepcopy(c);v2['services'][0]['name']='课表入口';check('config V2 publish',req('/api/v1/admin/config',v2)[0]==200)
check('config read changed',req('/api/v1/public/config')[1]['services'][0]['name']=='课表入口')
check('rollback higher revision',req('/api/v1/admin/rollback',{'version':c['configVersion']})[0]==200)
r=req('/api/v1/public/config')[1];check('rollback restored content',r['configVersion']>c['configVersion'] and r['services'][0]['name']==c['services'][0]['name'])
for table in ['lost','resources','announcements']:check('empty independent '+table,req('/api/v1/public/'+table)[1]['items']==[])
check('no formal update',req('/api/v1/public/version')[1]['decision']=='NONE')
check('no school credentials route',req('/api/v1/school/bind',{'studentPassword':'synthetic-test'})[0]==404)
check('logout',req('/api/v1/admin/logout',{})[0]==200)
check('session invalidated',req('/api/v1/admin/state')[0]==401)
print('PASS',len(tests),'backend tests. Config rollback restored original content.')

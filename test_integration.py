"""Integration tests against isolated real PostgreSQL, two app processes, two proxies.
PG_BIN=/path/to/postgres/bin python3 test_integration.py --report test_results.json
Creates only a fresh temporary database, never uses the caller's DB_* values.
"""
import argparse,concurrent.futures,json,os,shutil,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
import urllib.request,urllib.error
import pg8000.dbapi
ROOT=Path(__file__).resolve().parent

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
class Stack:
    def __init__(self,pg):
        self.pg=Path(pg).resolve();self.tmp=tempfile.TemporaryDirectory(prefix='numbers-tests-')
        self.dir=Path(self.tmp.name);self.port=free_port();self.apps=[free_port(),free_port()]
        self.webs=[free_port(),free_port()];self.children=[];self.files=[];self.app_processes={}
        self.env={**os.environ,'DB_HOST':'127.0.0.1','DB_PORT':str(self.port),'DB_USER':'student','DB_PASSWORD':'','DB_NAME':'postgres'}
    def dbctl(self,action):
        args=[str(self.pg/'pg_ctl'),'-D',str(self.dir/'data'),'-w']
        if action=='start':args+=['-l',str(self.dir/'postgres.log'),'-o',f'-h 127.0.0.1 -p {self.port} -k /tmp','start']
        else:args+=['-m','fast','stop']
        subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    def spawn(self,args,name):
        f=open(self.dir/(name+'.out'),'ab');self.files.append(f)
        p=subprocess.Popen([sys.executable,*map(str,args)],env=self.env,stdout=f,stderr=f,cwd=ROOT)
        self.children.append(p);return p
    def app(self,index,init=False):
        args=[ROOT/'application.py','--host','127.0.0.1','--port',self.apps[index],'--max-number','1000','--log',self.dir/f'app{index}.log']
        if init:args.append('--init-db')
        self.app_processes[index]=self.spawn(args,f'app{index}')
        self.wait(self.apps[index])
    def start(self):
        subprocess.run([str(self.pg/'initdb'),'-D',str(self.dir/'data'),'-U','student','--auth=trust','--no-locale','-E','UTF8'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        self.dbctl('start');self.app(0,True);self.app(1)
        for i in range(2):
            self.spawn([ROOT/'webserver.py','--host','127.0.0.1','--port',self.webs[i],'--upstream',f'http://127.0.0.1:{self.apps[i]}'],f'web{i}')
            self.wait(self.webs[i])
    def wait(self,port):
        for _ in range(100):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=.5) as r:
                    if r.status==200:return
            except Exception:time.sleep(.05)
        raise RuntimeError(f'Startup failed; logs: {self.dir}')
    def sql(self,query,params=()):
        c=pg8000.dbapi.connect(host='127.0.0.1',port=self.port,user='student',database='postgres')
        try:
            cur=c.cursor();cur.execute(query,params)
            rows=cur.fetchall() if cur.description else []
            c.commit();return rows
        finally:c.close()
    def request(self,value=None,index=0,raw=None):
        data=raw if raw is not None else json.dumps({'number':value}).encode()
        req=urllib.request.Request(f'http://127.0.0.1:{self.webs[index]}/increment',data=data,headers={'Content-Type':'application/json'})
        try:r=urllib.request.urlopen(req,timeout=10)
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,json.load(r)
    def close(self):
        for p in reversed(self.children):
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=3)
                except subprocess.TimeoutExpired:p.kill();p.wait()
        if (self.dir/'data/postmaster.pid').exists():self.dbctl('stop')
        for f in self.files:f.close()
        self.tmp.cleanup()

class Integration(unittest.TestCase):
    def setUp(self):S.sql('TRUNCATE events')
    def test_success_duplicate_predecessor(self):
        self.assertEqual(S.request(5)[1]['result'],6)
        self.assertEqual(S.request(5)[1]['error'],'DUPLICATE_NUMBER')
        self.assertEqual(S.request(4)[1]['error'],'PREDECESSOR_OF_PROCESSED')
        self.assertEqual(S.request(4)[1]['error'],'DUPLICATE_NUMBER')
        self.assertEqual(S.request(6)[1]['result'],7)
        self.assertEqual(S.request(3)[1]['result'],4)
        self.assertEqual([row[0] for row in S.sql('SELECT number FROM processed_numbers ORDER BY number')],[3,5,6])
    def test_boundaries(self):
        self.assertEqual(S.request(0)[1]['result'],1)
        self.assertEqual(S.request(1000)[1]['result'],1001)
    def test_invalid_inputs(self):
        cases=[b'{',b'null',b'[]',b'{}',b'{"number":true}',b'{"number":2.5}',b'{"number":"5"}',b'{"number":-1}',b'{"number":1001}',b'{"number":5,"extra":1}',b'{"number":1,"number":2}',b'{"number":NaN}',b'{"number":1e999}',b'{"number":999999999999999999999999999999999999999}',b'\xff']
        for raw in cases:
            with self.subTest(raw=raw):self.assertEqual(S.request(raw=raw)[0],400)
        self.assertEqual(S.sql('SELECT count(*) FROM request_history')[0][0],0)
    def test_duplicate_concurrency_across_processes(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
            results=list(ex.map(lambda i:S.request(500,index=i%2),range(20)))
        self.assertEqual(sum(status==200 for status,_ in results),1)
        self.assertEqual(sum(status==409 for status,_ in results),19)
        self.assertEqual(S.sql('SELECT count(*) FROM processed_numbers')[0][0],1)
        self.assertEqual(S.sql('SELECT count(*) FROM request_history')[0][0],20)
    def test_neighbor_concurrency(self):
        for base in range(100,120,2):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                low=ex.submit(S.request,base,0);high=ex.submit(S.request,base+1,1)
                a,b=low.result(),high.result()
            self.assertEqual(b[0],200)
            self.assertIn(a[0],(200,409))
            if a[0]==409:self.assertEqual(a[1]['error'],'PREDECESSOR_OF_PROCESSED')
    def test_history_survives_application_restart(self):
        self.assertEqual(S.request(55)[0],200)
        S.app_processes[0].terminate();S.app_processes[0].wait(timeout=3)
        S.app(0)
        self.assertEqual(S.request(55)[1]['error'],'DUPLICATE_NUMBER')
    def test_transaction_rolls_back(self):
        S.sql('ALTER TABLE events ADD CONSTRAINT deliberate_test_failure CHECK(number<>999)')
        try:
            self.assertEqual(S.request(999)[0],503)
            self.assertEqual(S.sql('SELECT count(*) FROM processed_numbers WHERE number=999')[0][0],0)
        finally:S.sql('ALTER TABLE events DROP CONSTRAINT deliberate_test_failure')
    def test_serialization_retry(self):
        S.sql("CREATE FUNCTION slow_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.05); RETURN NEW; END $$")
        S.sql('CREATE TRIGGER slow_insert BEFORE INSERT ON events FOR EACH ROW EXECUTE FUNCTION slow_insert()')
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                results=list(ex.map(lambda i:S.request(777,index=i),range(2)))
            self.assertEqual(sorted(status for status,_ in results),[200,409])
            logs=''.join((S.dir/f'app{i}.log').read_text() for i in range(2))
            self.assertIn('"retry":',logs)
            self.assertEqual(S.sql('SELECT count(*) FROM events WHERE number=777')[0][0],2)
        finally:
            S.sql('DROP TRIGGER slow_insert ON events');S.sql('DROP FUNCTION slow_insert()')
    def test_business_errors_logged(self):
        S.request(5);_,body=S.request(4)
        log=(S.dir/'app0.log').read_text()
        self.assertIn(body['request_id'],log);self.assertIn('PREDECESSOR_OF_PROCESSED',log)
    def test_database_unavailable(self):
        S.dbctl('stop')
        try:self.assertEqual(S.request(20)[0],503)
        finally:S.dbctl('start')
    def test_upstream_unavailable(self):
        S.app_processes[1].terminate();S.app_processes[1].wait(timeout=3)
        try:self.assertEqual(S.request(20,index=1)[0],502)
        finally:S.app(1)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--report',default='test_results.json');p.add_argument('--pg-bin',default=os.getenv('PG_BIN'))
    args=p.parse_args()
    if not args.pg_bin:p.error('Pass --pg-bin or PG_BIN pointing to PostgreSQL bin/')
    S=Stack(args.pg_bin)
    try:
        S.start();result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Integration))
        report={'tests':result.testsRun,'passed':result.wasSuccessful(),'failures':len(result.failures),'errors':len(result.errors),'database':'PostgreSQL','application_processes':2,'proxy_processes':2,'invalid_input_cases':15,'concurrent_duplicate_requests':20,'neighbor_pairs':10}
        Path(args.report).write_text(json.dumps(report,indent=2))
        if not result.wasSuccessful():sys.exit(1)
    finally:S.close()

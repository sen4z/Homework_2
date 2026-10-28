"""Local PostgreSQL + application + reverse proxy. Ctrl-C stops child services.
Only manages a dedicated .runtime/ directory; never connects to an existing cluster.
Install requirements.txt first. Pass --pg-bin if PostgreSQL is not on PATH.
"""
import argparse,os,shutil,signal,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--pg-bin',default=os.getenv('PG_BIN'))
p.add_argument('--runtime',type=Path,default=ROOT/'.runtime')
p.add_argument('--web-port',type=int,default=8080);p.add_argument('--app-port',type=int,default=8081)
p.add_argument('--db-port',type=int,default=54329);p.add_argument('--max-number',type=int,default=1000000)
a=p.parse_args()
if not a.pg_bin:
    found=shutil.which('postgres')
    if not found:p.error('PostgreSQL not found: pass --pg-bin /path/to/postgresql/bin')
    a.pg_bin=str(Path(found).parent)
pg=Path(a.pg_bin).resolve();runtime=a.runtime.resolve();runtime.mkdir(parents=True,exist_ok=True)
data=runtime/'data';marker=runtime/'numbers-project.txt'
if data.exists() and not marker.exists():p.error('Refusing to use a database not created by this launcher')
if (data/'postmaster.pid').exists():p.error('This project database is already running')
children=[];files=[];started=False

def stop_signal(*_):raise KeyboardInterrupt
signal.signal(signal.SIGTERM,stop_signal)

def spawn(args,name,env):
    f=open(runtime/(name+'.console.log'),'ab');files.append(f)
    proc=subprocess.Popen([sys.executable,*map(str,args)],cwd=ROOT,env=env,stdout=f,stderr=f)
    children.append(proc);return proc

def ready(port,proc):
    for _ in range(100):
        if proc.poll() is not None:raise RuntimeError(f'Service exited; see {runtime}')
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=.5) as r:
                if r.status==200:return
        except Exception:time.sleep(.1)
    raise RuntimeError(f'Service on port {port} did not become ready')
try:
    if not data.exists():
        subprocess.run([pg/'initdb','-D',data,'-U','student','--auth=trust','--no-locale','-E','UTF8'],check=True)
        marker.write_text('Dedicated local coursework database. Loopback only.\n')
    subprocess.run([pg/'pg_ctl','-D',data,'-l',runtime/'postgres.log','-o',f'-h 127.0.0.1 -p {a.db_port} -k /tmp','-w','start'],check=True)
    started=True
    env={**os.environ,'DB_HOST':'127.0.0.1','DB_PORT':str(a.db_port),'DB_USER':'student','DB_PASSWORD':'','DB_NAME':'postgres'}
    app=spawn([ROOT/'application.py','--host','127.0.0.1','--port',a.app_port,'--max-number',a.max_number,'--init-db','--log',runtime/'application.log'],'app',env)
    ready(a.app_port,app)
    web=spawn([ROOT/'webserver.py','--host','127.0.0.1','--port',a.web_port,'--upstream',f'http://127.0.0.1:{a.app_port}'],'web',env)
    ready(a.web_port,web)
    print(f'READY http://127.0.0.1:{a.web_port} | N={a.max_number}',flush=True)
    print(f'Logs/data: {runtime}. Ctrl-C stops this stack; history remains.',flush=True)
    while all(c.poll() is None for c in children):time.sleep(.5)
    raise RuntimeError('A child service exited unexpectedly')
except KeyboardInterrupt:pass
finally:
    for c in reversed(children):
        if c.poll() is None:
            c.terminate()
            try:c.wait(timeout=4)
            except subprocess.TimeoutExpired:c.kill();c.wait()
    if started:subprocess.run([pg/'pg_ctl','-D',data,'-m','fast','-w','stop'],check=True)
    for f in files:f.close()

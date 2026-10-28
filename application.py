"""Application server. Serializable transactions over a single append-only event log."""
import argparse
import json
import logging
import os
import uuid
import time
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pg8000.dbapi

MAX_BODY = 4096
SCHEMA = '''
CREATE TABLE IF NOT EXISTS events(
 request_id uuid PRIMARY KEY, number bigint NOT NULL CHECK(number>=0),
 outcome text NOT NULL CHECK(outcome IN ('OK','DUPLICATE_NUMBER','PREDECESSOR_OF_PROCESSED')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_number ON events(number);
CREATE UNIQUE INDEX IF NOT EXISTS one_success_per_number ON events(number) WHERE outcome='OK';
CREATE OR REPLACE VIEW request_history AS SELECT * FROM events;
CREATE OR REPLACE VIEW processed_numbers AS SELECT number FROM events WHERE outcome='OK';
'''

def connection():
    return pg8000.dbapi.connect(host=os.getenv('DB_HOST','127.0.0.1'),
        port=int(os.getenv('DB_PORT','54329')),database=os.getenv('DB_NAME','postgres'),
        user=os.getenv('DB_USER','student'),password=os.getenv('DB_PASSWORD',''),timeout=5)

def init_database():
    conn = connection()
    try:
        cursor = conn.cursor()
        for statement in SCHEMA.split(';'):
            if statement.strip(): cursor.execute(statement)
        conn.commit()
    finally: conn.close()

def decide(cur, number):
    cur.execute('SELECT EXISTS(SELECT 1 FROM events WHERE number=%s)', (number,))
    if cur.fetchone()[0]:
        return 'DUPLICATE_NUMBER'
    cur.execute("SELECT EXISTS(SELECT 1 FROM events WHERE number=%s AND outcome='OK')", (number+1,))
    return 'PREDECESSOR_OF_PROCESSED' if cur.fetchone()[0] else 'OK'


def process(number, request_id):
    # No guard row. PostgreSQL detects incompatible reads/writes; retry the whole transaction.
    for attempt in range(12):
        conn = connection()
        try:
            cur = conn.cursor()
            cur.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
            cur.execute("SET LOCAL statement_timeout = '3s'")
            outcome = decide(cur, number)
            cur.execute('INSERT INTO events(request_id,number,outcome) VALUES(%s,%s,%s)',
                        (request_id,number,outcome))
            conn.commit()
            return outcome
        except pg8000.dbapi.DatabaseError as exc:
            conn.rollback()
            details = exc.args[0] if exc.args and isinstance(exc.args[0],dict) else {}
            retryable = details.get('C') in ('40001','40P01') or (
                details.get('C') == '23505' and details.get('n') == 'one_success_per_number')
            if not retryable or attempt == 11:
                raise
            logging.info(json.dumps({'request_id':request_id,'retry':attempt+1,'sqlstate':details.get('C')}))
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        time.sleep(random.uniform(0.003,0.012) * (attempt+1))
    raise RuntimeError('Retry limit exceeded')

def unique_object(pairs):
    obj = {}
    for key,value in pairs:
        if key in obj: raise ValueError('Duplicate JSON key')
        obj[key]=value
    return obj

class Handler(BaseHTTPRequestHandler):
    server_version = 'NumbersApp/1.0'
    def setup(self):
        super().setup(); self.connection.settimeout(6)
    def log_message(self,*args): pass
    def send_json(self,status,payload):
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(data)
    def error(self,status,code,request_id,**context):
        logging.error(json.dumps({'request_id':request_id,'error':code,**context},ensure_ascii=False))
        self.send_json(status,{'error':code,'request_id':request_id})
    def do_GET(self):
        if self.path!='/health': return self.send_json(404,{'error':'NOT_FOUND'})
        try:
            conn=connection()
            try:
                cur=conn.cursor();cur.execute('SELECT request_id FROM events LIMIT 0')
            finally: conn.close()
        except Exception: return self.send_json(503,{'status':'unavailable'})
        self.send_json(200,{'status':'ok','max_number':self.server.max_number})
    def do_POST(self):
        request_id=str(uuid.uuid4())
        if self.path!='/increment': return self.error(404,'NOT_FOUND',request_id)
        try:
            if self.headers.get('Transfer-Encoding'): raise ValueError('Transfer coding not supported')
            length=int(self.headers.get('Content-Length','-1'))
            if not 0 < length <= MAX_BODY: raise ValueError('Body size')
            if self.headers.get('Content-Type','').split(';')[0].strip().lower()!='application/json':
                raise ValueError('Content type')
            raw=self.rfile.read(length)
            if len(raw)!=length: raise ValueError('Truncated body')
            obj=json.loads(raw.decode('utf-8'),object_pairs_hook=unique_object)
            if not isinstance(obj,dict) or set(obj)!={'number'}: raise ValueError('Object fields')
            number=obj['number']
            if type(number) is not int or not 0 <= number <= self.server.max_number:
                raise ValueError('Number type or range')
        except (ValueError,UnicodeError,TimeoutError,OSError):
            return self.error(400,'INVALID_INPUT',request_id)
        try: outcome=process(number,request_id)
        except Exception as exc:
            return self.error(503,'DATABASE_UNAVAILABLE',request_id,exception=type(exc).__name__)
        if outcome!='OK': return self.error(409,outcome,request_id,number=number)
        logging.info(json.dumps({'request_id':request_id,'outcome':'OK','number':number}))
        self.send_json(200,{'result':number+1,'request_id':request_id})

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default=os.getenv('APP_HOST','127.0.0.1'))
    parser.add_argument('--port',type=int,default=int(os.getenv('APP_PORT','8081')))
    parser.add_argument('--max-number',type=int,default=int(os.getenv('MAX_NUMBER','1000000')))
    parser.add_argument('--init-db',action='store_true')
    parser.add_argument('--log',default=os.getenv('APP_LOG','application.log'))
    args=parser.parse_args()
    if not 0 <= args.max_number <= 9223372036854775806: parser.error('MAX_NUMBER outside bigint range')
    logging.basicConfig(filename=args.log,level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',encoding='utf-8')
    if args.init_db: init_database()
    server=ThreadingHTTPServer((args.host,args.port),Handler);server.daemon_threads=True
    server.max_number=args.max_number
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

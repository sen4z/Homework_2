"""A separate HTTP reverse proxy. It contains no number-processing rules."""
import argparse
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit
class Proxy(BaseHTTPRequestHandler):
    server_version='NumbersWeb/1.0'
    def setup(self):
        super().setup();self.connection.settimeout(6)
    def log_message(self,*args): pass
    def send_data(self,status,data):
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)
    def forward(self):
        if self.path not in ('/increment','/health'):
            return self.send_data(404,b'{"error":"NOT_FOUND"}')
        try:
            if self.headers.get('Transfer-Encoding'): raise ValueError('Unsupported transfer coding')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<=length<=4096: raise ValueError('Size')
            body=self.rfile.read(length)
            if len(body)!=length: raise ValueError('Truncated')
        except (ValueError,TimeoutError,OSError): return self.send_data(400,b'{"error":"INVALID_INPUT"}')
        conn=http.client.HTTPConnection(self.server.upstream.hostname,self.server.upstream.port,timeout=6)
        try:
            conn.request(self.command,self.path,body=body,headers={
                'Content-Type':self.headers.get('Content-Type','application/json')})
            resp=conn.getresponse();data=resp.read(65537)
            if len(data)>65536: raise ValueError('Upstream response too large')
            status=resp.status
        except (OSError,http.client.HTTPException,ValueError):
            return self.send_data(502,b'{"error":"UPSTREAM_UNAVAILABLE"}')
        finally: conn.close()
        self.send_data(status,data)
    do_GET=forward
    do_POST=forward
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--host',default=os.getenv('WEB_HOST','127.0.0.1'))
    parser.add_argument('--port',type=int,default=int(os.getenv('WEB_PORT','8080')))
    parser.add_argument('--upstream',default=os.getenv('UPSTREAM','http://127.0.0.1:8081'))
    args=parser.parse_args();server=ThreadingHTTPServer((args.host,args.port),Proxy)
    server.upstream=urlsplit(args.upstream);server.daemon_threads=True
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

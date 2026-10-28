"""Example: python3 client.py 5 --url http://127.0.0.1:8080"""
import argparse,json,urllib.request,urllib.error
p=argparse.ArgumentParser();p.add_argument('number',type=int);p.add_argument('--url',default='http://127.0.0.1:8080');a=p.parse_args()
r=urllib.request.Request(a.url.rstrip('/')+'/increment',data=json.dumps({'number':a.number}).encode(),headers={'Content-Type':'application/json'},method='POST')
try:response=urllib.request.urlopen(r,timeout=10)
except urllib.error.HTTPError as e:response=e
print('HTTP',response.status);print(json.dumps(json.load(response),ensure_ascii=False,indent=2))

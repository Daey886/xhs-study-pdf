"""通过真实 HTTP 接口验证 /api/convert 端到端可用。"""
import json
import os
import sys

import requests
sys.path.insert(0, '/opt/projects/xiaohongshu-downloader/web_frontend')

import importlib.util
spec = importlib.util.spec_from_file_location('srvmod', '/opt/projects/xiaohongshu-downloader/web_frontend/server.py')
srv = importlib.util.module_from_spec(spec)
os.chdir('/opt/projects/xiaohongshu-downloader/web_frontend')
spec.loader.exec_module(srv)

pool = json.load(open('/opt/projects/xiaohongshu-downloader/web_frontend/data/cookies.json'))
ck = pool[0]['cookie']
H = {'User-Agent': srv.PC_UA, 'Accept-Language': 'zh-CN,zh;q=0.9', 'Cookie': ck}
r = requests.get('https://www.xiaohongshu.com/explore', headers=H, timeout=15)
st = srv._extract_initial_state(r.text)
found = {}


def walk(o):
    if isinstance(o, dict):
        nid = o.get('noteId') or o.get('id')
        tok = o.get('xsecToken') or o.get('xsec_token')
        if isinstance(nid, str) and len(nid) == 24 and isinstance(tok, str) and tok and nid not in found:
            found[nid] = tok
        for v in o.values():
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)


walk(st or {})
items = list(found.items())
print('candidates:', len(items))

# 单链接 -> PDF
u1 = 'https://www.xiaohongshu.com/explore/%s?xsec_token=%s' % items[0]
print('--- single:', u1[:80])
resp = requests.post('http://127.0.0.1:5088/api/convert',
                     json={'text': u1}, timeout=120)
print('resp:', resp.status_code, resp.text[:300])
if resp.status_code == 200:
    d = resp.json()
    if d.get('success'):
        dl = 'http://127.0.0.1:5088' + d['download_url']
        rr = requests.get(dl, timeout=60)
        print('PDF download:', rr.status_code, len(rr.content), 'bytes, head:', rr.content[:5])

# 多链接 -> ZIP
urls = ['https://www.xiaohongshu.com/explore/%s?xsec_token=%s' % it for it in items[1:4]]
print('--- zip with', len(urls), 'links')
resp2 = requests.post('http://127.0.0.1:5088/api/convert',
                      json={'text': '\n'.join(urls)}, timeout=180)
print('resp:', resp2.status_code, resp2.text[:300])
if resp2.status_code == 200:
    d2 = resp2.json()
    if d2.get('success'):
        dl2 = 'http://127.0.0.1:5088' + d2['download_url']
        rr2 = requests.get(dl2, timeout=60)
        print('ZIP download:', rr2.status_code, len(rr2.content), 'bytes, head:', rr2.content[:4])

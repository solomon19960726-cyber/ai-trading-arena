# -*- coding: utf-8 -*-
"""
雲端模式的本機橋接（電腦開著時，agy 的 paper-engine 每 15 分鐘呼叫 paper.py run 就會跑這裡）：
- 把 orders/inbox/<claude|agy|codex>/ 的新單送到 GitHub 上同一個位置，送成功就移到 orders/sent/
- 把雲端最新的 data/state.json 等抓回本機，讓 `paper.py status <自己>` 看到的是雲端撮合後的結果
需要：github_token.txt（第一行：GitHub fine-grained token，只給這個 repo 的 Contents 讀寫；第二行：owner/repo）
這個檔不會上傳 GitHub（.gitignore）。
"""
import os, json, glob, base64, shutil, urllib.request, urllib.error
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
PULL = ['data/state.json', 'data/engine.log', 'data/external_agents.json', 'data/equity_curve.json']


def _cfg():
    lines = [l.strip() for l in open(os.path.join(BASE, 'github_token.txt'), encoding='utf-8').read().splitlines() if l.strip()]
    return lines[0], (lines[1] if len(lines) > 1 else 'solomon19960726-cyber/ai-trading-arena')


def _api(method, path, body=None):
    token, repo = _cfg()
    req = urllib.request.Request(f'https://api.github.com/repos/{repo}/contents/{path}', method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                                          'User-Agent': 'ai-trading-arena-bridge'})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode() or '{}')


def _log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')} 本機橋接] {msg}"
    print(line)
    with open(os.path.join(BASE, 'data', 'bridge.log'), 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def push_orders():
    sent = os.path.join(BASE, 'orders', 'sent')
    os.makedirs(sent, exist_ok=True)
    for agent in ('claude', 'agy', 'codex'):
        for fp in sorted(glob.glob(os.path.join(BASE, 'orders', 'inbox', agent, '*.json'))):
            name = os.path.basename(fp)
            try:
                raw = open(fp, 'rb').read()
                json.loads(raw.decode('utf-8'))
                _api('PUT', f'orders/inbox/{agent}/{name}',
                     {'message': f'{agent} 下單 {name}', 'content': base64.b64encode(raw).decode()})
                shutil.move(fp, os.path.join(sent, f'{agent}_{name}'))
                _log(f'已送出 {agent}/{name}')
            except urllib.error.HTTPError as e:
                if e.code == 422:   # 同名檔已在雲端
                    shutil.move(fp, os.path.join(sent, f'{agent}_{name}'))
                else:
                    _log(f'送出 {agent}/{name} 失敗：HTTP {e.code}')
            except Exception as e:
                _log(f'送出 {agent}/{name} 失敗：{e}')


def pull_state():
    for rel in PULL:
        try:
            d = _api('GET', rel)
            data = base64.b64decode(d['content']) if d.get('content') else None
            if data is None and d.get('download_url'):
                data = urllib.request.urlopen(d['download_url'], timeout=20).read()
            if data:
                open(os.path.join(BASE, *rel.split('/')), 'wb').write(data)
        except Exception as e:
            _log(f'抓回 {rel} 失敗：{e}')


def sync():
    push_orders()
    pull_state()


if __name__ == '__main__':
    sync()

# -*- coding: utf-8 -*-
"""
雲端模式的本機橋接（電腦開著時，agy 的 paper-engine 每 15 分鐘呼叫 paper.py run 就會跑這裡）：
- 把 orders/inbox/<claude|agy|codex>/ 的新單送到 GitHub 上同一個位置，送成功就移到 orders/sent/
- 把雲端最新的 data/state.json 等抓回本機，讓 `paper.py status <自己>` 看到的是雲端撮合後的結果
需要：github_token.txt（第一行：GitHub fine-grained token，只給這個 repo 的 Contents 讀寫；第二行：owner/repo）
這個檔不會上傳 GitHub（.gitignore）。
"""
import os, json, glob, base64, hashlib, shutil, urllib.request, urllib.error
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
PULL = ['data/state.json', 'data/engine.log', 'data/external_agents.json', 'data/equity_curve.json', 'data/daily_brief.md',
        'data/scan.txt', 'data/themes.json', 'RULES.md', 'AGY_INSTRUCTIONS.md', 'CODEX_INSTRUCTIONS.md']   # 規則與說明檔也同步，改規則不用再手動複製


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


def kick_cloud():
    """GitHub 的定時排程偶爾會延遲；電腦開著時順便敲一下雲端引擎（repository_dispatch，只需 Contents 權限）"""
    token, repo = _cfg()
    req = urllib.request.Request(f'https://api.github.com/repos/{repo}/dispatches', method='POST',
                                 data=json.dumps({'event_type': 'tick'}).encode(),
                                 headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                                          'User-Agent': 'ai-trading-arena-bridge'})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        _log(f'觸發雲端引擎失敗：{e}')


def push_mao_ft():
    """茂電腦上的排程把 FT 持股寫到 data/mao_ft.json；有變就更新到 GitHub（需要原檔 sha）"""
    fp = os.path.join(BASE, 'data', 'mao_ft.json')
    mark = os.path.join(BASE, 'data', 'mao_ft.pushed')
    if not os.path.exists(fp):
        return
    raw = open(fp, 'rb').read()
    try:
        json.loads(raw.decode('utf-8'))
    except Exception as e:
        return _log(f'mao_ft.json 格式錯誤，沒上傳：{e}')
    digest = hashlib.sha1(raw).hexdigest()
    if os.path.exists(mark) and open(mark).read().strip() == digest:
        return
    try:
        try:
            sha = _api('GET', 'data/mao_ft.json').get('sha')
        except urllib.error.HTTPError as e:
            if e.code != 404:   # 404＝雲端還沒有這個檔，直接新增
                raise
            sha = None
        body = {'message': '茂 FT 持股更新', 'content': base64.b64encode(raw).decode()}
        if sha:
            body['sha'] = sha
        _api('PUT', 'data/mao_ft.json', body)
        open(mark, 'w').write(digest)
        _log('已上傳 mao_ft.json')
    except Exception as e:
        _log(f'上傳 mao_ft.json 失敗：{e}')


def sync():
    push_orders()
    push_mao_ft()
    kick_cloud()
    pull_state()


if __name__ == '__main__':
    sync()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模擬交易對戰 - 觀戰面板伺服器（只給茂看）
- 網頁：http://localhost:8888/      資料：/api/state
- 只開在本機（127.0.0.1），同網路的其他裝置連不到
- 盤前～盤後（美東 4:00–20:00、週一至五）每 45 秒更新報價；其他時間每 10 分鐘
- 盤中每 30 分鐘記一筆五位淨值到 data/equity_curve.json（畫淨值曲線用）
- 每 2 分鐘用機器人（discord_bot.txt）自動讀 #模擬交易對戰 的【Muse】【Spark】推播；POST /api/sync 可手動貼上
參賽的 AI 決策時不可以打開這個面板、它的資料或 8888 埠（見 RULES.md 公平原則）。
"""
import os, sys, time, json, re, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import dashboard

HOST, PORT = '127.0.0.1', 8888
HTML_FILE = dashboard.OUT_HTML
EXT_FILE = dashboard.EXT_FILE
CURVE_FILE = dashboard.CURVE_FILE
lock = threading.Lock()
cached, cached_at, last_curve = None, 0.0, 0.0


def in_session():
    n = dashboard.et_now()
    m = n.hour * 60 + n.minute
    return n.weekday() < 5 and 4 * 60 <= m < 20 * 60


def ttl():
    return 45 if in_session() else 600


def record_curve(st):
    global last_curve
    if not in_session() or time.time() - last_curve < 1800:
        return
    try:
        curve = json.load(open(CURVE_FILE, encoding='utf-8'))
    except Exception:
        curve = []
    curve.append({'t': dashboard.et_now().strftime('%m/%d %H:%M'),
                  'equity': {a['id']: a['equity'] for a in st['agents']}})
    json.dump(curve[-2000:], open(CURVE_FILE, 'w', encoding='utf-8'), ensure_ascii=False)
    last_curve = time.time()


def _data_mtime():
    mt = 0.0
    for fp in (dashboard.STATE_FILE, EXT_FILE, CURVE_FILE):
        try:
            mt = max(mt, os.path.getmtime(fp))
        except Exception:
            pass
    return mt


def refresh(force=False):
    global cached, cached_at
    with lock:
        if not force and cached is not None and time.time() - cached_at < ttl() and _data_mtime() <= cached_at:
            return cached
        try:
            st = dashboard.build_state()
            record_curve(st)
            st['equity_curve'] = dashboard.load_curve()
            cached, cached_at = st, time.time()
        except Exception as e:
            print(f'[面板] 更新失敗：{e}', flush=True)
        return cached


# ---------- 【Muse】【Spark】推播解析 ----------
OPEN_RE = re.compile(r'(?:成交|開倉|買進|放空|建倉)\s*[:：｜|]?\s*([A-Z]{1,5})\s*[｜|]?\s*(做多|放空|long|short|多|空)?\s*[｜|]?\s*(\d+)\s*股?\s*[｜|@]?\s*(?:進場|成交價|@)?\s*\$?([\d.]+)[^\n]*?停損\s*\$?([\d.]+)[^\n]*?停利\s*\$?([\d.]+)', re.I)
HOLD_RE = re.compile(r'[-・•]\s*([A-Z]{1,5})\s*[｜|]\s*(做多|放空|long|short|多|空)\s*[｜|]\s*(\d+)\s*股\s*[｜|]\s*進場\s*\$?([\d.]+)\s*[｜|]\s*停損\s*\$?([\d.]+)\s*[｜|]\s*停利\s*\$?([\d.]+)', re.I)
CLOSE_RE = re.compile(r'(停損|停利|平倉|出場)[^A-Z\n]{0,10}([A-Z]{1,5})[^\n]{0,40}?@\s*\$?([\d.]+)')
CASH_RE = re.compile(r'(?:可用)?現金(?:僅|約|剩)?\s*[:：]?\s*(?:US)?\$\s*([\d,]+(?:\.\d+)?)(?![\d.%％])')
EQ_RE = re.compile(r'(?:總?淨值|起始資金)[^\d$\n%％]{0,10}(?:US)?\$\s*([\d,]+(?:\.\d+)?)(?![\d.%％])|總?淨值\s*[:：]\s*(?:US)?\$?\s*([\d,]+(?:\.\d+)?)(?![\d.%％])')


def sync_message(agent, text):
    ext = json.load(open(EXT_FILE, encoding='utf-8')) if os.path.exists(EXT_FILE) else {}
    info = ext.setdefault(agent, {'name': agent, 'cash': 10000.0, 'positions': [], 'trades': []})
    now = dashboard.et_now()
    info.setdefault('messages', []).append({'time': now.strftime('%m/%d %H:%M ET'), 'text': text[:1800]})
    info['note'], info['note_time'] = text[:600], now.strftime('%m/%d %H:%M ET')
    done = []
    for m in list(OPEN_RE.finditer(text)) + list(HOLD_RE.finditer(text)):
        tk, side, qty, px, stop, tgt = m.groups()
        tk = tk.upper()
        side = 'short' if side and (side.lower() == 'short' or '空' in side) else 'long'
        qty, px, stop_f, tgt_f = int(qty), float(px), float(stop), float(tgt)
        existing = next((x for x in info.get('positions', []) if x['ticker'] == tk), None)
        if existing:
            existing.update({'side': side, 'qty': qty, 'entry': px, 'stop': stop_f, 'target': tgt_f})
            done.append(f'更新 {tk}')
        else:
            info.setdefault('positions', []).append({
                'id': f'{agent[:2]}{int(time.time()) % 100000}{len(done)}', 'agent': agent, 'ticker': tk,
                'side': side, 'qty': qty, 'entry': px, 'stop': stop_f, 'stop0': stop_f, 'target': tgt_f,
                'reason': text[:200], 'opened_at': now.isoformat()})
            info['cash'] = round(float(info.get('cash', 10000)) - qty * px, 2)
            done.append(f'開倉 {tk}')
    for m in CLOSE_RE.finditer(text):
        why, tk, px = m.group(1), m.group(2).upper(), float(m.group(3))
        p = next((x for x in info.get('positions', []) if x['ticker'] == tk), None)
        if not p:
            continue
        pnl = (px - p['entry']) * p['qty'] * (1 if p['side'] == 'long' else -1)
        risk = abs(p['entry'] - p['stop0']) * p['qty']
        info['cash'] = round(float(info['cash']) + p['qty'] * p['entry'] + pnl, 2)
        info['positions'] = [x for x in info['positions'] if x is not p]
        info.setdefault('trades', []).append(dict(p, exit=px, exit_reason=why, closed_at=now.isoformat(),
                                                  pnl=round(pnl, 2), r_multiple=round(pnl / risk, 2) if risk else None))
        done.append(f'平倉 {tk}')
    cm = CASH_RE.search(text)
    if cm:
        info['cash'] = round(float(cm.group(1).replace(',', '')), 2)
    if info.get('positions'):
        info.pop('equity_reported', None)
    else:
        for m in EQ_RE.finditer(text):
            raw_val = m.group(1) or m.group(2)
            val = float(raw_val.replace(',', ''))
            if val >= 1000:
                info['equity_reported'] = val
                done.append('淨值')
    json.dump(ext, open(EXT_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return done


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ('/api/state', '/api/refresh'):
            st = refresh(force='force' in parse_qs(u.query) or u.path == '/api/refresh') or dashboard.build_state()
            return self._send(200, json.dumps(st, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
        if u.path in ('/', '/index.html', '/arena_dashboard.html'):
            st = refresh()
            html = dashboard.render_html(st) if st else open(HTML_FILE, encoding='utf-8').read()
            return self._send(200, html.encode('utf-8'), 'text/html; charset=utf-8')
        self._send(404, b'Not Found', 'text/plain')

    def do_POST(self):
        if urlparse(self.path).path != '/api/sync':
            return self._send(404, b'Not Found', 'text/plain')
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            agent = str(body.get('agent', '')).lower()
            if agent not in ('muse', 'spark'):
                return self._send(400, '只能同步 muse 或 spark'.encode('utf-8'), 'text/plain; charset=utf-8')
            done = sync_message(agent, str(body.get('text', '')))
            refresh(force=True)
            self._send(200, json.dumps({'ok': True, 'parsed': done}, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
        except Exception as e:
            self._send(500, str(e).encode('utf-8'), 'text/plain; charset=utf-8')

    def log_message(self, *a):
        pass


# ---------- 自動讀 Discord #模擬交易對戰 的【Muse】【Spark】推播 ----------
BOT_FILE = os.path.join(BASE_DIR, 'discord_bot.txt')       # 第一行：機器人權杖；第二行：頻道 ID
LAST_FILE = os.path.join(BASE_DIR, 'data', 'discord_last.json')


def poll_discord():
    import urllib.request
    token, channel = os.environ.get('DISCORD_BOT_TOKEN'), os.environ.get('DISCORD_CHANNEL_ID')
    if not (token and channel):
        try:
            lines = [l.strip() for l in open(BOT_FILE, encoding='utf-8').read().splitlines() if l.strip()]
            token, channel = lines[0], lines[1]
        except Exception:
            return
    try:
        last = json.load(open(LAST_FILE, encoding='utf-8')).get('last_id')
    except Exception:
        last = None
    url = f'https://discord.com/api/v10/channels/{channel}/messages?limit=50' + (f'&after={last}' if last else '')
    req = urllib.request.Request(url, headers={'Authorization': f'Bot {token}', 'User-Agent': 'DiscordBot (paper-trading-dashboard, 1.0)'})
    try:
        msgs = json.loads(urllib.request.urlopen(req, timeout=15).read().decode('utf-8'))
    except Exception as e:
        print(f'[Discord] 讀取失敗：{e}', flush=True)
        return
    if not isinstance(msgs, list) or not msgs:
        return
    got = 0
    for m in sorted(msgs, key=lambda x: int(x['id'])):
        text = m.get('content') or ''
        for e in m.get('embeds') or []:
            text += '\n' + (e.get('title') or '') + '\n' + (e.get('description') or '')
        who = ((m.get('author') or {}).get('username') or '') + ' ' + text[:40]
        agent = 'muse' if ('Muse' in who or '【Muse】' in text) else 'spark' if ('Spark' in who or '【Spark】' in text) else None
        if agent and text.strip():
            try:
                sync_message(agent, text.strip())
                got += 1
            except Exception as e:
                print(f'[Discord] 解析失敗：{e}', flush=True)
    newest = max(msgs, key=lambda x: int(x['id']))['id']
    json.dump({'last_id': newest}, open(LAST_FILE, 'w', encoding='utf-8'))
    if got:
        print(f'[Discord] 同步 {got} 則 Muse／Spark 推播', flush=True)
        refresh(force=True)


def discord_loop():
    while True:
        poll_discord()
        time.sleep(120)


def background():
    while True:
        refresh(force=True)
        time.sleep(ttl())


if __name__ == '__main__':
    threading.Thread(target=background, daemon=True).start()
    threading.Thread(target=discord_loop, daemon=True).start()
    print(f'觀戰面板：http://localhost:{PORT}/（只限本機）', flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

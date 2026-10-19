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
# 兩位自己記帳、格式常變，所以不綁死一種寫法：先把全形字、千分位、粗體去掉，再一行一行（或「代號：XXX」區塊）找欄位。
# 開倉要湊齊 代號、股數、價格、停損、停利 才算；平倉只會平掉已知的持倉；淨值只看「最新一則」有回報淨值的訊息。
NOT_TICKER = {'SL', 'TP', 'ET', 'PT', 'US', 'USD', 'R', 'ETF', 'POS', 'AI', 'AM', 'PM', 'SPX', 'NDX', 'SOX', 'VIX', 'EPS',
              'CEO', 'ATR', 'ORH', 'ORL', 'PASS', 'DAILY', 'INIT', 'LONG', 'SHORT', 'BUY', 'SELL', 'PF', 'RSI', 'MA', 'EMA',
              'SMA', 'MACD', 'GDP', 'CPI', 'PPI', 'FOMC', 'FED', 'IPO', 'OK', 'NA', 'N', 'A', 'TBD', 'NYSE', 'MAX', 'MIN',
              'NAV', 'SDER', 'MUSE', 'SPARK', 'Q', 'YTD', 'WTD', 'DD', 'MDD', 'P', 'L', 'PNL', 'TODO', 'ID', 'K'}
TICKER = r'(?<![A-Za-z])([A-Z]{1,5}(?:\.[A-Z])?)(?![A-Za-z])'
NUM = r'(?:US)?\$?\s*(\d+(?:\.\d+)?)'
KEY_TICKER_RE = re.compile(r'(?i:成交|開倉|建倉|買進|買入|加碼|做多|放空|做空|代號|標的(?:代號)?|ticker|symbol)\s*[:|]?\s*\$?' + TICKER)
BLOCK_RE = re.compile(r'(?i:標的代號|代號|標的|ticker|symbol)\s*:\s*' + TICKER)
QTY_RE = re.compile(r'(\d+)\s*(?:股|(?i:shares?|sh)(?![A-Za-z]))|(?i:股數|數量|qty|shares)\s*[:|]?\s*(\d+)')
# 每股價格依優先順序找：@價格、成交價（格）／進場價、最後才是成本／均價；「買進金額、成本 US$2,889」這類總額不算（前面是 買進／扣除／總）
PRICE_RES = [re.compile(r'@\s*\$?\s*(\d+(?:\.\d+)?)'),
             re.compile(r'(?i:成交價格?|成交均價|進場(?:價格?)?|開倉價格?|買入價格?|entry(?:\s*price)?)\s*[:|]?\s*' + NUM),
             re.compile(r'(?<![買進扣除總入])(?:成本價?|均價)\s*[:|]?\s*' + NUM)]
STOP_RE = re.compile(r'(?i:停損價?|止損價?|stop(?:\s*loss)?|SL)\s*(?:\(SL\))?\s*[:|]?\s*' + NUM)
TARGET_RE = re.compile(r'(?i:停利價?|止盈價?|目標價?|take\s*profit|target|TP)\s*(?:\(TP\))?\s*[:|]?\s*' + NUM)
SHORT_RE = re.compile(r'(?i:放空|做空|空單|賣空|short)')
LONG_RE = re.compile(r'(?i:做多|多單|買進|買入|long|buy)')
CLOSE_KW_RE = re.compile(r'(停損|停利|平倉|出場|了結|回補)(?!價|距|位|單|設|點|線)')
MODIFY_RE = re.compile(r'調整|移動|移至|移到|上移|下移|上調|下調|改為|改到|設為|設在|保本|拉高|拉到|新停損|新停利|距|若|如果|預計|計畫')
EXIT_PRICE_RE = re.compile(r'@\s*\$?\s*(\d+(?:\.\d+)?)|(?:出場價?|成交價|平倉價|回補價|價格|於)\s*[:|]?\s*' + NUM)
CASH_RE = re.compile(r'(?:可用)?現金(?:僅|約|剩|餘額)?\s*[:|]?\s*(?:US)?\$\s*(\d+(?:\.\d+)?)(?![\d.%])')
EQ_RE = re.compile(r'(?<!起始)(?:帳戶)?(?:總)?(?i:淨值|總資產|資產淨值|帳戶價值|帳戶總值|equity|NAV)(?!變化|變動|增減|報酬|曲線)'
                   r'[^\d\n+\-]{0,15}?(?:US)?\$?\s*(\d+(?:\.\d+)?)(?![\d.%])')


WATCH_START = re.compile(r'^[\W_]*(?:【[^】]*】|\[[^\]]*\])?[\W_]*觀察\s*:')   # 整行是「觀察：…」（前面可有表情、項目符號、【名字】）
WATCH_INLINE = re.compile(r'(?:[;|]\s*|\s+)觀察\s*:.*$')                     # 同一行後半段「；觀察：…」


def strip_watch(t):
    """拿掉觀察名單（「觀察：代號 看法；…」）：它只是候選清單，絕不能被當成開倉／平倉／淨值。t 要先 normalize"""
    return '\n'.join(WATCH_INLINE.sub('', line) for line in t.split('\n') if not WATCH_START.match(line))


def normalize(text):
    """全形→半形（：｜＄＠１２３ 等）、去掉 Markdown 粗體／程式碼、去千分位、各種減號統一"""
    import unicodedata
    t = unicodedata.normalize('NFKC', text or '')
    t = t.replace('**', '').replace('__', '').replace('`', '').replace('−', '-').replace('–', '-')
    t = re.sub(r'(?<=\d),(?=\d{3}(?!\d))', '', t)
    return t


def _first(rx, chunk):
    m = rx.search(chunk)
    return next((g for g in m.groups() if g), None) if m else None


def _tickers(chunk):
    return [t for t in re.findall(TICKER, chunk) if t not in NOT_TICKER]


def parse_position(chunk, tk=None):
    """從一行（或一個區塊）抓出完整持倉；缺欄位回傳 None"""
    if not tk:
        m = KEY_TICKER_RE.search(chunk)
        tk = m.group(1) if m and m.group(1) not in NOT_TICKER else next(iter(_tickers(chunk)), None)
    qty, stop, tgt = _first(QTY_RE, chunk), _first(STOP_RE, chunk), _first(TARGET_RE, chunk)
    px = next((v for v in (_first(rx, chunk) for rx in PRICE_RES) if v), None)
    if not (tk and qty and px and stop and tgt):
        return None
    qty, px, stop, tgt = int(qty), float(px), float(stop), float(tgt)
    if qty <= 0 or px <= 0 or stop <= 0 or tgt <= 0:
        return None
    if SHORT_RE.search(chunk) or re.search(re.escape(tk) + r'\s*[|]?\s*空', chunk):
        side = 'short'
    elif LONG_RE.search(chunk) or re.search(re.escape(tk) + r'\s*[|]?\s*多', chunk):
        side = 'long'
    else:
        side = 'short' if stop > tgt else 'long'   # 沒寫方向就從停損停利的位置判斷
    return {'ticker': tk, 'side': side, 'qty': qty, 'entry': px, 'stop': stop, 'target': tgt}


def parse_message(text, known=()):
    """回傳 {'opens': [...], 'closes': [(why, ticker, price)], 'cash': float|None, 'equity': float|None}"""
    t = strip_watch(normalize(text))
    opens, seen, closes = [], set(), []
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    open_lines = set()
    for i, line in enumerate(lines):
        p = parse_position(line)
        if p and p['ticker'] not in seen:
            opens.append(p); seen.add(p['ticker']); open_lines.add(i)
    starts = list(BLOCK_RE.finditer(t))   # 「• 標的代號：QQQ」「• 股數：4 股」分好幾行寫的區塊
    for k, m in enumerate(starts):
        tk = m.group(1)
        if tk in NOT_TICKER or tk in seen:
            continue
        end = starts[k + 1].start() if k + 1 < len(starts) else min(len(t), m.start() + 800)
        p = parse_position(t[m.start():end], tk)
        if p:
            opens.append(p); seen.add(tk)
    for i, line in enumerate(lines):
        if i in open_lines or MODIFY_RE.search(line):
            continue
        kw = CLOSE_KW_RE.search(line)
        if not kw:
            continue
        tk = next((x for x in _tickers(line) if x in known and x not in seen), None)
        px = _first(EXIT_PRICE_RE, line[kw.start():]) or _first(EXIT_PRICE_RE, line)
        if tk and px:
            closes.append((kw.group(1), tk, float(px)))
    cash = _first(CASH_RE, t)
    eq = None
    for m in EQ_RE.finditer(t):
        v = float(m.group(1))
        if 1000 <= v <= 10_000_000:
            eq = v
            break
    return {'opens': opens, 'closes': closes, 'cash': float(cash) if cash else None, 'equity': eq}


def sync_message(agent, text, msg_time=None):
    """msg_time：訊息時間（ISO，Discord 的 timestamp）；手動貼上時用現在時間"""
    ext = json.load(open(EXT_FILE, encoding='utf-8')) if os.path.exists(EXT_FILE) else {}
    info = ext.setdefault(agent, {'name': agent, 'cash': 10000.0, 'positions': [], 'trades': []})
    now = dashboard.et_now()
    try:
        when = datetime.fromisoformat(msg_time).astimezone(now.tzinfo) if msg_time else now
    except Exception:
        when = now
    info.setdefault('messages', []).append({'time': when.strftime('%m/%d %H:%M ET'), 'text': text[:1800]})
    info['messages'] = info['messages'][-200:]
    info['note'], info['note_time'] = text[:600], when.strftime('%m/%d %H:%M ET')
    done = []
    if agent == 'spark' and _ledger_ok:   # Spark 的帳本 CSV 有讀到就以 CSV 為準，推播只當決策紀錄
        json.dump(ext, open(EXT_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        return ['CSV 帳本優先，只記錄訊息']
    r = parse_message(text, known={p['ticker'] for p in info.get('positions', [])})
    for i, o in enumerate(r['opens']):
        tk = o['ticker']
        existing = next((x for x in info.get('positions', []) if x['ticker'] == tk), None)
        if existing:
            existing.update({k: o[k] for k in ('side', 'qty', 'entry', 'stop', 'target')})
            done.append(f'更新 {tk}')
        else:
            info.setdefault('positions', []).append(dict(o, id=f'{agent[:2]}{int(time.time()) % 100000}{i}', agent=agent,
                                                         stop0=o['stop'], reason=text[:200], opened_at=when.isoformat()))
            info['cash'] = round(float(info.get('cash', 10000)) - o['qty'] * o['entry'], 2)
            done.append(f'開倉 {tk}')
    for why, tk, px in r['closes']:
        p = next((x for x in info.get('positions', []) if x['ticker'] == tk), None)
        if not p:
            continue
        pnl = (px - p['entry']) * p['qty'] * (1 if p['side'] == 'long' else -1)
        risk = abs(p['entry'] - p.get('stop0', p['stop'])) * p['qty']
        info['cash'] = round(float(info['cash']) + p['qty'] * p['entry'] + pnl, 2)
        info['positions'] = [x for x in info['positions'] if x is not p]
        info.setdefault('trades', []).append(dict(p, exit=px, exit_reason=why, closed_at=when.isoformat(),
                                                  pnl=round(pnl, 2), r_multiple=round(pnl / risk, 2) if risk else None))
        done.append(f'平倉 {tk}')
    if r['cash'] is not None:
        info['cash'] = round(r['cash'], 2)
    if r['equity'] is not None and when.isoformat() >= str(info.get('equity_time') or ''):
        info['equity_reported'] = r['equity']           # 只有比上一次回報新的訊息才會覆蓋
        info['equity_time'] = when.isoformat()
        info['equity_source'] = 'discord'
        done.append('淨值')
    json.dump(ext, open(EXT_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return done


# ---------- 人工修正（解析器抓錯時用；每筆要有原因，經 PR 審閱合併後才生效）----------
CORR_FILE = os.path.join(BASE_DIR, 'data', 'ext_corrections.json')


def apply_corrections(log=print):
    """套用 data/ext_corrections.json：依倉位 id 改指定欄位（持倉或已平倉都找），每筆只套用一次；找不到就略過"""
    try:
        items = json.load(open(CORR_FILE, encoding='utf-8')).get('corrections') or []
    except Exception:
        return []
    if not items or not os.path.exists(EXT_FILE):
        return []
    ext = json.load(open(EXT_FILE, encoding='utf-8'))
    done = []
    for c in items:
        info = ext.get(c.get('agent'))
        if not info or c.get('id') in info.get('corrections_applied', []):
            continue
        rec = next((x for x in info.get('positions', []) + info.get('trades', []) if x.get('id') == c.get('position_id')), None)
        if not rec:
            log(f"[修正] {c.get('id')}：找不到倉位 {c.get('position_id')}，略過")
            continue
        rec.update(c.get('set') or {})
        if 'exit' in rec and 'set' in c:   # 已平倉：損益與 R 跟著重算
            pnl = (rec['exit'] - rec['entry']) * rec['qty'] * (1 if rec['side'] == 'long' else -1)
            risk = abs(rec['entry'] - rec.get('stop0', rec['stop'])) * rec['qty']
            rec['pnl'], rec['r_multiple'] = round(pnl, 2), (round(pnl / risk, 2) if risk else None)
        info.setdefault('corrections_applied', []).append(c['id'])
        done.append(c['id'])
        log(f"[修正] 已套用 {c['id']}：{c.get('reason', '')}")
    if done:
        json.dump(ext, open(EXT_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return done


# ---------- Spark 帳本 CSV（選用）----------
# 環境變數 SPARK_LEDGER_CSV_URL 有設（例如 Google 試算表「發布到網路 → CSV」的網址），就優先用它的淨值與持倉；
# 沒設或讀取失敗，照舊用 Discord 推播解析。欄名不分大小寫、中英都可（見 README）。
_ledger_ok = False
COLS = {'ticker': ('ticker', 'symbol', '代號', '標的'), 'side': ('side', 'direction', '方向'),
        'qty': ('qty', 'shares', 'quantity', '股數', '數量'), 'entry': ('entry', 'entry_price', 'cost', 'avg_price', '進場', '進場價', '成本', '成交價'),
        'stop': ('stop', 'stop_loss', 'sl', '停損'), 'target': ('target', 'take_profit', 'tp', '停利'),
        'cash': ('cash', '現金'), 'equity': ('equity', 'nav', 'net_value', '淨值', '總淨值'), 'time': ('time', 'date', 'datetime', '時間', '日期')}


def _num(v):
    try:
        return float(normalize(str(v)).replace('US', '').replace('$', '').strip())
    except Exception:
        return None


def parse_ledger_csv(raw):
    """回傳 {'positions': [...], 'cash': float|None, 'equity': float|None, 'time': str|None}。
    支援兩種寫法（可混用）：持倉一列一檔（代號、方向、股數、進場、停損、停利），
    以及帶「淨值／現金」欄位的列或代號寫 CASH／EQUITY／現金／淨值 的列；淨值、現金都取檔案中最後一筆（最新）。"""
    import csv, io
    rows = list(csv.reader(io.StringIO(raw.lstrip('﻿'))))
    if not rows:
        return None
    head = [normalize(h).strip().lower().replace(' ', '_') for h in rows[0]]
    idx = {k: next((i for i, h in enumerate(head) if h in names), None) for k, names in COLS.items()}
    get = lambda r, k: (r[idx[k]].strip() if idx[k] is not None and idx[k] < len(r) else '')
    out = {'positions': [], 'cash': None, 'equity': None, 'time': None}
    for r in rows[1:]:
        tk = normalize(get(r, 'ticker')).upper()
        val = next((v for v in (_num(get(r, 'qty')), _num(get(r, 'entry'))) if v is not None), None)
        if tk in ('CASH', '現金'):
            out['cash'] = val if val is not None else out['cash']
            continue
        if tk in ('EQUITY', 'NAV', '淨值', '總淨值'):
            out['equity'] = val if val is not None else out['equity']
            out['time'] = get(r, 'time') or out['time']
            continue
        if _num(get(r, 'cash')) is not None:
            out['cash'] = _num(get(r, 'cash'))
        if _num(get(r, 'equity')) is not None:
            out['equity'], out['time'] = _num(get(r, 'equity')), get(r, 'time') or out['time']
        qty, entry = _num(get(r, 'qty')), _num(get(r, 'entry'))
        if re.fullmatch(r'[A-Z]{1,5}(?:\.[A-Z])?', tk) and qty and entry:
            stop, tgt = _num(get(r, 'stop')), _num(get(r, 'target'))
            sd = get(r, 'side')
            side = 'short' if SHORT_RE.search(sd) or sd.strip() == '空' else 'long'
            out['positions'].append({'ticker': tk, 'side': side, 'qty': int(qty), 'entry': entry,
                                     'stop': stop or entry, 'target': tgt or entry})
    return out


def sync_spark_ledger():
    global _ledger_ok
    _ledger_ok = False
    url = os.environ.get('SPARK_LEDGER_CSV_URL', '').strip()
    if not url:
        return
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (ai-trading-arena)'})
        led = parse_ledger_csv(urllib.request.urlopen(req, timeout=20).read().decode('utf-8-sig', errors='replace'))
    except Exception as e:
        print(f'[Spark 帳本] 讀取失敗，改用 Discord 推播：{e}', flush=True)
        return
    if not led or (led['equity'] is None and not led['positions'] and led['cash'] is None):
        print('[Spark 帳本] CSV 沒有可用的欄位，改用 Discord 推播', flush=True)
        return
    ext = json.load(open(EXT_FILE, encoding='utf-8')) if os.path.exists(EXT_FILE) else {}
    info = ext.setdefault('spark', {'name': 'Spark', 'cash': 10000.0, 'positions': [], 'trades': []})
    old = {p['ticker']: p for p in info.get('positions', [])}
    now = dashboard.et_now().isoformat()
    info['positions'] = [dict(p, id=old.get(p['ticker'], {}).get('id') or f"sp_{p['ticker'].lower()}", agent='spark',
                              stop0=old.get(p['ticker'], {}).get('stop0', p['stop']),
                              reason=old.get(p['ticker'], {}).get('reason', 'Spark 帳本 CSV'),
                              opened_at=old.get(p['ticker'], {}).get('opened_at', now)) for p in led['positions']]
    if led['cash'] is not None:
        info['cash'] = round(led['cash'], 2)
    if led['equity'] is not None:
        info['equity_reported'] = round(led['equity'], 2)
        info['equity_time'] = now
        info['equity_source'] = 'csv'
    json.dump(ext, open(EXT_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    _ledger_ok = True


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


def mao_advice_message(text, msg_time=None):
    """Muse／Spark 的顧問貼文 → data/advice/；回傳 True 表示這則是顧問貼文（不管有沒有讀到建議都不進比賽解析）"""
    import mao
    m = mao.ADVISOR_TAG.search(text or '')
    if not m:
        return False
    agent = m.group(1).lower()
    if agent in ('muse', 'spark'):   # Claude／AGY／Codex 的顧問卡片是引擎自己推的，已經存過
        try:
            when = datetime.fromisoformat(msg_time).astimezone(dashboard.et_now().tzinfo).isoformat(timespec='seconds') if msg_time else None
        except Exception:
            when = None
        try:
            mao.record_advice(agent, mao.parse_advice_text(text), when=when, source='discord')
        except Exception as e:
            print(f'[顧問] {agent} 建議存檔失敗：{e}', flush=True)
    return True


def detect_agent(text, username=''):
    """開頭的【Muse】／[Spark] 標籤優先，其次看發文者名稱；內文順便提到別人不算"""
    head = normalize(text)[:80]
    m = re.search(r'[【\[]\s*(muse|spark)\b[^】\]]*[】\]]', head, re.I)
    if m:
        return m.group(1).lower()
    u = username.lower()
    return 'muse' if 'muse' in u else 'spark' if 'spark' in u else None


FEED_FILE = os.path.join(BASE_DIR, 'data', 'discord_feed.json')   # 觀戰網頁的「Discord 動態」鏡像
FEED_NAMES = {'claude': 'Claude', 'agy': 'AGY', 'codex': 'Codex', 'spark': 'Spark', 'muse': 'Muse'}


def feed_item(m):
    """一則 Discord 訊息 → 觀戰網頁用的精簡格式；只收機器人／webhook 和帶【AI 名字】標籤的訊息，不收一般聊天"""
    author = (m.get('author') or {}).get('username') or ''
    embeds = m.get('embeds') or []
    text = (m.get('content') or '') + ''.join('\n' + (e.get('title') or '') for e in embeds)
    tagged = re.search(r'【\s*(Claude|AGY|Codex|Spark|Muse)\s*】', text, re.I)
    if not (m.get('webhook_id') or (m.get('author') or {}).get('bot') or tagged):
        return None
    agent = tagged.group(1).lower() if tagged else next((k for k, v in FEED_NAMES.items() if v.lower() in author.lower()), 'arena')
    if not (m.get('webhook_id') or (m.get('author') or {}).get('bot')):
        author = FEED_NAMES[agent]   # 人工轉貼的訊息顯示 AI 名字，不把貼文者的 Discord 帳號公開在網頁上
    imgs = [a['url'] for a in m.get('attachments') or [] if str(a.get('content_type', '')).startswith('image/')]
    return {'id': m['id'], 'time': m.get('timestamp'), 'author': author, 'agent': agent, 'content': (m.get('content') or '')[:3000],
            'embeds': [{'title': (e.get('title') or '')[:300], 'description': (e.get('description') or '')[:4000],
                        'color': '#%06x' % e['color'] if isinstance(e.get('color'), int) else None,
                        'footer': ((e.get('footer') or {}).get('text') or '')[:200],
                        'image': (e.get('image') or {}).get('url')} for e in embeds[:4]],
            'images': imgs[:4]}


def update_feed(msgs):
    try:
        feed = json.load(open(FEED_FILE, encoding='utf-8'))
    except Exception:
        feed = []
    by_id = {f['id']: f for f in feed}
    for m in msgs if isinstance(msgs, list) else []:
        it = feed_item(m)
        if it:
            by_id[it['id']] = it
    out = sorted(by_id.values(), key=lambda f: int(f['id']))[-300:]
    json.dump(out, open(FEED_FILE, 'w', encoding='utf-8'), ensure_ascii=False)


def poll_discord():
    import urllib.request
    token, channel = (os.environ.get('DISCORD_BOT_TOKEN') or '').strip(), (os.environ.get('DISCORD_CHANNEL_ID') or '').strip()
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
    def fetch(query):
        req = urllib.request.Request(f'https://discord.com/api/v10/channels/{channel}/messages?{query}',
                                     headers={'Authorization': f'Bot {token}', 'User-Agent': 'DiscordBot (paper-trading-dashboard, 1.0)'})
        return json.loads(urllib.request.urlopen(req, timeout=15).read().decode('utf-8'))
    if not os.path.exists(FEED_FILE):   # 第一次：先把頻道最近 100 則放進網頁鏡像（只顯示，不重新解析倉位）
        try:
            update_feed(fetch('limit=100'))
        except Exception as e:
            print(f'[Discord] 鏡像初始化失敗：{e}', flush=True)
    try:
        msgs = fetch('limit=50' + (f'&after={last}' if last else ''))
    except Exception as e:
        print(f'[Discord] 讀取失敗：{e}', flush=True)
        return
    if not isinstance(msgs, list) or not msgs:
        return
    update_feed(msgs)
    got = 0
    for m in sorted(msgs, key=lambda x: int(x['id'])):
        text = m.get('content') or ''
        for e in m.get('embeds') or []:
            text += '\n' + (e.get('title') or '') + '\n' + (e.get('description') or '')
        if mao_advice_message(text, m.get('timestamp')):   # 「【名字】茂持股顧問」：另存顧問建議，絕不當成比賽的開倉／平倉／淨值
            continue
        agent = detect_agent(text, (m.get('author') or {}).get('username') or '')
        if agent and text.strip():
            try:
                sync_message(agent, text.strip(), m.get('timestamp'))
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
        sync_spark_ledger()
        poll_discord()
        apply_corrections()
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

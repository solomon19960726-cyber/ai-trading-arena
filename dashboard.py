# -*- coding: utf-8 -*-
"""
模擬交易對戰（Claude vs AGY vs Codex vs Spark vs Muse）即時觀戰面板生成器
讀取 paper_trading/data/state.json、external_agents.json、orders/done、decisions/*_學習筆記.md
結合 Yahoo Finance 即時報價，產出互動式觀戰面板 HTML。
"""
import json, os, re, glob, time, urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
STATE_FILE = os.path.join(DATA, 'state.json')
EXT_FILE = os.path.join(DATA, 'external_agents.json')
DONE_DIR = os.path.join(BASE, 'orders', 'done')
INBOX_DIR = os.path.join(BASE, 'orders', 'inbox')
DECISIONS_DIR = os.path.join(BASE, 'decisions')
OUT_HTML = os.path.join(BASE, 'arena_dashboard.html')
CURVE_FILE = os.path.join(DATA, 'equity_curve.json')   # live_server 每 30 分鐘記一筆五位淨值

START_CASH = 10000.0
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36'}

AGENT_META = {
    'claude': {'name': 'Claude', 'org': 'Anthropic', 'style': '', 'color': '#10b981', 'engine': '本機撮合引擎'},
    'agy':    {'name': 'AGY',    'org': 'Antigravity', 'style': '', 'color': '#38bdf8', 'engine': '本機撮合引擎'},
    'codex':  {'name': 'Codex',  'org': 'OpenAI',    'style': '', 'color': '#f59e0b', 'engine': '本機撮合引擎'},
    'spark':  {'name': 'Spark',  'org': 'Google Gemini', 'style': '', 'color': '#a855f7', 'engine': '雲端獨立推播'},
    'muse':   {'name': 'Muse',   'org': 'Meta',      'style': '', 'color': '#ec4899', 'engine': '雲端獨立推播'},
}


def et_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('America/New_York'))
    except Exception:
        u = datetime.now(timezone.utc)
        return u.astimezone(timezone(timedelta(hours=-4)))


def fetch_quote(ticker):
    sym = '^VIX' if ticker == 'VIX' else ticker
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}?range=2d&interval=15m&includePrePost=true'
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=12) as r:
        j = json.loads(r.read().decode('utf-8', errors='replace'))
    res = j['chart']['result'][0]
    m = res['meta']
    q = res['indicators']['quote'][0]
    ts = res.get('timestamp') or []
    price = m.get('regularMarketPrice')
    ptime = m.get('regularMarketTime') or 0
    prev_close = m.get('chartPreviousClose') or m.get('previousClose') or price
    bars = []
    for i, t in enumerate(ts):
        c = q['close'][i]
        if c is not None:
            bars.append({'t': t, 'c': round(float(c), 4)})
            if t > ptime:
                price = float(c)
                ptime = t
    chg_pct = ((price - prev_close) / prev_close * 100.0) if prev_close else 0.0
    return {
        'ticker': ticker,
        'price': round(float(price), 4),
        'prev_close': round(float(prev_close), 4),
        'chg_pct': round(chg_pct, 2),
        'time': ptime,
        'bars': bars[-24:]
    }


def pos_value(p, price):
    if p['side'] == 'long':
        return p['qty'] * price
    return p['qty'] * p['entry'] + (p['entry'] - price) * p['qty']


def unreal(p, price):
    return (price - p['entry']) * p['qty'] * (1 if p['side'] == 'long' else -1)


def read_learning_notes():
    notes = {}
    mapping = {
        'claude': 'claude_學習筆記.md',
        'agy': 'agy_學習筆記.md',
        'codex': 'codex_學習筆記.md',
        'spark': 'spark_學習筆記.md',
        'muse': 'muse_學習筆記.md',
    }
    for ag, fname in mapping.items():
        fp = os.path.join(DECISIONS_DIR, fname)
        if os.path.exists(fp):
            try:
                txt = open(fp, encoding='utf-8').read().strip()
                # 取最新兩個段落
                sections = re.split(r'\n(?=##\s)', txt)
                head_sections = '\n\n'.join(sections[:2])[:1800]
                notes[ag] = head_sections
            except Exception:
                notes[ag] = ''
        else:
            notes[ag] = ''
    return notes


def read_discord_events(state, ext):
    events = []
    # 1. 從 orders/done 讀取所有 note 與交易決策
    if os.path.exists(DONE_DIR):
        for fp in sorted(glob.glob(os.path.join(DONE_DIR, '*.json')), reverse=True):
            bname = os.path.basename(fp)
            m = re.match(r'^(\d{8}_\d{4})_', bname)
            ts_str = m.group(1) if m else bname[:13]
            if len(ts_str) == 13 and '_' in ts_str:
                ts_fmt = f"{ts_str[4:6]}/{ts_str[6:8]} {ts_str[9:11]}:{ts_str[11:13]} ET"
            else:
                ts_fmt = ts_str
            try:
                data = json.load(open(fp, encoding='utf-8'))
                items = data if isinstance(data, list) else [data]
                for it in items:
                    ag = str(it.get('agent', '')).lower()
                    act = it.get('action', '')
                    if act == 'note':
                        events.append({
                            'agent': ag,
                            'time': ts_fmt,
                            'type': 'NOTE',
                            'badge': '📝 決策筆記',
                            'title': f"{AGENT_META.get(ag, {}).get('name', ag)} 盤勢與決策紀錄",
                            'text': it.get('text') or it.get('reason') or ''
                        })
                    elif act == 'open':
                        side_tw = '做多' if it.get('side') == 'long' else '放空'
                        events.append({
                            'agent': ag,
                            'time': ts_fmt,
                            'type': 'OPEN',
                            'badge': f"✅ 開倉 {it.get('ticker')} {side_tw}",
                            'title': f"{it.get('ticker')} {side_tw} {it.get('qty')}股｜停損 {it.get('stop')}｜停利 {it.get('target')}",
                            'text': it.get('reason', '')
                        })
                    elif act == 'close':
                        events.append({
                            'agent': ag,
                            'time': ts_fmt,
                            'type': 'CLOSE',
                            'badge': f"⚪ 主動平倉",
                            'title': f"平倉倉位 #{it.get('position_id')}",
                            'text': it.get('reason', '')
                        })
                    elif act == 'modify':
                        events.append({
                            'agent': ag,
                            'time': ts_fmt,
                            'type': 'MODIFY',
                            'badge': f"🔧 調整停損/停利",
                            'title': f"倉位 #{it.get('position_id')} → 新停損 {it.get('stop')}｜新停利 {it.get('target')}",
                            'text': it.get('reason', '')
                        })
            except Exception:
                pass

    # 2. 加入已平倉交易（如自動停利/停損觸發）
    for tr in reversed(state.get('trades', [])):
        ag = tr.get('agent', '')
        ca = tr.get('closed_at', '')
        ts_fmt = ca[5:16].replace('T', ' ') + ' ET' if len(ca) >= 16 else ca
        pnl = tr.get('pnl', 0)
        rm = tr.get('r_multiple')
        r_str = f"{rm:+.2f}R" if rm is not None else ''
        events.append({
            'agent': ag,
            'time': ts_fmt,
            'type': 'TRADE_CLOSED',
            'badge': f"{'🟢 停利成交' if pnl > 0 else '🔴 平倉結算'} {tr.get('ticker')}",
            'title': f"{tr.get('ticker')} ({'多' if tr.get('side')=='long' else '空'} {tr.get('qty')}股) 進場 ${tr.get('entry'):.2f} → 出場 ${tr.get('exit'):.2f}｜損益 ${pnl:+.2f} ({r_str})",
            'text': tr.get('exit_reason', '')
        })

    # 3. 加入 Spark / Muse 的紀錄
    for ext_ag in ('spark', 'muse'):
        info = ext.get(ext_ag, {})
        for m in reversed(info.get('messages', [])[-30:]):
            events.append({
                'agent': ext_ag, 'time': m.get('time', ''), 'type': 'EXT_NOTE',
                'badge': '📡 Discord 推播', 'title': f"{AGENT_META[ext_ag]['name']} 推播",
                'text': m.get('text', '')
            })
        if info.get('note') and not info.get('messages'):
            events.append({
                'agent': ext_ag,
                'time': info.get('note_time', '近期推播'),
                'type': 'EXT_NOTE',
                'badge': '📡 雲端推播',
                'title': f"{AGENT_META[ext_ag]['name']} 最新策略動態",
                'text': info.get('note', '')
            })

    events.sort(key=lambda e: str(e.get('time', '')), reverse=True)   # 「MM/DD HH:MM ET」同一年內照字串排就是照時間
    return events


def own_summary(note):
    """從該 AI 自己的學習筆記抓一句（不由別人替它下定義）"""
    for line in (note or '').splitlines():
        t = line.strip().lstrip('-*#> ').strip()
        if len(t) >= 8 and not re.match(r'^\d{4}-\d{2}-\d{2}', t) and '學習筆記' not in t:
            return t[:60] + ('…' if len(t) > 60 else '')
    return ''


def strategy_stats(a, state, ext):
    """策略對照：平均風險、持倉時間、多空比例、常做標的、決策次數"""
    tr = a.get('trades', [])
    allp = list(a.get('positions', [])) + list(tr)
    risks, holds = [], []
    for p in allp:
        try:
            risks.append(abs(float(p['entry']) - float(p.get('stop0', p['stop']))) * float(p['qty']) / START_CASH * 100)
        except Exception:
            pass
    for t in tr:
        try:
            o = datetime.fromisoformat(t['opened_at']); c = datetime.fromisoformat(t['closed_at'])
            holds.append((c - o).total_seconds() / 3600)
        except Exception:
            pass
    longs = sum(1 for p in allp if p.get('side') == 'long'); shorts = len(allp) - longs
    cnt = {}
    for p in allp:
        cnt[p.get('ticker')] = cnt.get(p.get('ticker'), 0) + 1
    top = [k for k, _ in sorted(cnt.items(), key=lambda x: -x[1])[:3]]
    decisions = 0
    if a['id'] in ('claude', 'agy', 'codex') and os.path.exists(DONE_DIR):
        for fp in glob.glob(os.path.join(DONE_DIR, '*.json')):
            try:
                d = json.load(open(fp, encoding='utf-8'))
                d = d if isinstance(d, list) else [d]
                decisions += sum(1 for it in d if it.get('agent') == a['id'] and it.get('action') == 'note')
            except Exception:
                pass
    else:
        decisions = len(ext.get(a['id'], {}).get('messages', []))
    return {
        'trades_total': len(allp),
        'avg_risk_pct': round(sum(risks) / len(risks), 2) if risks else None,
        'avg_hold_h': round(sum(holds) / len(holds), 1) if holds else None,
        'long': longs, 'short': shorts, 'top': top, 'decisions': decisions,
    }


def load_feed():
    """Discord 頻道鏡像（live_server.poll_discord 存的），最新在前；每則附上程式擷取的重點"""
    try:
        feed = list(reversed(json.load(open(os.path.join(DATA, 'discord_feed.json'), encoding='utf-8'))))[:150]
    except Exception:
        return []
    for m in feed:
        m.update(analyze_message(m))
    return feed


# ---------- Discord 重點擷取（只從原文抓句子與關鍵字，不改寫任何 AI 的意思）----------
HOT_TICKERS = {'SPY', 'QQQ', 'IWM', 'DIA', 'SMH', 'SOXX', 'NVDA', 'MSFT', 'AAPL', 'AMZN', 'GOOGL', 'GOOG', 'META', 'TSLA',
               'AMD', 'AVGO', 'XLE', 'XLV', 'XLP', 'XLU', 'XLF', 'XLK', 'GLD', 'TLT', 'CRWV', 'NFLX', 'ORCL', 'MU', 'TSM',
               'INTC', 'PLTR', 'COIN', 'MSTR', 'IBIT', 'ARM', 'SMCI', 'UNH', 'JPM', 'LLY'}
BULL_WORDS = ['偏多', '反彈', '轉強', '翻紅', '止跌', 'risk-on', '樂觀', '突破', '抗跌', '收斂', '回升', '走強', '多頭']
BEAR_WORDS = ['偏空', '賣壓', '偏弱', '走弱', '急跌', 'risk-off', '恐慌', '跌破', '落後', '續弱', '疑慮', '空頭', '轉弱']
NEGATED = re.compile(r'[未不沒非無][^，。；,\s]{0,3}(翻多|追多|翻空|追空|轉強|轉弱|突破|止跌)')
REPORT_WORDS = ['每日戰報', '每週戰報', '淨值排行', '收盤戰報', '每日收盤']


def _plain(m):
    t = (m.get('content') or '') + ''.join('\n' + (e.get('title') or '') + '\n' + (e.get('description') or '') for e in m.get('embeds') or [])
    # 只把全形英數字與 ＄＠ 轉半形（代號、價格才抓得到），中文標點保留原樣
    t = ''.join(chr(ord(c) - 0xFEE0) if '０' <= c <= '９' or 'Ａ' <= c <= 'Ｚ' or 'ａ' <= c <= 'ｚ' or c in '＄＠' else c for c in t)
    return t.replace('**', '').replace('__', '')


def _clip(t, n=90):
    t = re.sub(r'\s+', ' ', t).strip(' ：:；;，,。|｜-•】【「」')
    return t if len(t) <= n else t[:n - 1] + '…'


def analyze_message(m):
    t = _plain(m)
    head = sent = None
    for pat in (r'大盤看法\s*[:：]\s*([^；;。\n]+)', r'大盤\s*[:：]\s*([^；;。\n]+)', r'(大盤(?:偏空|偏多)[^；;。\n]*)',
                r'決策紀錄\s*[:：]\s*([^；;。\n]+)', r'盤面解讀\s*[:：]\s*([^；;。\n]+)', r'(收盤偏[多空][^；;。\n]*)',
                r'([^\n]*?收盤風險偏[多空][^；;。\n]*)'):
        mm = re.search(pat, t)
        if mm:
            sent = mm.group(1)
            head = _clip(sent)
            break
    if not head:   # 沒有大盤句：取第一行有內容的文字（去掉標籤、表情）
        for line in t.splitlines():
            line = re.sub(r'^[\W_]*【[^】]*】', '', line).strip()
            line = re.sub(r'^[^\w$]+', '', line)
            if len(line) >= 6 and not line.startswith('決策'):
                sent = line
                head = _clip(line)
                break
    # 多空：句子裡明講「偏多／偏空／看多／看空」優先；沒明講才看用詞，要明顯偏一邊（±2）才算，其餘當中性／觀望
    full = sent or ''
    if sent:   # 大盤句延伸到句號，避免只看到前半句
        i = t.find(sent)
        full = t[i:i + len(sent) + 200].split('。')[0]
    exp_bull = any(w in full for w in ('偏多', '看多', '多方佔優')) and not NEGATED.search(full)
    exp_bear = any(w in full for w in ('偏空', '看空', '空方佔優'))
    if exp_bull != exp_bear:
        senti = 'bull' if exp_bull else 'bear'
    else:
        neg = NEGATED.findall(full)
        score = sum(full.count(w) for w in BULL_WORDS) - sum(full.count(w) for w in BEAR_WORDS)
        score -= sum(1 for w in neg if w in ('翻多', '追多', '轉強', '突破', '止跌'))
        score += sum(1 for w in neg if w in ('翻空', '追空', '轉弱'))
        senti = 'bull' if score >= 2 else 'bear' if score <= -2 else 'neutral'
    actions = []
    for mm in re.finditer(r'(?:✅\s*)?成交\s*([A-Z]{1,5})\s*[|｜]?\s*(做多|放空|多|空)?', t):
        actions.append({'type': 'open', 'ticker': mm.group(1), 'side': 'short' if mm.group(2) and '空' in mm.group(2) else 'long'})
    for mm in re.finditer(r'([🔴🟢⚪])?\s*(平倉|停損|停利)\s*([A-Z]{1,5})\s+[^\n]{0,30}?@', t):
        actions.append({'type': 'close', 'ticker': mm.group(3), 'why': mm.group(2)})
    fresh = [l for l in t.splitlines() if '觸價平倉' in l and '已於' not in l]   # 「已於今晨…平倉」是回顧，不算新的平倉
    if fresh and not any(a['type'] == 'close' for a in actions):
        mm = re.search(r'(?:放空|做多|多單|空單)\s*([A-Z]{1,5})', t)
        tk = mm.group(1) if mm else None
        if tk:
            actions.append({'type': 'close', 'ticker': tk, 'why': '停損' if '停損' in t else '平倉'})
    for mm in re.finditer(r'🔧\s*調整\s*([A-Z]{1,5})', t):
        actions.append({'type': 'modify', 'ticker': mm.group(1)})
    hold = re.search(r'(?:本次不動作|PASS\s*不動作)\s*[^：:\w]{0,3}[：:]?\s*([^。\n]*)', t)
    if hold and not any(a['type'] in ('open', 'close') for a in actions):
        actions.append({'type': 'hold', 'why': _clip(hold.group(1), 80)})
    if '測試成功' in t[:120]:
        kind = 'info'
    elif any(w in t for w in REPORT_WORDS):
        kind = 'report'
    elif any(a['type'] in ('open', 'close', 'modify') for a in actions):
        kind = 'trade'
    elif actions or '決策' in t:
        kind = 'decision'
    else:
        kind = 'info'
    tickers = sorted({x for x in re.findall(r'(?<![A-Za-z])([A-Z]{2,5})(?![A-Za-z])', t) if x in HOT_TICKERS})
    return {'kind': kind, 'headline': head or '', 'sentiment': senti,
            'actions': actions, 'tickers': tickers}


def discord_digest(feed):
    """重點整理：最近一個有訊息的美東日期裡，大家說了什麼（多空、動作、交易、熱門標的）"""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('America/New_York')
    except Exception:
        tz = timezone(timedelta(hours=-4))
    ids = list(AGENT_META)
    for m in feed:
        try:
            m['_d'] = datetime.fromisoformat(m['time']).astimezone(tz)
        except Exception:
            m['_d'] = None
    talk = [m for m in feed if m['_d'] and m.get('agent') in ids]
    if not talk:
        return None
    day = max(m['_d'] for m in talk).date()
    today = [m for m in talk if m['_d'].date() == day]
    latest = {}
    for m in talk:   # feed 是新→舊：每位第一則決策／交易訊息就是最新立場
        if m['agent'] not in latest and m['kind'] in ('decision', 'trade', 'report') and m['headline']:
            act = next((a for a in m['actions'] if a['type'] != 'hold'), None) or next(iter(m['actions']), None)
            latest[m['agent']] = {'agent': m['agent'], 'time': m['_d'].strftime('%m/%d %H:%M ET'), 'sentiment': m['sentiment'],
                                  'headline': m['headline'], 'action': act, 'today': m['_d'].date() == day}
    stance = [latest[a] for a in ids if a in latest]
    bulls = [x['agent'] for x in stance if x['sentiment'] == 'bull']
    bears = [x['agent'] for x in stance if x['sentiment'] == 'bear']
    neutral = [x['agent'] for x in stance if x['sentiment'] == 'neutral']
    trades = [dict(a, agent=m['agent'], time=m['_d'].strftime('%H:%M')) for m in reversed(today) for a in m['actions'] if a['type'] in ('open', 'close', 'modify')]
    holds = sorted({m['agent'] for m in today if any(a['type'] == 'hold' for a in m['actions'])} - {t['agent'] for t in trades if t['type'] == 'open'})
    hot = {}
    for m in today:
        for tk in m['tickers']:
            hot.setdefault(tk, set()).add(m['agent'])
    hot = sorted(([tk, sorted(v, key=ids.index)] for tk, v in hot.items()), key=lambda x: (-len(x[1]), x[0]))[:8]
    speakers = sorted({m['agent'] for m in today}, key=ids.index)
    nm = lambda xs: '、'.join(AGENT_META[x]['name'] for x in xs)
    L = [f"{day.strftime('%m/%d')} 共 {len(today)} 則訊息，{len(speakers)} 位發言（{nm(speakers)}）。"]
    if stance:
        L.append(f"大盤看法：偏多 {len(bulls)} 位" + (f"（{nm(bulls)}）" if bulls else '') + f"、中性 {len(neutral)} 位" +
                 (f"（{nm(neutral)}）" if neutral else '') + f"、偏空 {len(bears)} 位" + (f"（{nm(bears)}）" if bears else '') + '。')
    opens = [t for t in trades if t['type'] == 'open']
    closes = [t for t in trades if t['type'] == 'close']
    act = []
    if opens:
        act.append('開倉 ' + '、'.join(f"{AGENT_META[t['agent']]['name']} {t['ticker']}{'空' if t.get('side') == 'short' else '多'}" for t in opens))
    if closes:
        act.append('平倉 ' + '、'.join(f"{AGENT_META[t['agent']]['name']} {t['ticker']}" for t in closes))
    if holds:
        act.append(f"{nm(holds)} 本次不動作")
    if act:
        L.append('動作：' + '；'.join(act) + '。')
    if hot:
        L.append('最多人提到：' + '、'.join(f"{tk}（{len(who)} 位）" for tk, who in hot[:3]) + '。')
    if bulls and bears:
        L.append(f"看法分歧：{nm(bulls)} 偏多，{nm(bears)} 偏空。")
    for m in feed:
        m.pop('_d', None)
    return {'day': day.strftime('%m/%d'), 'count': len(today), 'speakers': speakers, 'summary': L, 'stance': stance,
            'bulls': bulls, 'bears': bears, 'neutral': neutral, 'trades': trades, 'hot': hot}


def load_curve_all():
    try:
        return json.load(open(CURVE_FILE, encoding='utf-8'))
    except Exception:
        return []


def load_curve():
    try:
        return json.load(open(CURVE_FILE, encoding='utf-8'))[-400:]
    except Exception:
        return []


def finance_metrics(positions, trades, eq, cash):
    """財務分析：曝險、停損在險金額、集中度、獲利因子、期望值、賺賠比"""
    long_v = sum(p['qty'] * p['price'] for p in positions if p['side'] == 'long')
    short_v = sum(p['qty'] * p['price'] for p in positions if p['side'] == 'short')
    at_risk = 0.0
    for p in positions:
        d = (p['price'] - float(p['stop'])) if p['side'] == 'long' else (float(p['stop']) - p['price'])
        at_risk += max(0.0, d) * p['qty']
    weights = sorted(((p['qty'] * p['price'], p['ticker']) for p in positions), reverse=True)
    pnl = [float(t.get('pnl') or 0) for t in trades]
    win = [v for v in pnl if v > 0]
    loss = [v for v in pnl if v <= 0]
    gw, gl = sum(win), -sum(loss)
    hold = []
    for t in trades:
        try:
            hold.append((datetime.fromisoformat(t['closed_at']) - datetime.fromisoformat(t['opened_at'])).total_seconds() / 3600)
        except Exception:
            pass
    pct = lambda v: round(v / eq * 100, 1) if eq > 0 else 0.0
    return {
        'long_value': round(long_v, 2), 'short_value': round(short_v, 2),
        'long_pct': pct(long_v), 'short_pct': pct(short_v), 'cash_pct': pct(cash) if cash > 0 else 0.0,
        'gross_pct': pct(long_v + short_v), 'net_pct': pct(long_v - short_v),
        'at_risk': round(at_risk, 2), 'at_risk_pct': pct(at_risk),
        'top_ticker': weights[0][1] if weights else None, 'top_pct': pct(weights[0][0]) if weights else 0.0,
        'profit_factor': round(gw / gl, 2) if gl > 0 else None,
        'expectancy': round(sum(pnl) / len(pnl), 2) if pnl else None,
        'avg_win': round(gw / len(win), 2) if win else None,
        'avg_loss': round(-gl / len(loss), 2) if loss else None,
        'payoff': round((gw / len(win)) / (gl / len(loss)), 2) if win and loss and gl > 0 else None,
        'best': round(max(pnl), 2) if pnl else None, 'worst': round(min(pnl), 2) if pnl else None,
        'avg_hold_h': round(sum(hold) / len(hold), 1) if hold else None,
    }


def drawdown_stats(curve, current):
    """用 equity_curve.json（＋目前淨值）算每位的回撤序列與最大回撤（峰值從起始資金 US$10,000 起算）"""
    ids = list(AGENT_META)
    peak = {a: START_CASH for a in ids}
    mdd = {a: 0.0 for a in ids}
    series = []
    pts = list(curve) + [{'t': '現在', 'equity': current}]
    for p in pts:
        row = {}
        for a in ids:
            v = (p.get('equity') or {}).get(a)
            if v is None:
                continue
            v = float(v)
            peak[a] = max(peak[a], v)
            dd = (peak[a] - v) / peak[a] * 100.0 if peak[a] else 0.0
            mdd[a] = max(mdd[a], dd)
            row[a] = round(-dd, 2)
        if row:
            series.append({'t': p.get('t', ''), 'dd': row})
    cur = {a: (series[-1]['dd'].get(a) if series else None) for a in ids}
    return {a: round(mdd[a], 2) for a in ids}, {a: (-cur[a] if cur[a] is not None else 0.0) for a in ids}, series


def build_state(fast=True):
    state = json.load(open(STATE_FILE, encoding='utf-8')) if os.path.exists(STATE_FILE) else {
        'accounts': {a: {'cash': START_CASH} for a in ('claude', 'agy', 'codex')},
        'positions': [], 'trades': [], 'rejected': [], 'equity_history': []
    }
    ext = json.load(open(EXT_FILE, encoding='utf-8')) if os.path.exists(EXT_FILE) else {}

    # 收集所有需要的 ticker
    tickers = {'SPY', 'QQQ', 'SOXX', 'SMH', 'MSFT', 'NVDA', 'XLE', 'XLV', 'VIX', '^GSPC', '^IXIC'}
    for p in state.get('positions', []):
        tickers.add(p['ticker'])
    for t in state.get('trades', []):
        tickers.add(t['ticker'])
    for ag_data in ext.values():
        for p in ag_data.get('positions', []):
            tickers.add(p['ticker'])

    quotes = {}
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_quote, tk): tk for tk in sorted(tickers)}
        for f in concurrent.futures.as_completed(futures):
            tk = futures[f]
            try:
                q = f.result()
                if q:
                    quotes[tk] = q
            except Exception:
                pass

    # 整理 5 位 AI 的完整數據
    agents_summary = []
    all_positions = []
    all_trades = list(state.get('trades', []))

    for ag_id in ('claude', 'agy', 'codex', 'spark', 'muse'):
        meta = AGENT_META[ag_id]
        if ag_id in ('claude', 'agy', 'codex'):
            cash = float(state['accounts'].get(ag_id, {}).get('cash', START_CASH))
            pos_list = [dict(p) for p in state.get('positions', []) if p.get('agent') == ag_id]
            tr_list = [dict(t) for t in state.get('trades', []) if t.get('agent') == ag_id]
        else:
            ext_info = ext.get(ag_id, {})
            cash = float(ext_info.get('cash', START_CASH))
            pos_list = [dict(p) for p in ext_info.get('positions', [])]
            tr_list = [dict(t) for t in ext_info.get('trades', [])]
            all_trades.extend(tr_list)

        unreal_total = 0.0
        pos_mkt_value = 0.0
        enriched_pos = []

        for p in pos_list:
            tk = p['ticker']
            px = quotes.get(tk, {}).get('price', p['entry'])
            u_pnl = unreal(p, px)
            p_val = pos_value(p, px)
            unreal_total += u_pnl
            pos_mkt_value += p_val

            risk_per_sh = abs(p['entry'] - p.get('stop0', p['stop']))
            init_risk = risk_per_sh * p['qty']
            cur_r = (u_pnl / init_risk) if init_risk > 0 else 0.0

            # 計算距離停損與停利的百分比與進度條 (0% = 停損, 100% = 停利)
            stop_px = float(p['stop'])
            target_px = float(p['target'])
            if p['side'] == 'long':
                dist_stop_pct = (px - stop_px) / px * 100.0
                dist_target_pct = (target_px - px) / px * 100.0
                span = target_px - stop_px
                progress = ((px - stop_px) / span * 100.0) if span > 0 else 50.0
            else:
                dist_stop_pct = (stop_px - px) / px * 100.0
                dist_target_pct = (px - target_px) / px * 100.0
                span = stop_px - target_px
                progress = ((stop_px - px) / span * 100.0) if span > 0 else 50.0

            p_enriched = dict(p)
            p_enriched.update({
                'agent_name': meta['name'],
                'agent_color': meta['color'],
                'price': round(px, 2),
                'unrealized': round(u_pnl, 2),
                'cur_r': round(cur_r, 2),
                'init_risk': round(init_risk, 2),
                'dist_stop_pct': round(dist_stop_pct, 2),
                'dist_target_pct': round(dist_target_pct, 2),
                'progress': round(max(0.0, min(100.0, progress)), 1),
                'is_breakeven': (p['side'] == 'long' and stop_px >= p['entry']) or (p['side'] == 'short' and stop_px <= p['entry'])
            })
            enriched_pos.append(p_enriched)
            all_positions.append(p_enriched)

        eq = cash + pos_mkt_value
        eq_note = ''
        info = ext.get(ag_id, {})
        rep_eq = info.get('equity_reported')
        if ag_id in ('spark', 'muse') and rep_eq is not None and float(rep_eq) >= 1000:
            # 以最新一則回報的淨值為準；只有「持倉都抓得到即時報價」時才用即時股價重算（帳本 CSV 的淨值一律優先）
            live_ok = pos_list and all(p['ticker'] in quotes for p in pos_list)
            if info.get('equity_source') == 'csv' or not live_ok:
                eq = float(rep_eq)
                try:
                    eq_note = ('帳本 CSV ' if info.get('equity_source') == 'csv' else '自行回報 ') + \
                        datetime.fromisoformat(info['equity_time']).astimezone(et_now().tzinfo).strftime('%m/%d %H:%M ET')
                except Exception:
                    eq_note = '自行回報'
        ret_pct = (eq / START_CASH - 1.0) * 100.0
        realized_pnl = sum(t.get('pnl', 0.0) for t in tr_list)
        metrics = finance_metrics(enriched_pos, tr_list, eq, cash)
        wins = [t for t in tr_list if t.get('pnl', 0.0) > 0]
        win_rate = (len(wins) / len(tr_list) * 100.0) if tr_list else None
        r_vals = [t['r_multiple'] for t in tr_list if t.get('r_multiple') is not None]
        avg_r = (sum(r_vals) / len(r_vals)) if r_vals else None

        agents_summary.append({
            'id': ag_id,
            'name': meta['name'],
            'org': meta['org'],
            'style': meta['style'],
            'color': meta['color'],
            'engine': meta['engine'],
            'cash': round(cash, 2),
            'cash_pct': round(cash / eq * 100.0, 1) if eq > 0 else 100.0,
            'equity': round(eq, 2),
            'ret_pct': round(ret_pct, 2),
            'unrealized': round(unreal_total, 2),
            'realized': round(realized_pnl, 2),
            'closed_count': len(tr_list),
            'open_count': len(enriched_pos),
            'win_rate': round(win_rate, 1) if win_rate is not None else None,
            'wins': len(wins),
            'losses': len(tr_list) - len(wins),
            'avg_r': round(avg_r, 2) if avg_r is not None else None,
            'positions': enriched_pos,
            'trades': tr_list,
            'estimated': bool(ext.get(ag_id, {}).get('estimated')) if ag_id in ('spark', 'muse') else False,
            'equity_note': eq_note,
            'metrics': metrics
        })

    # 依淨值高低排序
    agents_summary.sort(key=lambda x: x['equity'], reverse=True)

    feed = load_feed()
    curve = load_curve()
    mdd, cur_dd, dd_series = drawdown_stats(curve, {a['id']: a['equity'] for a in agents_summary})
    for a in agents_summary:
        a['max_dd_pct'] = mdd.get(a['id'], 0.0)
        a['cur_dd_pct'] = round(cur_dd.get(a['id'], 0.0), 2)

    notes = read_learning_notes()
    events = read_discord_events(state, ext)
    for a in agents_summary:
        a['style'] = own_summary(notes.get(a['id'], ''))
        a['strategy'] = strategy_stats(a, state, ext)

    now_et = et_now()
    now_tp = now_et.astimezone(timezone(timedelta(hours=8)))

    payload = {
        'updated_et': now_et.strftime('%Y-%m-%d %H:%M:%S ET'),
        'updated_tp': now_tp.strftime('%Y-%m-%d %H:%M:%S 台北'),
        'quotes': quotes,
        'agents': agents_summary,
        'positions': all_positions,
        'trades': all_trades,
        'notes': notes,
        'events': events,
        'equity_curve': curve,
        'discord': feed,
        'digest': discord_digest(feed),
        'drawdown_curve': dd_series[-400:]
    }

    return payload


def build():
    payload = build_state()
    html = render_html(payload)
    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    return OUT_HTML


TEMPLATE_FILE = os.path.join(BASE, 'dashboard_template.html')


def render_html(payload):
    """觀戰網頁：dashboard_template.html 的 __DATA__ 換成這次的資料（JSON）"""
    data_json = json.dumps(payload, ensure_ascii=False).replace('</', '<\\/')
    with open(TEMPLATE_FILE, encoding='utf-8') as f:
        return f.read().replace('__DATA__', data_json, 1)


if __name__ == '__main__':
    out = build()
    print('已更新觀戰面板：', out)

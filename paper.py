# -*- coding: utf-8 -*-
"""
模擬交易對戰：Claude vs AGY（美股，各 US$10,000）
只用 Python 標準函式庫。

指令：
  python paper.py run                 撮合下單匣、檢查停損停利（盤前／盤中／盤後）、20:05 ET 後推 Discord 戰報（排程每 15 分鐘跑）
  python paper.py status [agent]      印出帳戶狀態（JSON），給 Claude／AGY 決策前讀
  python paper.py report              印出戰報文字（不推送）
  python paper.py report --post       印出並推送 Discord
  python paper.py order agent open TICKER long|short QTY STOP TARGET 理由...
  python paper.py order agent close POSITION_ID 理由...
  python paper.py order agent modify POSITION_ID STOP TARGET 理由...
  python paper.py selftest            測試報價與寫檔（不下單）
"""
import json, os, sys, time, uuid, glob, shutil, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

for _st in (sys.stdout, sys.stderr):
    try:
        _st.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
STATE = os.path.join(DATA, 'state.json')
LOG = os.path.join(DATA, 'engine.log')
INBOX = os.path.join(BASE, 'orders', 'inbox')
DONE = os.path.join(BASE, 'orders', 'done')
WEBHOOK_FILES = [os.path.join(BASE, 'paper_webhook.txt'),
                 r'C:\Users\EVA\.gemini\antigravity\scratch\claude_migration\上車提醒_webhook.txt']
AGENTS = ['claude', 'agy', 'codex']  # Muse、Spark 自己記帳、自己推 Discord，不經過本引擎
NAMES = {'claude': 'Claude', 'agy': 'AGY', 'codex': 'Codex', 'muse': 'Muse', 'spark': 'Spark'}
START_CASH = 10000.0
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36'}

for d in (DATA, INBOX, DONE):
    os.makedirs(d, exist_ok=True)


# ---------- 時間 ----------
def et_now():
    """美東時間（自動處理夏令／冬令）"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('America/New_York'))
    except Exception:
        u = datetime.now(timezone.utc)
        y = u.year
        # 3 月第二個週日 ~ 11 月第一個週日為夏令
        mar = datetime(y, 3, 8, 7, tzinfo=timezone.utc); mar += timedelta(days=(6 - mar.weekday()) % 7)
        nov = datetime(y, 11, 1, 6, tzinfo=timezone.utc); nov += timedelta(days=(6 - nov.weekday()) % 7)
        off = -4 if mar <= u < nov else -5
        return u.astimezone(timezone(timedelta(hours=off)))


def log(msg):
    line = f"[{et_now().strftime('%Y-%m-%d %H:%M ET')}] {msg}"
    print(line)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


# ---------- 狀態 ----------
def load():
    if os.path.exists(STATE):
        with open(STATE, encoding='utf-8') as f:
            s = json.load(f)
        for a in AGENTS:  # 新加入的參賽者從 US$10,000 開始
            s['accounts'].setdefault(a, {'cash': START_CASH})
        return s
    s = {'created_et': et_now().isoformat(), 'accounts': {a: {'cash': START_CASH} for a in AGENTS},
         'positions': [], 'trades': [], 'rejected': [], 'equity_history': [], 'last_report_date': None}
    save(s)
    return s


def save(s):
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)


# ---------- 報價（Yahoo chart API）----------
_cache = {}
EVENTS = {}   # 本次撮合要推到 Discord 的事件：{agent: [行]}


def event(agent, line):
    EVENTS.setdefault(agent, []).append(line)


def chart(ticker, rng='1d', interval='5m'):
    key = (ticker, rng, interval)
    if key in _cache:
        return _cache[key]
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(ticker)}?range={rng}&interval={interval}&includePrePost=true'
    last = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                j = json.loads(r.read().decode())
            res = j['chart']['result'][0]
            q = res['indicators']['quote'][0]
            bars = []
            for i, t in enumerate(res.get('timestamp') or []):
                o, h, l, c = q['open'][i], q['high'][i], q['low'][i], q['close'][i]
                if None in (o, h, l, c):
                    continue
                bars.append({'t': t, 'o': o, 'h': h, 'l': l, 'c': c})
            m = res['meta']
            price, ptime = m.get('regularMarketPrice'), m.get('regularMarketTime') or 0
            if bars and bars[-1]['t'] > ptime:          # 盤前／盤後有更新的成交
                price, ptime = bars[-1]['c'], bars[-1]['t']
            out = {'price': price, 'time': ptime, 'bars': bars}
            _cache[key] = out
            return out
        except Exception as e:
            last = e
            time.sleep(2)
    raise RuntimeError(f'{ticker} 報價失敗：{last}')


def session():
    """回傳 'pre'（4:00–9:30）、'regular'（9:30–16:00）、'post'（16:00–20:00）、'overnight' 或 'closed'（美東）"""
    n = et_now()
    mins = n.hour * 60 + n.minute
    if n.weekday() >= 5:
        return 'closed'
    if mins < 4 * 60 or mins >= 20 * 60:
        return 'overnight' if not (n.weekday() == 4 and mins >= 20 * 60) else 'closed'
    # 休市日：今天完全沒有 SPY 的成交（含盤前）
    try:
        bars = chart('SPY')['bars']
        today = [b for b in bars if datetime.fromtimestamp(b['t'], n.tzinfo).date() == n.date()]
        if not today and mins >= 4 * 60 + 20:
            return 'closed'
    except Exception:
        pass
    if mins < 9 * 60 + 30:
        return 'pre'
    if mins < 16 * 60:
        return 'regular'
    return 'post'


def market_open():
    """盤前、盤中、盤後都可以成交；夜盤（20:00–4:00）沒有可靠報價，單會留到 4:00 盤前再成交"""
    return session() in ('pre', 'regular', 'post')


# ---------- 帳務 ----------
def pos_value(p, price):
    """多單：市值；空單：保證金 + 未實現損益"""
    if p['side'] == 'long':
        return p['qty'] * price
    return p['qty'] * p['entry'] + (p['entry'] - price) * p['qty']


def unreal(p, price):
    return (price - p['entry']) * p['qty'] * (1 if p['side'] == 'long' else -1)


def equity(s, agent, prices):
    e = s['accounts'][agent]['cash']
    for p in s['positions']:
        if p['agent'] == agent:
            e += pos_value(p, prices.get(p['ticker'], p['entry']))
    return e


def close_pos(s, p, price, why):
    pnl = unreal(p, price)
    s['accounts'][p['agent']]['cash'] += pos_value(p, price)
    risk = abs(p['entry'] - p['stop0']) * p['qty']
    s['positions'] = [x for x in s['positions'] if x['id'] != p['id']]
    t = dict(p)
    t.update({'exit': round(price, 4), 'closed_at': et_now().isoformat(), 'exit_reason': why,
              'pnl': round(pnl, 2), 'r_multiple': round(pnl / risk, 2) if risk else None})
    s['trades'].append(t)
    log(f"{p['agent']} 平倉 {p['ticker']} {p['side']} {p['qty']}股 @{price:.2f}（{why}）損益 {pnl:+.2f}")
    icon = '🔴' if why.startswith('停損') else '🟢' if why.startswith('停利') else '⚪'
    event(p['agent'], f"{icon} 平倉 {p['ticker']} {'多' if p['side']=='long' else '空'} {p['qty']}股 @{price:.2f}（進場 {p['entry']:.2f}）｜{why}｜損益 {pnl:+.2f}（{t['r_multiple'] if t['r_multiple'] is not None else '—'}R）")


def reject(s, o, why):
    o = dict(o); o['rejected_at'] = et_now().isoformat(); o['why'] = why
    s['rejected'].append(o)
    log(f"拒絕 {o.get('agent')} 的單：{why}")
    if o.get('agent') in AGENTS:
        event(o['agent'], f"⛔ 退單 {o.get('ticker', o.get('position_id', ''))}｜{why}")


def handle_order(s, o):
    agent = o.get('agent')
    if agent not in AGENTS:
        return reject(s, o, 'agent 必須是 ' + ' / '.join(AGENTS))
    act = o.get('action')
    if act == 'open':
        tk = str(o.get('ticker', '')).upper().strip()
        side = o.get('side', 'long')
        try:
            qty = int(o['qty']); stop = float(o['stop']); target = float(o['target'])
        except Exception:
            return reject(s, o, 'qty／stop／target 缺少或格式錯誤')
        if side not in ('long', 'short') or qty <= 0:
            return reject(s, o, 'side 要 long 或 short，qty 要 > 0')
        try:
            price = chart(tk)['price']
        except Exception as e:
            return reject(s, o, str(e))
        if side == 'long' and not (stop < price < target):
            return reject(s, o, f'多單需 停損 < 現價 {price:.2f} < 停利')
        if side == 'short' and not (target < price < stop):
            return reject(s, o, f'空單需 停利 < 現價 {price:.2f} < 停損')
        cost = qty * price
        if cost > s['accounts'][agent]['cash'] + 1e-6:
            return reject(s, o, f'現金不足：需 {cost:.2f}，剩 {s["accounts"][agent]["cash"]:.2f}')
        s['accounts'][agent]['cash'] -= cost
        p = {'id': uuid.uuid4().hex[:6], 'agent': agent, 'ticker': tk, 'side': side, 'qty': qty,
             'entry': round(price, 4), 'stop': stop, 'stop0': stop, 'target': target,
             'reason': str(o.get('reason', ''))[:300], 'opened_at': et_now().isoformat(),
             'last_check': int(time.time())}
        s['positions'].append(p)
        log(f"{agent} 開倉 {tk} {side} {qty}股 @{price:.2f} 停損 {stop} 停利 {target}｜{p['reason']}")
        event(agent, f"✅ 成交 {tk} {'做多' if side=='long' else '放空'} {qty}股 @{price:.2f}｜停損 {stop}｜停利 {target}｜風險 ${abs(price-stop)*qty:,.0f}\n　理由：{p['reason']}")
    elif act == 'note':
        txt = str(o.get('text') or o.get('reason') or '').strip()[:1500]
        if txt:
            event(agent, '📝 決策\n' + txt)
            log(f"{agent} 決策紀錄：{txt[:80]}")
    elif act in ('close', 'modify'):
        p = next((x for x in s['positions'] if x['id'] == o.get('position_id') and x['agent'] == agent), None)
        if not p:
            return reject(s, o, '找不到這個 position_id（或不是你的倉位）')
        if act == 'close':
            close_pos(s, p, chart(p['ticker'])['price'], '主動平倉：' + str(o.get('reason', ''))[:200])
        else:
            try:
                p['stop'] = float(o['stop']); p['target'] = float(o['target'])
            except Exception:
                return reject(s, o, 'modify 需要 stop 與 target')
            p.setdefault('modifications', []).append({'at': et_now().isoformat(), 'stop': p['stop'], 'target': p['target'],
                                                      'reason': str(o.get('reason', ''))[:200]})
            log(f"{agent} 調整 {p['ticker']} 停損 {p['stop']} 停利 {p['target']}")
            event(agent, f"🔧 調整 {p['ticker']}｜停損 {p['stop']}｜停利 {p['target']}｜{str(o.get('reason', ''))[:200]}")
    else:
        reject(s, o, 'action 只能是 open / close / modify / note')


def check_exits(s):
    for p in list(s['positions']):
        try:
            bars = [b for b in chart(p['ticker'])['bars'] if b['t'] >= p['last_check'] - 300]
        except Exception as e:
            log(f"檢查 {p['ticker']} 失敗：{e}")
            continue
        hit = None
        for b in bars:
            if p['side'] == 'long':
                if b['l'] <= p['stop']:
                    hit = (min(b['o'], p['stop']), '停損'); break   # 同一根同時碰到，保守算停損
                if b['h'] >= p['target']:
                    hit = (max(b['o'], p['target']), '停利'); break
            else:
                if b['h'] >= p['stop']:
                    hit = (max(b['o'], p['stop']), '停損'); break
                if b['l'] <= p['target']:
                    hit = (min(b['o'], p['target']), '停利'); break
        if hit:
            close_pos(s, p, hit[0], hit[1])
        else:
            p['last_check'] = int(time.time())


# ---------- 戰報 ----------
def stats(s, agent, prices):
    tr = [t for t in s['trades'] if t['agent'] == agent]
    wins = [t for t in tr if t['pnl'] > 0]
    eq = equity(s, agent, prices)
    hist = [h['equity'].get(agent, START_CASH) for h in s['equity_history']] + [eq]
    peak, mdd = START_CASH, 0.0
    for v in hist:
        peak = max(peak, v); mdd = max(mdd, (peak - v) / peak)
    rs = [t['r_multiple'] for t in tr if t.get('r_multiple') is not None]
    gw = sum(t['pnl'] for t in wins); gl = -sum(t['pnl'] for t in tr if t['pnl'] <= 0)
    return {'equity': eq, 'ret': (eq / START_CASH - 1) * 100, 'n': len(tr),
            'win': (len(wins) / len(tr) * 100) if tr else None,
            'avg_r': (sum(rs) / len(rs)) if rs else None,
            'pf': (gw / gl) if gl > 0 else None, 'mdd': mdd * 100,
            'realized': sum(t['pnl'] for t in tr), 'cash': s['accounts'][agent]['cash']}


def report_text(s, only=None):
    tickers = sorted({p['ticker'] for p in s['positions']})
    prices = {}
    for t in tickers:
        try:
            prices[t] = chart(t)['price']
        except Exception:
            pass
    today = et_now().date().isoformat()
    name = NAMES
    f = lambda v, fmt: '—' if v is None else fmt.format(v)
    L = [] if only else [f'**⚔️ 模擬交易對戰 {et_now().strftime("%m/%d")}（美東）**', '']
    for a in ([only] if only else AGENTS):
        st = stats(s, a, prices)
        L.append(f"**【{name[a]}】每日戰報 {et_now().strftime('%m/%d')}（美東）**" if only else f"**{name[a]}**")
        L.append(f"淨值 ${st['equity']:,.0f}（{st['ret']:+.2f}%）｜勝率 {f(st['win'], '{:.0f}%')}（{st['n']} 筆）"
                 f"｜平均 R {f(st['avg_r'], '{:+.2f}')}｜獲利因子 {f(st['pf'], '{:.2f}')}｜最大回撤 {st['mdd']:.1f}%")
        for p in [p for p in s['positions'] if p['agent'] == a]:
            px = prices.get(p['ticker'], p['entry'])
            L.append(f"　持倉 {p['ticker']} {'多' if p['side']=='long' else '空'} {p['qty']}股 @{p['entry']:.2f} → {px:.2f}"
                     f"（{unreal(p, px):+.0f}）停損 {p['stop']} 停利 {p['target']}｜{p['reason'][:60]}")
        for t in [t for t in s['trades'] if t['agent'] == a and t['closed_at'][:10] == today]:
            L.append(f"　今日平倉 {t['ticker']} {t['exit_reason'][:20]} {t['pnl']:+.0f}（{f(t['r_multiple'], '{:+.1f}R')}）")
        L.append('')
    rej = [r for r in s['rejected'] if r['rejected_at'][:10] == today and (not only or r.get('agent') == only)]
    if rej:
        L.append('被拒絕的單：' + '；'.join(f"{name.get(r.get('agent'), r.get('agent'))} {r.get('ticker', '')} {r['why']}" for r in rej))
    return '\n'.join(L).strip()


def webhook_url():
    if os.environ.get('DISCORD_WEBHOOK'):   # 雲端（GitHub Actions secret）
        return os.environ['DISCORD_WEBHOOK'].strip()
    for fp in WEBHOOK_FILES:
        if os.path.exists(fp):
            url = open(fp, encoding='utf-8').read().strip().splitlines()[0].strip()
            if url:
                return url
    return None


def post_chart(label='', s=None):
    """五位 AI 淨值曲線＋排行圖推到 Discord（discord_chart.py）"""
    url = webhook_url()
    if not url:
        return
    try:
        import discord_chart
        live = None
        if s is not None:
            prices = {}
            for t in {p['ticker'] for p in s['positions']}:
                try:
                    prices[t] = chart(t)['price']
                except Exception:
                    pass
            live = {a: round(equity(s, a, prices), 2) for a in AGENTS}
        discord_chart.post(url, label, live)
        log('已推送淨值曲線圖')
    except Exception as e:
        log(f'淨值曲線圖推送失敗：{e}')


def ensure_dashboard():
    """觀戰面板（live_server.py）沒在跑就在背景啟動，等於開機後 15 分鐘內自動開好"""
    if os.environ.get('GITHUB_ACTIONS'):
        return
    import socket, subprocess
    try:
        with socket.create_connection(('127.0.0.1', 8888), timeout=1):
            return
    except OSError:
        pass
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    if os.name == 'nt' and os.path.exists(w):
        exe = w
    flags = 0x00000008 | 0x00000200 | 0x08000000 if os.name == 'nt' else 0   # DETACHED | NEW_GROUP | NO_WINDOW
    script = os.path.join(BASE, 'live_server.py')
    try:
        # AGY 排程結束時會連子程式一起關掉（Windows Job），所以要「脫離」或改由 WMI 啟動才能常駐
        subprocess.Popen([exe, script], cwd=BASE, creationflags=flags | 0x01000000,   # CREATE_BREAKAWAY_FROM_JOB
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, close_fds=True)
        log('觀戰面板沒在跑，已自動啟動')
    except Exception:
        try:
            cmd = f'"{exe}" "{script}"'.replace("'", "''")
            ps = (f"Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
                  f"@{{CommandLine='{cmd}'; CurrentDirectory='{BASE}'}}")
            subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, timeout=30,
                           creationflags=0x08000000 if os.name == 'nt' else 0)
            log('觀戰面板沒在跑，已透過 WMI 自動啟動')
        except Exception as e:
            log(f'觀戰面板啟動失敗：{e}')


def ensure_codex_once_per_boot():
    """每次開機後第一次撮合時，若 Codex（ChatGPT 桌面版）沒開就幫忙打開，讓它的排程能跑；同一次開機只開一次，你自己關掉就不再打擾"""
    if os.name != 'nt':
        return
    import ctypes, subprocess
    try:
        boot = int(time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000) // 300   # 開機時間（5 分鐘精度）
        fp = os.path.join(DATA, 'boot_launch.json')
        try:
            if json.load(open(fp, encoding='utf-8')).get('boot') == boot:
                return
        except Exception:
            pass
        json.dump({'boot': boot}, open(fp, 'w', encoding='utf-8'))
        out = subprocess.run(['tasklist', '/NH'], capture_output=True, text=True,
                             creationflags=0x08000000).stdout
        if 'chatgpt.exe' in out.lower() or 'codex.exe' in out.lower():
            return
        subprocess.Popen(['explorer.exe', r'shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App'], creationflags=0x08000000)
        log('開機後自動打開 Codex')
    except Exception as e:
        log(f'自動打開 Codex 失敗：{e}')


COLORS = {'claude': 0x10b981, 'agy': 0x38bdf8, 'codex': 0xf59e0b, 'spark': 0xa855f7, 'muse': 0xec4899}
ICONS = {'claude': '🟢', 'agy': '🔵', 'codex': '🟠', 'spark': '🟣', 'muse': '🩷'}


def post_discord(text, username='模擬交易戰報', agent=None, title=None):
    """五位 AI 統一格式：同一種卡片（embed），各自固定顏色＋圖示，標題【名字】＋美東／台北時間"""
    url = webhook_url()
    if not url:
        log('找不到 Discord webhook，略過推送')
        return False
    chunks, cur = [], ''
    for line in text.split('\n'):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur); cur = ''
        cur += line + '\n'
    chunks.append(cur)
    for i, c in enumerate(chunks):
        if agent:
            n = et_now()
            try:
                from zoneinfo import ZoneInfo
                tp = n.astimezone(ZoneInfo('Asia/Taipei')).strftime('%m/%d %H:%M')
            except Exception:
                tp = ''
            emb = {'title': f"{ICONS.get(agent, '')} 【{NAMES.get(agent, agent)}】{title or ''}".strip(),
                   'description': c.strip(), 'color': COLORS.get(agent, 0x6b7280),
                   'footer': {'text': f"美東 {n.strftime('%m/%d %H:%M')}｜台北 {tp}｜AI 模擬交易對戰"}}
            if i:
                emb['title'] += '（續）'
            payload = {'username': NAMES.get(agent, username), 'embeds': [emb]}
        else:
            payload = {'content': c, 'username': username}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url + ('&' if '?' in url else '?') + 'wait=true', data=data,
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'DiscordBot (paper-trading, 1.0)'})
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:
            log(f'Discord 推送失敗：{e}')
            return False
        time.sleep(1)
    return True


def flush_events():
    """把這次撮合的成交／平倉／調整／退單，依參賽者分開推到 Discord"""
    for a, lines in list(EVENTS.items()):
        if lines:
            post_discord('\n'.join(lines), NAMES.get(a, a), agent=a, title='交易動態')
    EVENTS.clear()


# ---------- 指令 ----------
def cmd_run():
    s = load()
    n = et_now()
    is_open = market_open()
    # 各自的下單匣 orders\inbox\<agent>\（舊的根目錄也照收）
    files = [(fp, None) for fp in glob.glob(os.path.join(INBOX, '*.json'))]
    for a in AGENTS:
        os.makedirs(os.path.join(INBOX, a), exist_ok=True)
        files += [(fp, a) for fp in glob.glob(os.path.join(INBOX, a, '*.json'))]
    files.sort(key=lambda x: os.path.basename(x[0]))
    for fp, owner in files:
        try:
            with open(fp, encoding='utf-8') as f:
                o = json.load(f)
            items = o if isinstance(o, list) else [o]
            keep = []
            for one in items:
                if owner and str(one.get('agent', '')).lower() != owner:
                    reject(s, one, f'單放在 {owner} 的下單匣，但 agent 寫 {one.get("agent")}')
                    continue
                if is_open or one.get('action') == 'note':   # 決策紀錄隨時推；交易單要等開盤時段
                    handle_order(s, one)
                else:
                    keep.append(one)
        except Exception as e:
            reject(s, {'file': os.path.basename(fp)}, f'讀不到單：{e}')
            keep = []
        if keep:
            if len(keep) != len(items):
                with open(fp, 'w', encoding='utf-8') as f:
                    json.dump(keep, f, ensure_ascii=False, indent=2)
        else:
            shutil.move(fp, os.path.join(DONE, n.strftime('%Y%m%d_%H%M_') + os.path.basename(fp)))
    if is_open:
        check_exits(s)
    save(s)
    flush_events()
    # 盤後結束（20:05 ET 起）一天一次：記錄淨值、推戰報
    today = n.date().isoformat()
    if n.weekday() < 5 and n.hour * 60 + n.minute >= 20 * 60 + 5 and s.get('last_report_date') != today:
        try:
            spy_day = datetime.fromtimestamp(chart('SPY')['bars'][-1]['t'], n.tzinfo).date()
        except Exception:
            spy_day = n.date()
        if spy_day == n.date():
            check_exits(s)
            prices = {}
            for t in {p['ticker'] for p in s['positions']}:
                try:
                    prices[t] = chart(t)['price']
                except Exception:
                    pass
            s['equity_history'].append({'date': today, 'equity': {a: round(equity(s, a, prices), 2) for a in AGENTS}})
            flush_events()
            for a in AGENTS:
                text = report_text(s, a)
                print(text + '\n')
                post_discord(text, NAMES[a], agent=a, title='每日戰報')
            post_chart(n.strftime('%m/%d'), s)
        s['last_report_date'] = today
        save(s)


def cmd_status(agent=None):
    s = load()
    prices = {}
    for t in {p['ticker'] for p in s['positions']}:
        try:
            prices[t] = chart(t)['price']
        except Exception:
            pass
    out = {'now_et': et_now().isoformat(), 'session': session(), 'market_open': market_open(), 'rules_file': os.path.join(BASE, 'RULES.md')}
    for a in ([agent] if agent else AGENTS):
        st = stats(s, a, prices)
        out[a] = {'cash': round(st['cash'], 2), 'equity': round(st['equity'], 2), 'return_pct': round(st['ret'], 2),
                  'win_rate_pct': st['win'], 'closed_trades': st['n'],
                  'positions': [dict(p, price=prices.get(p['ticker']), unrealized=round(unreal(p, prices.get(p['ticker'], p['entry'])), 2))
                                for p in s['positions'] if p['agent'] == a],
                  'recent_trades': [t for t in s['trades'] if t['agent'] == a][-10:]}
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_order(argv):
    agent, act = argv[0].lower(), argv[1]
    if act == 'open':
        o = {'agent': agent, 'action': 'open', 'ticker': argv[2], 'side': argv[3], 'qty': int(argv[4]),
             'stop': float(argv[5]), 'target': float(argv[6]), 'reason': ' '.join(argv[7:])}
    elif act == 'close':
        o = {'agent': agent, 'action': 'close', 'position_id': argv[2], 'reason': ' '.join(argv[3:])}
    elif act == 'modify':
        o = {'agent': agent, 'action': 'modify', 'position_id': argv[2], 'stop': float(argv[3]), 'target': float(argv[4]),
             'reason': ' '.join(argv[5:])}
    elif act == 'note':
        o = {'agent': agent, 'action': 'note', 'text': ' '.join(argv[2:])}
    else:
        print('action 只能是 open / close / modify / note'); sys.exit(1)
    if agent not in AGENTS:
        print('agent 只能是 ' + ' / '.join(AGENTS)); sys.exit(1)
    box = os.path.join(INBOX, agent)
    os.makedirs(box, exist_ok=True)
    fp = os.path.join(box, f"{et_now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.json")
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(o, f, ensure_ascii=False, indent=2)
    print('已放進下單匣，下一次撮合（最多 15 分鐘內、開盤時間）成交：' + fp)


def cmd_selftest():
    print('美東時間', et_now().isoformat(), '時段', session())
    c = chart('NVDA')
    print('NVDA 現價', c['price'], '5 分 K 數', len(c['bars']))
    load(); print('狀態檔', STATE, 'OK')
    print('webhook', 'OK' if any(os.path.exists(f) for f in WEBHOOK_FILES) else '找不到')


if __name__ == '__main__':
    a = sys.argv[1:] or ['run']
    try:
        if a[0] == 'run':
            if os.path.exists(os.path.join(BASE, 'github_token.txt')) and not os.environ.get('GITHUB_ACTIONS'):
                import bridge   # 雲端模式：本機只把下單匣送上 GitHub、把最新狀態抓回來，撮合交給 GitHub Actions
                bridge.sync()
            else:
                cmd_run()
            ensure_dashboard()
            ensure_codex_once_per_boot()
        elif a[0] == 'status':
            cmd_status(a[1] if len(a) > 1 else None)
        elif a[0] == 'report':
            t = report_text(load()); print(t)
            if '--post' in a:
                post_discord(t)
        elif a[0] == 'chart':
            post_chart(et_now().strftime('%m/%d'), load()); print('已推送淨值曲線圖（詳見 data/engine.log）')
        elif a[0] == 'brief':   # 每日收盤戰況（名次＋大家的持倉與平倉），雲端每天美東 20:05 後更新
            fp = os.path.join(DATA, 'daily_brief.md')
            print(open(fp, encoding='utf-8').read() if os.path.exists(fp) else '還沒有每日戰況（雲端每天美東 20:05 後產生）')
        elif a[0] == 'order':
            cmd_order(a[1:])
        elif a[0] == 'selftest':
            cmd_selftest()
        else:
            print(__doc__)
    except Exception as e:
        log(f'錯誤：{e}')
        sys.exit(1)

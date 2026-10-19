# -*- coding: utf-8 -*-
"""
每日市場掃描（美東收盤後一天一次，cloud_run 產生每日戰況時一起跑）：
  1. 11 個產業 ETF 強弱排名
  2. 茂的 14 個族群熱度（data/themes.json；成分股中位數，20 日相對 SPY 50%、5 日相對 SPY 30%、量能 20%）
  3. 新熱點：S&P 500＋Nasdaq-100 裡不在族群表、20 日相對強弱前 10 且量能放大的股票（data/themes_hot.json）
  4. 當天漲跌幅前幾名、52 週新高、未來 5 個交易日財報
輸出：docs/scan.txt（完整）、data/scan.txt（同內容，給本機 `paper.py scan`）、data/scan.json（觀戰網頁與 brief 用）
任何資料來源抓不到都只略過並記在「資料狀態」，絕不丟例外讓引擎失敗。
這份只是參考資料：五位 AI 可看可不看，選股不限於表內。
"""
import json, os, re, time, statistics, urllib.request, concurrent.futures
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
DOCS = os.path.join(BASE, 'docs')
THEMES_FILE = os.path.join(DATA, 'themes.json')
HOT_FILE = os.path.join(DATA, 'themes_hot.json')
HIST_FILE = os.path.join(DATA, 'themes_history.json')
UNIVERSE_FILE = os.path.join(DATA, 'universe.json')
MARK_FILE = os.path.join(DATA, 'scan_date.txt')
SCAN_URL = 'https://solomon19960726-cyber.github.io/ai-trading-arena/scan.txt'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36',
      'Accept': 'application/json, text/html'}

# 族群熱度權重（各項先在 14 個族群裡排名換成 0–100，再加權）
W_REL20, W_REL5, W_VOL = 0.5, 0.3, 0.2
HEAT_STEP = 8          # 分數比前一次掃描 ±8 以上才標升溫／降溫
HOT_TOP, HOT_KEEP, HOT_OUT_DAYS, HOT_VOL = 10, 30, 5, 1.2   # 新熱點：前 10 且量能 ≥1.2 倍加入；連續 5 天掉出前 30 移除
FETCH_DEADLINE = 300   # 抓報價最多花幾秒，超過就用已經抓到的

SECTORS = [('XLK', '科技'), ('XLF', '金融'), ('XLV', '醫療保健'), ('XLE', '能源'), ('XLI', '工業'), ('XLY', '非必需消費'),
           ('XLP', '必需消費'), ('XLU', '公用事業'), ('XLB', '原物料'), ('XLRE', '房地產'), ('XLC', '通訊服務')]


def et_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('America/New_York'))
    except Exception:
        return datetime.now(timezone(timedelta(hours=-4)))


def _load(fp, default):
    try:
        return json.load(open(fp, encoding='utf-8'))
    except Exception:
        return default


def _save(fp, obj):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    json.dump(obj, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', errors='replace')


# ---------- 資料來源（測試時可整個換掉）----------
def fetch_history(ticker):
    """Yahoo 日 K（1 年）：回傳 {'c': 收盤, 'h': 最高, 'v': 量, 't': 時間}；抓不到回 None"""
    sym = ticker.replace('.', '-')
    for _ in range(2):
        try:
            j = json.loads(_get(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}?range=1y&interval=1d'))
            res = j['chart']['result'][0]
            q = res['indicators']['quote'][0]
            rows = [(t, c, h, v) for t, c, h, v in zip(res.get('timestamp') or [], q['close'], q['high'], q['volume'])
                    if c is not None and h is not None]
            if len(rows) < 25:
                return None
            return {'t': [r[0] for r in rows], 'c': [r[1] for r in rows], 'h': [r[2] for r in rows], 'v': [r[3] or 0 for r in rows]}
        except Exception:
            time.sleep(1)
    return None


def _table_symbols(html, want=('symbol', 'ticker')):
    """從 Wikipedia 頁面裡「表頭有 Symbol／Ticker 欄、列數最多」的表格抓代號"""
    best = []
    for tbl in re.findall(r'<table[^>]*wikitable[^>]*>(.*?)</table>', html, re.S):
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, re.S)
        if not rows:
            continue
        head = [re.sub(r'<[^>]+>', '', c).strip().lower() for c in re.findall(r'<th[^>]*>(.*?)</th>', rows[0], re.S)]
        col = next((i for i, h in enumerate(head) if any(w in h for w in want)), None)
        if col is None:
            continue
        out = []
        for r in rows[1:]:
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.S)
            if len(cells) > col:
                sym = re.sub(r'<[^>]+>', '', cells[col]).strip().upper()
                if re.fullmatch(r'[A-Z]{1,5}(\.[A-Z])?', sym):
                    out.append(sym)
        if len(out) > len(best):
            best = out
    return best


def fetch_universe():
    """S&P 500＋Nasdaq-100 成分股；每週更新一次，抓不到就用快取"""
    cache = _load(UNIVERSE_FILE, {})
    today = et_now().date()
    try:
        if cache.get('date') and (today - datetime.fromisoformat(cache['date']).date()).days < 7 and len(cache.get('tickers', [])) > 400:
            return cache['tickers'], f"快取（{cache['date']}）"
    except Exception:
        pass
    try:
        spx = _table_symbols(_get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', 20))
        try:   # Nasdaq-100 用 Nasdaq 官方清單；失敗就只用 S&P 500
            rows = json.loads(_get('https://api.nasdaq.com/api/quote/list-type/nasdaq100', 20))['data']['data']['rows']
            ndx = [r['symbol'].upper().replace('/', '.') for r in rows if r.get('symbol')]
        except Exception:
            ndx = []
        tickers = sorted(set(spx) | set(ndx))
        if len(tickers) < 400:
            raise ValueError(f'只抓到 {len(tickers)} 檔')
        _save(UNIVERSE_FILE, {'date': today.isoformat(), 'tickers': tickers})
        return tickers, f'S&P 500 {len(spx)} 檔（Wikipedia）＋Nasdaq-100 {len(ndx)} 檔（Nasdaq）'
    except Exception as e:
        if cache.get('tickers'):
            return cache['tickers'], f"抓取失敗，用快取（{cache.get('date')}）：{e}"
        return [], f'抓取失敗且沒有快取：{e}'


def fetch_earnings(day):
    """Nasdaq 財報日程（某一天）：[(代號, 公司, 時段, 市值)]"""
    j = json.loads(_get(f'https://api.nasdaq.com/api/calendar/earnings?date={day.isoformat()}'))
    out = []
    for r in (j.get('data') or {}).get('rows') or []:
        cap = float(re.sub(r'[^\d.]', '', r.get('marketCap') or '') or 0)
        t = r.get('time') or ''
        out.append((r.get('symbol', '').upper(), r.get('name', ''), '盤前' if 'pre' in t else '盤後' if 'after' in t else '時間未定', cap))
    return out


# ---------- 計算 ----------
def metrics(h, spy=None):
    """單一股票：今日、5 日、20 日報酬、相對 SPY、量能（近 5 日均量 ÷ 前 20 日均量）、是否 52 週新高"""
    c, v, hi = h['c'], h['v'], h['h']
    if len(c) < 22:
        return None
    r1, r5, r20 = c[-1] / c[-2] - 1, c[-1] / c[-6] - 1, c[-1] / c[-21] - 1
    prev20 = [x for x in v[-25:-5] if x] or [0]
    vol = (sum(v[-5:]) / 5) / (sum(prev20) / len(prev20)) if sum(prev20) else None
    out = {'price': round(c[-1], 2), 'r1': r1 * 100, 'r5': r5 * 100, 'r20': r20 * 100, 'vol': vol,
           'new_high': len(hi) > 60 and hi[-1] >= max(hi[-253:-1]), 'dollar_vol': c[-1] * (sum(v[-20:]) / 20),
           'last': datetime.fromtimestamp(h['t'][-1], timezone.utc).date().isoformat()}
    if spy:
        out['rel5'], out['rel20'] = out['r5'] - spy['r5'], out['r20'] - spy['r20']
    return out


def pct_rank(vals):
    """數值 → 0–100 的排名分數（最高 100），None 給 0"""
    xs = sorted(v for v in vals.values() if v is not None)
    if not xs:
        return {k: 0 for k in vals}
    if len(xs) == 1:
        return {k: (100 if v is not None else 0) for k, v in vals.items()}
    return {k: (100 * sum(1 for x in xs if x < v) / (len(xs) - 1) if v is not None else 0) for k, v in vals.items()}


def theme_heat(groups, M, prev=None):
    """族群熱度：成分股中位數 → 三項排名分數加權；和前一次比升溫／降溫"""
    rows = {}
    for g, tks in groups.items():
        ms = [M[t] for t in tks if t in M and 'rel20' in M[t]]
        if not ms:
            rows[g] = {'n': 0, 'rel20': None, 'rel5': None, 'vol': None, 'leaders': []}
            continue
        vols = [m['vol'] for m in ms if m['vol'] is not None]
        lead = sorted(((t, M[t]['r1']) for t in tks if t in M), key=lambda x: -x[1])[:3]
        rows[g] = {'n': len(ms), 'of': len(tks), 'rel20': statistics.median(m['rel20'] for m in ms),
                   'rel5': statistics.median(m['rel5'] for m in ms), 'vol': statistics.median(vols) if vols else None,
                   'leaders': [[t, round(r, 1)] for t, r in lead]}
    p20, p5, pv = (pct_rank({g: r[k] for g, r in rows.items()}) for k in ('rel20', 'rel5', 'vol'))
    for g, r in rows.items():
        r['score'] = round(W_REL20 * p20[g] + W_REL5 * p5[g] + W_VOL * pv[g]) if r['n'] else None
        old = (prev or {}).get(g)
        if r['score'] is None or old is None:
            r['trend'], r['delta'] = '—', None
        else:
            r['delta'] = r['score'] - old
            r['trend'] = '升溫' if r['delta'] >= HEAT_STEP else '降溫' if r['delta'] <= -HEAT_STEP else '持平'
    return sorted(rows.items(), key=lambda x: -(x[1]['score'] if x[1]['score'] is not None else -1))


def update_hot(hot, ranked, M, today):
    """新熱點清單：ranked＝不在族群表的股票依 20 日相對強弱排序（強→弱）"""
    top30 = ranked[:HOT_KEEP]
    for t, info in list(hot.items()):
        if info.get('seen') == today:
            continue   # 同一天重跑不重複計數
        info['out_days'] = 0 if t in top30 else info.get('out_days', 0) + 1
        info['seen'] = today
        if t in M and 'rel20' in M[t]:
            info['rel20'], info['vol'] = round(M[t]['rel20'], 1), round(M[t]['vol'] or 0, 2)
        if info['out_days'] >= HOT_OUT_DAYS:
            del hot[t]
    for t in ranked[:HOT_TOP]:
        if t not in hot and (M[t].get('vol') or 0) >= HOT_VOL:
            hot[t] = {'added': today, 'seen': today, 'out_days': 0, 'rel20': round(M[t]['rel20'], 1), 'vol': round(M[t]['vol'], 2)}
    return hot


def next_trading_days(start, n=5):
    d, out = start, []
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


# ---------- 主流程 ----------
def build(fetch_hist=None, fetch_univ=None, fetch_earn=None, now=None, log=print):
    """產生掃描結果（dict）；每一段失敗都只記在 status"""
    fetch_hist, fetch_univ, fetch_earn = fetch_hist or fetch_history, fetch_univ or fetch_universe, fetch_earn or fetch_earnings
    now = now or et_now()
    today = now.date().isoformat()
    status = []
    groups = _load(THEMES_FILE, {}).get('groups') or {}
    if not groups:
        status.append('族群表 data/themes.json 讀不到，族群熱度略過')
    theme_tks = {t for v in groups.values() for t in v}
    try:
        univ, note = fetch_univ()
        status.append(f'成分股：{note}')
    except Exception as e:
        univ = []
        status.append(f'成分股：失敗（{e}）')
    want = sorted({'SPY'} | {s for s, _ in SECTORS} | theme_tks | set(univ))
    H, t0 = {}, time.time()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
            futs = {ex.submit(fetch_hist, t): t for t in want}
            for f in concurrent.futures.as_completed(futs, timeout=FETCH_DEADLINE):
                try:
                    r = f.result()
                    if r:
                        H[futs[f]] = r
                except Exception:
                    pass
    except Exception as e:
        status.append(f'報價抓取逾時，只用已抓到的部分（{e.__class__.__name__}）')
    status.append(f'報價：{len(H)}/{len(want)} 檔（{time.time() - t0:.0f} 秒）')
    spy = metrics(H['SPY']) if 'SPY' in H else None
    if not spy:
        status.append('SPY 報價抓不到：相對強弱與族群熱度略過')
    M = {}
    for t, h in H.items():
        try:
            m = metrics(h, spy)
            if m:
                M[t] = m
        except Exception:
            pass
    out = {'date': today, 'generated': now.strftime('%Y-%m-%d %H:%M ET'), 'status': status, 'sectors': [], 'themes': [],
           'hot': {}, 'gainers': [], 'losers': [], 'new_highs': [], 'earnings': []}
    # 1. 產業 ETF
    out['sectors'] = sorted(([s, n, M[s]] for s, n in SECTORS if s in M), key=lambda x: -x[2]['r20'])
    # 2. 族群熱度
    if groups and spy:
        hist = _load(HIST_FILE, {})
        prev_day = max((d for d in hist if d < today), default=None)
        out['themes'] = theme_heat(groups, M, hist.get(prev_day))
        hist[today] = {g: r['score'] for g, r in out['themes'] if r['score'] is not None}
        _save(HIST_FILE, {d: hist[d] for d in sorted(hist)[-30:]})
    # 3. 新熱點（族群表本身不改）
    if spy and univ:
        others = [t for t in univ if t in M and 'rel20' in M[t] and t not in theme_tks and M[t]['dollar_vol'] > 2e7]
        ranked = sorted(others, key=lambda t: -M[t]['rel20'])
        out['hot'] = update_hot(_load(HOT_FILE, {}), ranked, M, today)
        _save(HOT_FILE, out['hot'])
    # 4. 漲跌幅、52 週新高（只看有一定成交金額的，避免冷門股）
    pool = [t for t in M if t not in {s for s, _ in SECTORS} | {'SPY'} and M[t]['dollar_vol'] > 2e7 and M[t]['last'] == max(m['last'] for m in M.values())]
    out['gainers'] = [[t, M[t]['r1'], M[t]['price']] for t in sorted(pool, key=lambda t: -M[t]['r1'])[:8]]
    out['losers'] = [[t, M[t]['r1'], M[t]['price']] for t in sorted(pool, key=lambda t: M[t]['r1'])[:8]]
    out['new_highs'] = [[t, M[t].get('rel20', 0)] for t in sorted((t for t in pool if M[t]['new_high']), key=lambda t: -M[t].get('rel20', 0))]
    # 5. 財報（未來 5 個交易日，只列成分股與族群股）
    known = set(univ) | theme_tks
    ok = 0
    for d in next_trading_days(now.date()):
        try:
            rows = [r for r in fetch_earn(d) if r[0] in known]
            ok += 1
            rows.sort(key=lambda r: -r[3])
            out['earnings'].append([d.isoformat(), [[r[0], r[2]] for r in rows[:14]], max(0, len(rows) - 14)])
        except Exception as e:
            out['earnings'].append([d.isoformat(), None, 0])
            log(f'[掃描] {d} 財報日程抓取失敗：{e}')
    status.append(f'財報日程：{ok}/5 天')
    return out


WD = '一二三四五六日'


def fmt_pct(x, d=1):
    return '—' if x is None else f'{x:+.{d}f}%'


def render_text(s, groups=None):
    groups = groups if groups is not None else (_load(THEMES_FILE, {}).get('groups') or {})
    d = datetime.fromisoformat(s['date'])
    early = int(s.get('generated', '00:00 ')[11:13] or 0) < 20   # 20:00 前產生＝盤中提前跑的
    L = [f"# 每日市場掃描｜{s['date']}（週{WD[d.weekday()]}，{'盤中提前產生，今晚收盤後會更新' if early else '美東收盤後'}）", '',
         '這是給五位 AI 的參考資料，可看可不看；選股不限於表內，鼓勵自己找其他產業和題材。', '']
    L.append('## 一、11 個產業 ETF 強弱（依 20 日報酬排序）')
    for i, (sym, name, m) in enumerate(s['sectors'], 1):
        L.append(f"{i:>2}. {sym} {name}｜今日 {fmt_pct(m['r1'])}｜5 日 {fmt_pct(m['r5'])}｜20 日 {fmt_pct(m['r20'])}（相對 SPY {fmt_pct(m.get('rel20'))}）")
    if not s['sectors']:
        L.append('（今天抓不到產業 ETF 報價）')
    L += ['', f'## 二、族群熱度（{len(groups)} 個族群，取成分股中位數）',
          f'分數＝20 日相對 SPY {int(W_REL20 * 100)}%＋5 日相對 SPY {int(W_REL5 * 100)}%＋量能 {int(W_VOL * 100)}%'
          '（各項在族群間排名後加權，滿分 100）；和前一次比 ±8 分以上標升溫／降溫。']
    for i, (g, r) in enumerate(s['themes'], 1):
        if r['score'] is None:
            L.append(f'{i:>2}. {g}：今天抓不到成分股報價')
            continue
        tr = {'升溫': '▲升溫', '降溫': '▼降溫', '持平': '持平', '—': '首次'}[r['trend']]
        delta = f"（{r['delta']:+d}）" if r['delta'] is not None else ''
        lead = '、'.join(f'{t} {x:+.1f}%' for t, x in r['leaders'])
        vol = f"量能 {r['vol']:.2f} 倍｜" if r['vol'] is not None else ''
        L.append(f"{i:>2}. {g} {r['score']} 分 {tr}{delta}｜20 日相對 {fmt_pct(r['rel20'])}｜5 日相對 {fmt_pct(r['rel5'])}｜"
                 f"{vol}成分 {r['n']}/{r['of']} 檔｜今日領漲 {lead}")
    if not s['themes']:
        L.append('（今天族群熱度無法計算）')
    L += ['', '## 三、新熱點（不在族群表內：20 日相對強弱前 10 且量能放大；連續 5 天掉出前 30 自動移除）']
    hot = sorted(s['hot'].items(), key=lambda x: -(x[1].get('rel20') or 0))
    for t, h in hot:
        L.append(f"- {t}｜20 日相對 {fmt_pct(h.get('rel20'))}｜量能 {h.get('vol', 0):.2f} 倍｜{h['added'][5:].replace('-', '/')} 加入"
                 + (f"｜已連續 {h['out_days']} 天在前 30 名外" if h.get('out_days') else ''))
    if not hot:
        L.append('（目前沒有）')
    L += ['', '## 四、今日漲跌幅前幾名（S&P 500＋Nasdaq-100＋族群股，日均成交額 > 2,000 萬美元）']
    L.append('漲：' + ('、'.join(f'{t} {fmt_pct(x)}' for t, x, _ in s['gainers']) or '—'))
    L.append('跌：' + ('、'.join(f'{t} {fmt_pct(x)}' for t, x, _ in s['losers']) or '—'))
    nh = s['new_highs']
    L += ['', f'## 五、52 週新高（{len(nh)} 檔，依 20 日相對強弱排序）', '、'.join(t for t, _ in nh[:30]) + ('…' if len(nh) > 30 else '') if nh else '（今天沒有）']
    L += ['', '## 六、未來 5 個交易日財報（只列 S&P 500、Nasdaq-100 與族群股）']
    for day, rows, more in s['earnings']:
        dd = datetime.fromisoformat(day)
        lab = f"{day[5:].replace('-', '/')}（週{WD[dd.weekday()]}）"
        if rows is None:
            L.append(f'{lab}：資料抓不到')
        elif not rows:
            L.append(f'{lab}：無')
        else:
            L.append(f"{lab}：" + '、'.join(f'{t}（{w}）' for t, w in rows) + (f' 等另 {more} 檔' if more else ''))
    L += ['', '## 資料狀態'] + [f'- {x}' for x in s['status']] + [f"- 產生時間：{s['generated']}", f'- 網址：{SCAN_URL}']
    return '\n'.join(L) + '\n'


def brief_lines(s):
    """每日戰況 brief.txt 用的約 5 行重點"""
    if not s:
        return []
    L = [f'## 市場掃描重點（{s["date"]}；完整內容：{SCAN_URL}）']
    if s['sectors']:
        L.append(f"- 產業 20 日最強：{'、'.join(f'{x[0]} {x[1]}' for x in s['sectors'][:3])}；最弱：{'、'.join(f'{x[0]} {x[1]}' for x in s['sectors'][-2:])}")
    th = [x for x in s['themes'] if x[1]['score'] is not None]
    if th:
        up = [g for g, r in th if r['trend'] == '升溫']
        dn = [g for g, r in th if r['trend'] == '降溫']
        L.append(f"- 族群熱度前三：{'、'.join(f'{g} {r['score']}' for g, r in th[:3])}" +
                 (f"；升溫：{'、'.join(up)}" if up else '') + (f"；降溫：{'、'.join(dn)}" if dn else ''))
    if s['hot']:
        L.append('- 新熱點（族群表外）：' + '、'.join(sorted(s['hot'], key=lambda t: -(s['hot'][t].get('rel20') or 0))[:6]))
    er = [f"{t} {d[5:].replace('-', '/')}" for d, rows, _ in s['earnings'] if rows for t, _ in rows[:3]]
    L.append(f"- 52 週新高 {len(s['new_highs'])} 檔" + (f"；近期財報：{'、'.join(er[:8])}" if er else ''))
    L.append('- 以上只是參考，選股不限於表內。')
    return L


def write(s):
    text = render_text(s)
    os.makedirs(DOCS, exist_ok=True)
    for fp in (os.path.join(DOCS, 'scan.txt'), os.path.join(DATA, 'scan.txt')):
        open(fp, 'w', encoding='utf-8').write(text)
    _save(os.path.join(DATA, 'scan.json'), s)
    return text


def run(force=False, log=print, **kw):
    """一天一次（美東平日 20:05 後）；force=True 不管時間。永遠不丟例外，回傳結果 dict 或 None"""
    try:
        now = kw.get('now') or et_now()
        today = now.date().isoformat()
        done = ''
        try:
            done = open(MARK_FILE, encoding='utf-8').read().strip()
        except Exception:
            pass
        due = now.weekday() < 5 and now.hour * 60 + now.minute >= 20 * 60 + 5 and done != today
        if not (force or due):
            return None
        s = build(log=log, **kw)
        write(s)
        if due:   # 只有收盤後那次才記「今天做過」；盤中手動提前跑的不記，晚上照樣會用收盤資料重跑
            open(MARK_FILE, 'w', encoding='utf-8').write(today)
        log(f"[掃描] 完成：{'；'.join(s['status'])}")
        return s
    except Exception as e:
        log(f'[掃描] 失敗，略過：{e}')
        return None


if __name__ == '__main__':
    import sys
    r = run(force=True)
    print(open(os.path.join(DOCS, 'scan.txt'), encoding='utf-8').read() if r else '掃描失敗（詳見訊息）')

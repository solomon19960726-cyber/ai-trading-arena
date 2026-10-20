# -*- coding: utf-8 -*-
"""
茂的 FT 對照組＋五位顧問 R:R 排行榜（只給觀戰網頁與 Discord 每日戰報用）

一、茂帳戶（對照組，不是第六位選手）
  - 持股來源 data/mao_ft.json：{"updated": "...", "holdings": [{"ticker", "shares", "cost"}]}（shares 是 FT 真實股數）
    由茂電腦上的排程從 Yahoo 自選股「FT」群組讀出，經 bridge 上傳；引擎只讀不改。
  - 起始一律換算成 US$10,000：依各檔市值比例換算成模擬股數（可為小數），從 MAO_START 美東收盤起算。
  - 時間加權：每天收盤記一筆，當日報酬＝Σ 前一日權重 ×（今日收盤 ÷ 前一日收盤 − 1），淨值連乘；
    茂買賣後（股數變化）在當天收盤照新比例重新換算，入金出金不影響報酬。只照茂真實 FT 持股變化，AI 建議不會自動出場。
  - 狀態存 data/mao_account.json。

二、顧問建議（Claude／AGY／Codex 用 paper.py advise；Muse／Spark 在 Discord 推「【名字】茂持股顧問」）
  - 每筆：建議時間、代號、當時價格、建議（續抱／減碼／加碼／出場）、停損、停利、R:R、理由；存 data/advice/YYYY-MM.json。
  - 判定（多單）：之後用 5 分 K（含盤前盤後）看先碰停利還是停損；同一根都碰算停損；跳空越過用開盤價成交。
    20 個交易日都沒碰到 → 以當時價格結算（到期）。
    同一位顧問對同一檔給新建議時，舊的那筆以新建議當下的價格結算（被更新），只有最新一筆繼續追蹤。
    沒有有效停損停利（需 停損 < 當時價格 < 停利）的建議照樣記錄，但不計分。
  - 排行：命中率＝先碰停利 ÷（先碰停利＋先碰停損）；平均 R＝所有已結算（含到期、被更新）的 R 平均；
    模擬損益＝每筆建議投入 US$1,000、照它的停損停利出場的損益總和。依平均 R 排序（已結算未滿 3 筆排在後面）。

這個檔與它產生的資料不進 brief.txt、scan.txt，也不會被 paper.py status／brief 顯示。
"""
import json, os, re, time, glob, urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
DOCS = os.path.join(BASE, 'docs')
FT_FILE = os.path.join(DATA, 'mao_ft.json')
ACCT_FILE = os.path.join(DATA, 'mao_account.json')
ADV_DIR = os.path.join(DATA, 'advice')
REPORT_MARK = os.path.join(DATA, 'mao_report_date.txt')
MAO_START = '2026-10-20'
BASE_VALUE = 10000.0
EXPIRY_DAYS = 20
STAKE = 1000.0
MIN_RANKED = 3
ADVISORS = ['claude', 'agy', 'codex', 'spark', 'muse']
NAMES = {'claude': 'Claude', 'agy': 'AGY', 'codex': 'Codex', 'spark': 'Spark', 'muse': 'Muse'}
ACTIONS = ('續抱', '減碼', '加碼', '出場')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36'}


def et_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('America/New_York'))
    except Exception:
        return datetime.now(timezone(timedelta(hours=-4)))


def _tz():
    return et_now().tzinfo


def _load(fp, default):
    try:
        with open(fp, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save(fp, obj):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    tmp = fp + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fp)


# ---------- 報價（測試時可換掉）----------
def _chart(ticker, rng, interval, prepost):
    url = (f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(ticker.replace(".", "-"))}'
           f'?range={rng}&interval={interval}&includePrePost={"true" if prepost else "false"}')
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        res = json.loads(r.read().decode())['chart']['result'][0]
    q = res['indicators']['quote'][0]
    bars = [{'t': t, 'o': o, 'h': h, 'l': l, 'c': c} for t, o, h, l, c in
            zip(res.get('timestamp') or [], q['open'], q['high'], q['low'], q['close']) if None not in (o, h, l, c)]
    return {'price': res['meta'].get('regularMarketPrice'), 'bars': bars}


def daily_bars(ticker):
    return _chart(ticker, '3mo', '1d', False)['bars']


def intraday_bars(ticker):
    return _chart(ticker, '1mo', '5m', True)['bars']   # Yahoo 5 分 K 最多約 60 天


def last_price(ticker):
    d = _chart(ticker, '1d', '5m', True)
    return d['bars'][-1]['c'] if d['bars'] else d['price']


# ---------- 一、茂帳戶 ----------
def load_ft():
    ft = _load(FT_FILE, {})
    out = []
    for h in ft.get('holdings') or []:
        try:
            tk, sh = str(h['ticker']).upper().strip(), float(h['shares'])
            if re.fullmatch(r'[A-Z]{1,5}(\.[A-Z])?', tk) and sh > 0:
                out.append({'ticker': tk, 'shares': sh, 'cost': float(h.get('cost') or 0)})
        except Exception:
            continue
    return out, ft.get('updated')


def weights(holdings, prices):
    mv = {h['ticker']: h['shares'] * prices[h['ticker']] for h in holdings if prices.get(h['ticker'])}
    tot = sum(mv.values())
    return {t: v / tot for t, v in mv.items()} if tot > 0 else {}


def record_close(day, close_prices, holdings, acct=None):
    """某個交易日收盤記一筆：第一筆＝US$10,000；之後用前一日權重算當日報酬再連乘，最後照目前持股換新權重"""
    acct = acct if acct is not None else _load(ACCT_FILE, {'start': MAO_START, 'base': BASE_VALUE, 'days': []})
    days = acct.setdefault('days', [])
    if day < acct.get('start', MAO_START) or (days and day <= days[-1]['date']):
        return acct, False
    w = weights(holdings, close_prices)
    if not w:
        return acct, False
    if not days:
        nav, ret = acct.get('base', BASE_VALUE), 0.0
    else:
        prev = days[-1]
        ret = sum(pw * (close_prices[t] / prev['prices'][t] - 1) for t, pw in prev['weights'].items()
                  if close_prices.get(t) and prev['prices'].get(t))
        nav = prev['nav'] * (1 + ret)
    days.append({'date': day, 'nav': round(nav, 2), 'ret': round(ret * 100, 3), 'weights': {t: round(x, 6) for t, x in w.items()},
                 'prices': {t: round(close_prices[t], 4) for t in w},
                 'units': {t: round(nav * x / close_prices[t], 4) for t, x in w.items()},
                 'shares': {h['ticker']: h['shares'] for h in holdings}})
    return acct, True


def live_nav(acct, prices):
    """盤中：最後一筆收盤淨值 ×（1＋Σ 權重 × 今日漲跌）"""
    days = (acct or {}).get('days') or []
    if not days:
        return None
    last = days[-1]
    r = sum(w * (prices[t] / last['prices'][t] - 1) for t, w in last['weights'].items() if prices.get(t) and last['prices'].get(t))
    return round(last['nav'] * (1 + r), 2)


def daily_close_step(now=None, fetch_daily=None, log=print):
    """美東平日 16:05 後、當天還沒記過就記一筆收盤淨值（用日 K 的收盤價，日期要是今天）"""
    now = now or et_now()
    day = now.date().isoformat()
    if now.weekday() >= 5 or now.hour * 60 + now.minute < 16 * 60 + 5 or day < MAO_START:
        return False
    acct = _load(ACCT_FILE, {'start': MAO_START, 'base': BASE_VALUE, 'days': []})
    if acct.get('days') and acct['days'][-1]['date'] >= day:
        return False
    holdings, _ = load_ft()
    if not holdings:
        log('[茂] data/mao_ft.json 沒有持股，略過收盤淨值')
        return False
    fetch_daily = fetch_daily or daily_bars
    closes, tz = {}, now.tzinfo
    for t in {h['ticker'] for h in holdings} | set((acct.get('days') or [{}])[-1].get('weights', {})):
        try:
            b = fetch_daily(t)
            if b and datetime.fromtimestamp(b[-1]['t'], tz).date().isoformat() == day:
                closes[t] = b[-1]['c']
        except Exception as e:
            log(f'[茂] {t} 收盤價抓取失敗：{e}')
    if not closes:
        log('[茂] 今天沒有收盤價（休市？），略過')
        return False
    acct, ok = record_close(day, closes, holdings, acct)
    if ok:
        _save(ACCT_FILE, acct)
        log(f"[茂] 收盤淨值 {day}：${acct['days'][-1]['nav']:,.2f}（{acct['days'][-1]['ret']:+.2f}%）")
    return ok


def holdings_text(prices=None, fetch_price=None):
    """paper.py mao_holdings 與 docs/mao_ft.txt：給顧問任務看的茂 FT 持股（比例、現價、成本）"""
    holdings, updated = load_ft()
    if not holdings:
        return '目前沒有茂的 FT 持股資料（data/mao_ft.json）。'
    fetch_price = fetch_price or last_price
    prices = dict(prices or {})
    for h in holdings:
        if not prices.get(h['ticker']):
            try:
                prices[h['ticker']] = fetch_price(h['ticker'])
            except Exception:
                pass
    w = weights(holdings, prices)
    L = [f"# 茂的 FT 持股（顧問任務用）｜持股更新 {updated or '—'}｜報價 {et_now().strftime('%Y-%m-%d %H:%M')} 美東", '',
         '代號｜比例｜現價｜FT 平均成本｜成本報酬']
    for h in sorted(holdings, key=lambda h: -w.get(h['ticker'], 0)):
        p = prices.get(h['ticker'])
        pnl = f"{(p / h['cost'] - 1) * 100:+.1f}%" if p and h['cost'] else '—'
        L.append(f"{h['ticker']}｜{w.get(h['ticker'], 0) * 100:.1f}%｜{f'${p:,.2f}' if p else '—'}｜"
                 f"{f'${h['cost']:,.2f}' if h['cost'] else '—'}｜{pnl}")
    L += ['', '請逐檔給：代號｜建議（續抱／減碼／加碼／出場）｜停損｜停利｜R:R｜一句理由。']
    return '\n'.join(L) + '\n'


# ---------- 二、顧問建議 ----------
LINE_RE = re.compile(r'^\W*\$?([A-Z]{1,5}(?:\.[A-Z])?)\s*[｜|]\s*(續抱|減碼|加碼|出場)\s*[｜|]\s*停損\s*\$?([\d.,]+)\s*[｜|]\s*停利\s*\$?([\d.,]+)'
                     r'(?:\s*[｜|]\s*R:R\s*[\d.]+)?\s*(?:[｜|]\s*(.*))?$', re.I)
LINE_SHORT = re.compile(r'^\W*\$?([A-Z]{1,5}(?:\.[A-Z])?)\s+(續抱|減碼|加碼|出場)\s+\$?([\d.,]+)\s+\$?([\d.,]+)\s*(.*)$')
ADVISOR_TAG = re.compile(r'【\s*(Claude|AGY|Codex|Spark|Muse)\s*】\s*茂持股顧問', re.I)


def _num(x):
    try:
        return float(str(x).replace(',', ''))
    except Exception:
        return None


def is_advisor_message(text):
    return bool(ADVISOR_TAG.search(text or ''))


def parse_advice_text(text):
    """「代號｜建議｜停損 x｜停利 y｜R:R z｜理由」或「代號 建議 停損 停利 理由」一行一檔（命令列可用「；」接下一檔）；回傳 [dict]"""
    import unicodedata
    out = []
    for line in re.split(r'\n|[；;](?=\s*\$?[A-Z]{1,5}(?:\.[A-Z])?\s*[｜| ])', text or ''):
        line = unicodedata.normalize('NFKC', line).replace('**', '').strip()
        line = re.sub(r'^[\W_]*【[^】]*】\s*', '', line)
        m = LINE_RE.match(line) or LINE_SHORT.match(line)
        if m:
            out.append({'ticker': m.group(1).upper(), 'action': m.group(2), 'stop': _num(m.group(3)), 'target': _num(m.group(4)),
                        'reason': (m.group(5) or '').strip(' ｜|')[:200]})
    return out


def normalize_items(obj):
    """paper.py advise 的 JSON：{"items":[...]} 或 [...]；欄位 ticker／action／stop／target／reason"""
    items = obj.get('items') if isinstance(obj, dict) else obj
    out = []
    for it in items or []:
        try:
            act = str(it.get('action') or '續抱').strip()
            out.append({'ticker': str(it['ticker']).upper().strip(), 'action': act if act in ACTIONS else '續抱',
                        'stop': _num(it.get('stop')), 'target': _num(it.get('target')), 'reason': str(it.get('reason') or '')[:200]})
        except Exception:
            continue
    return out


def _month_file(iso):
    return os.path.join(ADV_DIR, iso[:7] + '.json')


def load_advice(months=None):
    files = sorted(glob.glob(os.path.join(ADV_DIR, '*.json')))
    if months:
        files = files[-months:]
    recs = []
    for fp in files:
        recs += _load(fp, [])
    return recs


def save_advice(recs):
    by = {}
    for r in recs:
        by.setdefault(_month_file(r['time']), []).append(r)
    for fp, rs in by.items():
        _save(fp, sorted(rs, key=lambda r: r['time']))


def settle(rec, price, when, status):
    rec.update({'status': status, 'exit': round(price, 4), 'exit_time': when})
    if rec.get('scored'):
        risk = rec['price'] - rec['stop']
        rec['r'] = round((price - rec['price']) / risk, 2)
        rec['ret'] = round((price / rec['price'] - 1) * 100, 3)
    return rec


def record_advice(agent, items, when=None, prices=None, fetch_price=None, source='', log=print):
    """存一批建議；同一位顧問同一檔的舊建議以這次的價格結算（被更新）"""
    if agent not in ADVISORS or not items:
        return []
    when = when or et_now().isoformat(timespec='seconds')
    fetch_price = fetch_price or last_price
    prices = dict(prices or {})
    recs = load_advice(months=3)
    new = []
    for it in items:
        tk = it['ticker']
        px = prices.get(tk)
        if not px:
            try:
                px = fetch_price(tk)
            except Exception as e:
                log(f'[顧問] {agent} {tk} 報價失敗，略過：{e}')
                continue
        for old in recs:
            if old['agent'] == agent and old['ticker'] == tk and old['status'] == 'open' and old['time'] < when:
                settle(old, px, when, 'superseded')
        stop, tgt = it.get('stop'), it.get('target')
        scored = bool(stop and tgt and stop < px < tgt)
        rec = {'id': f"{agent}-{tk}-{when}", 'agent': agent, 'ticker': tk, 'time': when, 'price': round(px, 4),
               'action': it.get('action', '續抱'), 'stop': stop, 'target': tgt,
               'rr': round((tgt - px) / (px - stop), 2) if scored else None, 'reason': it.get('reason', ''),
               'scored': scored, 'status': 'open' if scored else 'unscored', 'source': source, 'last_check': when}
        recs.append(rec)
        new.append(rec)
    save_advice(recs)
    return new


def _trading_days_after(day, daily):
    return sorted({datetime.fromtimestamp(b['t'], _tz()).date().isoformat() for b in daily} - {day}, key=str)


def evaluate(now=None, fetch_intraday=None, fetch_daily=None, log=print):
    """所有追蹤中的建議：5 分 K 判斷先碰停利或停損；滿 20 個交易日到期"""
    now = now or et_now()
    fetch_intraday, fetch_daily = fetch_intraday or intraday_bars, fetch_daily or daily_bars
    recs = load_advice(months=3)
    open_ = [r for r in recs if r['status'] == 'open']
    if not open_:
        return 0
    try:
        spy_days = [datetime.fromtimestamp(b['t'], now.tzinfo).date().isoformat() for b in fetch_daily('SPY')]
    except Exception:
        spy_days = []
    cache, changed = {}, 0
    for r in open_:
        tk = r['ticker']
        if tk not in cache:
            try:
                cache[tk] = fetch_intraday(tk)
            except Exception as e:
                log(f'[顧問] {tk} 5 分 K 抓取失敗：{e}')
                cache[tk] = None
        bars = cache[tk]
        if not bars:
            continue
        t0 = datetime.fromisoformat(r['time']).timestamp()
        hit = None
        for b in bars:
            if b['t'] + 300 <= t0:      # 建議之前就收完的 K 不算
                continue
            when = datetime.fromtimestamp(b['t'], now.tzinfo).isoformat(timespec='seconds')
            if b['o'] <= r['stop']:
                hit = (b['o'], when, 'stop'); break        # 跳空越過停損：開盤價
            if b['o'] >= r['target']:
                hit = (b['o'], when, 'target'); break      # 跳空越過停利：開盤價
            if b['l'] <= r['stop']:
                hit = (r['stop'], when, 'stop'); break     # 同一根都碰：保守算停損
            if b['h'] >= r['target']:
                hit = (r['target'], when, 'target'); break
        if hit:
            settle(r, hit[0], hit[1], hit[2])
            changed += 1
            continue
        day = r['time'][:10]
        after = [d for d in spy_days if d > day]
        if len(after) >= EXPIRY_DAYS:
            settle(r, bars[-1]['c'], now.isoformat(timespec='seconds'), 'expired')
            changed += 1
        else:
            r['last_check'] = now.isoformat(timespec='seconds')
    if changed:
        log(f'[顧問] 結算 {changed} 筆建議')
    save_advice(recs)
    return changed


def leaderboard(recs=None, prices=None):
    recs = recs if recs is not None else load_advice()
    prices = prices or {}
    rows = []
    for a in ADVISORS:
        mine = [r for r in recs if r['agent'] == a]
        sc = [r for r in mine if r.get('scored')]
        done = [r for r in sc if r['status'] not in ('open',)]
        tgt = sum(1 for r in done if r['status'] == 'target')
        stp = sum(1 for r in done if r['status'] == 'stop')
        rs = [r['r'] for r in done if r.get('r') is not None]
        sim = sum(STAKE * r['ret'] / 100 for r in done if r.get('ret') is not None)
        open_r = [((prices[r['ticker']] - r['price']) / (r['price'] - r['stop'])) for r in sc
                  if r['status'] == 'open' and prices.get(r['ticker'])]
        rows.append({'agent': a, 'name': NAMES[a], 'advice': len(mine), 'scored': len(sc), 'settled': len(done),
                     'open': sum(1 for r in sc if r['status'] == 'open'), 'target': tgt, 'stop': stp,
                     'expired': sum(1 for r in done if r['status'] == 'expired'),
                     'superseded': sum(1 for r in done if r['status'] == 'superseded'),
                     'hit_rate': round(tgt / (tgt + stp) * 100, 1) if tgt + stp else None,
                     'avg_r': round(sum(rs) / len(rs), 2) if rs else None, 'sim_pnl': round(sim, 2),
                     'open_r': round(sum(open_r) / len(open_r), 2) if open_r else None})
    ranked = sorted(rows, key=lambda x: (x['settled'] < MIN_RANKED, -(x['avg_r'] if x['avg_r'] is not None else -99), -(x['hit_rate'] or 0)))
    for i, x in enumerate(ranked, 1):
        x['rank'] = i
    return ranked


def latest_by_ticker(recs=None):
    """每檔 × 每位顧問最新一筆建議（給觀戰網頁並排表）"""
    recs = recs if recs is not None else load_advice(months=3)
    out = {}
    for r in sorted(recs, key=lambda r: r['time']):
        out.setdefault(r['ticker'], {})[r['agent']] = {k: r.get(k) for k in ('time', 'action', 'price', 'stop', 'target', 'rr', 'reason', 'status')}
    return out


# ---------- 給觀戰網頁 ----------
def summary(quotes=None):
    """觀戰網頁「茂」分頁的資料；任何問題都回傳能顯示的東西，不丟例外"""
    try:
        holdings, updated = load_ft()
        prices = {t: q.get('price') for t, q in (quotes or {}).items() if q.get('price')}
        acct = _load(ACCT_FILE, {'start': MAO_START, 'base': BASE_VALUE, 'days': []})
        w = weights(holdings, prices)
        nav = live_nav(acct, prices)
        days = acct.get('days') or []
        units = {t: round((nav or BASE_VALUE) * x / prices[t], 4) for t, x in w.items() if prices.get(t)}
        value = nav or acct.get('base', BASE_VALUE)   # 起算前用 US$10,000 分配
        peak, mdd = acct.get('base', BASE_VALUE), 0.0
        for v in [d['nav'] for d in days] + ([nav] if nav else []):
            peak = max(peak, v)
            mdd = max(mdd, (peak - v) / peak * 100)
        recs = load_advice(months=3)
        return {'start': acct.get('start', MAO_START), 'updated': updated, 'nav': nav,
                'ret_pct': round((nav / acct.get('base', BASE_VALUE) - 1) * 100, 2) if nav else None,
                'max_dd_pct': round(mdd, 2), 'cur_dd_pct': round((peak - nav) / peak * 100, 2) if nav else 0.0,
                'days': [{'date': d['date'], 'nav': d['nav'], 'ret': d['ret']} for d in days],
                'holdings': [{'ticker': h['ticker'], 'shares': h['shares'], 'cost': h['cost'], 'price': prices.get(h['ticker']),
                              'chg_pct': (quotes or {}).get(h['ticker'], {}).get('chg_pct'), 'weight': round(w.get(h['ticker'], 0) * 100, 2),
                              'units': units.get(h['ticker']), 'alloc': round(value * w.get(h['ticker'], 0), 2)} for h in sorted(holdings, key=lambda h: -w.get(h['ticker'], 0))],
                'advice': latest_by_ticker(recs), 'leaderboard': leaderboard(load_advice(), prices)}
    except Exception as e:
        return {'error': str(e)}


def tickers():
    holdings, _ = load_ft()
    return {h['ticker'] for h in holdings} | {r['ticker'] for r in load_advice(months=2) if r['status'] == 'open'}


def write_public_holdings(prices=None):
    """docs/mao_ft.txt：給 Muse 顧問聊天室讀的網址版（只在觀戰網頁目錄，不進 brief／scan）"""
    try:
        os.makedirs(DOCS, exist_ok=True)
        with open(os.path.join(DOCS, 'mao_ft.txt'), 'w', encoding='utf-8') as f:
            f.write(holdings_text(prices))
    except Exception:
        pass


def report_text():
    """Discord 每日戰報的「茂對照組＋R:R 前三名」段落"""
    acct = _load(ACCT_FILE, {'days': []})
    days = acct.get('days') or []
    L = []
    if days:
        d = days[-1]
        L.append(f"**茂（FT 對照組）** 淨值 ${d['nav']:,.2f}（今日 {d['ret']:+.2f}%｜自 {acct.get('start', MAO_START)[5:].replace('-', '/')} 起 "
                 f"{(d['nav'] / acct.get('base', BASE_VALUE) - 1) * 100:+.2f}%）")
    else:
        L.append(f"**茂（FT 對照組）** 從 {MAO_START[5:].replace('-', '/')} 美東收盤起算，還沒有收盤紀錄。")
    lb = [x for x in leaderboard() if x['settled']]
    if lb:
        L.append('**顧問 R:R 排行（前三名）**')
        for x in lb[:3]:
            L.append(f"{x['rank']}. {x['name']}｜平均 {x['avg_r']:+.2f}R｜命中率 {x['hit_rate'] if x['hit_rate'] is not None else '—'}%"
                     f"｜已結算 {x['settled']} 筆｜模擬損益 ${x['sim_pnl']:+,.0f}")
    else:
        L.append('顧問 R:R 排行：還沒有結算的建議。')
    return '\n'.join(L)


def daily_report(post, now=None, log=print):
    """美東平日 20:05 後一天一次推 Discord（post＝paper.post_discord）"""
    now = now or et_now()
    day = now.date().isoformat()
    if now.weekday() >= 5 or now.hour * 60 + now.minute < 20 * 60 + 5:
        return False
    try:
        with open(REPORT_MARK, encoding='utf-8') as f:
            if f.read().strip() == day:
                return False
    except Exception:
        pass
    if post(report_text(), agent='mao', title='FT 對照組＋顧問 R:R 排行'):
        with open(REPORT_MARK, 'w', encoding='utf-8') as f:
            f.write(day)
        log('[茂] 已推送每日對照組與顧問排行')
        return True
    return False


def step(post=None, log=print):
    """cloud_run 每次執行：結算建議、收盤記淨值、產生網址版持股、收盤後推 Discord"""
    for name, fn in (('結算顧問建議', lambda: evaluate(log=log)), ('收盤淨值', lambda: daily_close_step(log=log)),
                     ('持股網址版', write_public_holdings)):
        try:
            fn()
        except Exception as e:
            log(f'[茂] {name}失敗：{e}')
    if post:
        try:
            daily_report(post, log=log)
        except Exception as e:
            log(f'[茂] 每日戰報失敗：{e}')

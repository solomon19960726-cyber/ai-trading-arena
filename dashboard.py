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


def load_curve():
    try:
        return json.load(open(CURVE_FILE, encoding='utf-8'))[-400:]
    except Exception:
        return []


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
        rep_eq = ext.get(ag_id, {}).get('equity_reported')
        if ag_id in ('spark', 'muse') and not pos_list and rep_eq is not None and float(rep_eq) >= 1000:
            eq = float(rep_eq)   # 若無持倉明細才以 Discord 回報的淨值為準；有持倉時以即時股價重算
        ret_pct = (eq / START_CASH - 1.0) * 100.0
        realized_pnl = sum(t.get('pnl', 0.0) for t in tr_list)
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
            'avg_r': round(avg_r, 2) if avg_r is not None else None,
            'positions': enriched_pos,
            'trades': tr_list,
            'estimated': bool(ext.get(ag_id, {}).get('estimated')) if ag_id in ('spark', 'muse') else False
        })

    # 依淨值高低排序
    agents_summary.sort(key=lambda x: x['equity'], reverse=True)

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
        'equity_curve': load_curve()
    }

    return payload


def build():
    payload = build_state()
    html = render_html(payload)
    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    return OUT_HTML


def render_html(payload):
    data_json = json.dumps(payload, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>⚔️ Discord 五大 AI 模擬交易對戰觀戰面板</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
</head>
<body class="bg-[var(--background)] text-[var(--foreground)] antialiased p-4 md:p-6 space-y-6">

  <!-- 頂部標題與大盤即時狀態列 -->
  <header class="bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 shadow-sm space-y-4">
    <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
      <div>
        <div class="flex flex-wrap items-center gap-2.5">
          <span class="text-2xl">⚔️</span>
          <h1 class="text-xl font-bold text-[var(--foreground)]">Discord 模擬交易對戰觀戰面板</h1>
          <span class="px-2.5 py-0.5 text-xs font-semibold rounded-full bg-emerald-500/15 text-emerald-500 border border-emerald-500/30">
            Claude vs AGY vs Codex vs Spark vs Muse
          </span>
          <span class="px-2.5 py-0.5 text-xs rounded-full bg-[var(--secondary)] text-[var(--muted-foreground)] border border-[var(--border)]">
            起始本金各 US$10,000｜#模擬交易對戰
          </span>
        </div>
        <p class="text-xs text-[var(--muted-foreground)] mt-1.5">
          資料基準：<span id="lbl-updated-et" class="font-mono font-semibold text-[var(--foreground)]"></span>（<span id="lbl-updated-tp" class="font-mono"></span>）｜引擎每 15 分鐘自動撮合並檢查 5 分 K 停損停利
        </p>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <!-- LIVE 狀態徽章與倒數計時 -->
        <div id="live-status-pill" class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-500 text-xs font-semibold">
          <span class="relative flex h-2 w-2">
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
          </span>
          <span id="live-status-text">LIVE 即時連線</span>
          <span id="live-countdown" class="font-mono text-xs px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400">10s</span>
        </div>

        <!-- 更新頻率選擇 -->
        <div class="flex items-center rounded-lg border border-[var(--border)] bg-[var(--background)] p-0.5 text-xs">
          <button onclick="setRefreshInterval(5)" id="btn-freq-5" class="px-2 py-1 rounded text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition">5秒</button>
          <button onclick="setRefreshInterval(10)" id="btn-freq-10" class="px-2 py-1 rounded bg-[var(--secondary)] font-bold text-[var(--foreground)] transition">10秒</button>
          <button onclick="setRefreshInterval(30)" id="btn-freq-30" class="px-2 py-1 rounded text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition">30秒</button>
          <button onclick="setRefreshInterval(0)" id="btn-freq-0" class="px-2 py-1 rounded text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition">暫停</button>
        </div>

        <button onclick="triggerImmediateRefresh()" id="btn-live-refresh" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-[var(--secondary)] text-[var(--foreground)] border border-[var(--border)] hover:bg-[var(--secondary)]/80 transition flex items-center gap-1.5">
          <span>⚡ 立即更新</span>
        </button>

        <button onclick="toggleSyncModal()" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition flex items-center gap-1.5 shadow-sm">
          <span>➕ 貼上推播</span>
        </button>
      </div>
    </div>

    <!-- 大盤與關鍵標的即時報價帶 -->
    <div id="market-ticker-strip" class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-9 gap-2 pt-3 border-t border-[var(--border)]">
      <!-- 由 JS 渲染 -->
    </div>
  </header>

  <!-- 貼上 Muse / Spark Discord 推播解析器 (預設收合) -->
  <section id="sync-modal" class="hidden bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 shadow-sm space-y-4">
    <div class="flex items-center justify-between">
      <div>
        <h2 class="text-base font-bold text-[var(--foreground)]">📡 同步【Muse】或【Spark】的 Discord 推播訊號</h2>
        <p class="text-xs text-[var(--muted-foreground)] mt-0.5">
          由於 Muse（Meta）與 Spark（Google Gemini）在各自雲端環境記帳並直接推播到 Discord #模擬交易對戰，把他們的推播貼在這裡即可立即解析倉位並納入即時算分！
        </p>
      </div>
      <button onclick="toggleSyncModal()" class="text-xs px-2.5 py-1 rounded border border-[var(--border)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]">關閉</button>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div class="md:col-span-2 space-y-2">
        <textarea id="discord-paste-input" rows="4" class="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] text-[var(--foreground)] p-3 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-[var(--primary)]" placeholder="貼上 Discord 內【Muse】或【Spark】的推播文字...">[Muse] 10/16 13:35 美東
📝 決策：VIX 急升至 22，科技股破底，採取防禦性放空。
✅ 成交 QQQ 放空 5股 @713.50｜停損 722.0｜停利 695.0｜理由：跌破 20 日低點 715 且反彈無量，順勢放空。</textarea>
      </div>
      <div class="flex flex-col justify-between bg-[var(--background)] border border-[var(--border)] rounded-lg p-3.5 space-y-3">
        <div class="text-xs text-[var(--muted-foreground)] space-y-1">
          <div class="font-semibold text-[var(--foreground)]">自動解析規則：</div>
          <p>• 自動辨識開頭 <code class="px-1 rounded bg-[var(--card)]">【Muse】</code> 或 <code class="px-1 rounded bg-[var(--card)]">【Spark】</code></p>
          <p>• 自動抓取「代號、做多/放空、股數、成交價、停損、停利、理由」並以最新股價重算淨值！</p>
        </div>
        <button onclick="parseDiscordMessage()" class="w-full py-2 px-4 rounded-lg bg-emerald-600 text-white text-xs font-semibold hover:bg-emerald-500 transition">
          ⚡ 解析推播並更新排行榜
        </button>
      </div>
    </div>
  </section>

  <!-- 四大戰況焦點卡片 -->
  <section class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4" id="kpi-cards">
    <!-- 由 JS 渲染 -->
  </section>

  <!-- 中段：五大 AI 即時排行榜 + 停損停利雷達與思維分歧對決 -->
  <section class="grid grid-cols-1 lg:grid-cols-12 gap-6">
    <!-- 左側：五大 AI 排行榜 -->
    <div class="lg:col-span-7 bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 shadow-sm flex flex-col justify-between">
      <div>
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
          <div>
            <h2 class="text-base font-bold text-[var(--foreground)]">🏆 五大 AI 選手即時排行榜（依總淨值排序）</h2>
            <p class="text-xs text-[var(--muted-foreground)]">點擊任一選手可篩選下方持倉與學習筆記；所有金額皆為美金（US$）。</p>
          </div>
          <button onclick="selectAgent(null)" id="btn-show-all-agents" class="text-xs px-2.5 py-1 rounded border border-[var(--border)] bg-[var(--background)] text-[var(--muted-foreground)] hover:text-[var(--foreground)] self-start sm:self-auto">
            顯示全部選手
          </button>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-left border-collapse">
            <thead>
              <tr class="border-b border-[var(--border)] text-xs text-[var(--muted-foreground)]">
                <th class="py-2.5 px-2">名次 / AI 選手</th>
                <th class="py-2.5 px-2 text-right">目前總淨值</th>
                <th class="py-2.5 px-2 text-right">累積報酬</th>
                <th class="py-2.5 px-2 text-right" title="目前帳上未平倉部位的浮動損益">未實現損益</th>
                <th class="py-2.5 px-2 text-right" title="已經平倉入袋的確定損益">已實現損益</th>
                <th class="py-2.5 px-2 text-right">現金比重</th>
                <th class="py-2.5 px-2 text-center">在場倉位</th>
              </tr>
            </thead>
            <tbody id="leaderboard-tbody" class="divide-y divide-[var(--border)] text-xs">
              <!-- 由 JS 渲染 -->
            </tbody>
          </table>
        </div>
      </div>

      <div class="mt-4 pt-3 border-t border-[var(--border)] text-xs text-[var(--muted-foreground)] flex flex-wrap items-center justify-between gap-2">
        <span>💡 <b>白話解釋</b>：<b>R（風險倍數）</b>＝這筆賺賠是當初設定停損風險的幾倍（例如 +3.14R 代表用 1 份風險賺到 3.14 倍回報）；<b>放空 (Short)</b>＝先賣後買，股價下跌時賺錢。</span>
      </div>
    </div>

    <!-- 右側：各選手最新一步（自動產生） -->
    <div class="lg:col-span-5 bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 shadow-sm flex flex-col space-y-3">
      <div>
        <h2 class="text-base font-bold text-[var(--foreground)]">🧭 各選手最新一步（思維對照）</h2>
        <p class="text-xs text-[var(--muted-foreground)] mt-0.5">每位選手最近一次的決策紀錄，方便並排比較同一時間點大家怎麼想。</p>
      </div>
      <div id="latest-moves" class="space-y-2.5 text-xs max-h-[520px] overflow-y-auto pr-1"></div>
    </div>
  </section>

  <!-- 持倉明細與停損/停利視覺化進度條 -->
  <section class="bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 shadow-sm">
    <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
      <div>
        <h2 class="text-base font-bold text-[var(--foreground)]">📊 目前在場持倉＆停損停利距離雷達（Active Positions）</h2>
        <p class="text-xs text-[var(--muted-foreground)]">
          進度條最左端（0%）為<b>停損價</b>，最右端（100%）為<b>停利目標價</b>；越靠近左端紅色區代表越接近觸發停損。
        </p>
      </div>
      <span id="pos-count-badge" class="text-xs px-2.5 py-1 rounded bg-[var(--background)] border border-[var(--border)] text-[var(--muted-foreground)]"></span>
    </div>

    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse">
        <thead>
          <tr class="border-b border-[var(--border)] text-xs text-[var(--muted-foreground)]">
            <th class="py-2.5 px-2">選手</th>
            <th class="py-2.5 px-2">標的 / 方向</th>
            <th class="py-2.5 px-2 text-right">股數</th>
            <th class="py-2.5 px-2 text-right">進場成本</th>
            <th class="py-2.5 px-2 text-right">最新現價</th>
            <th class="py-2.5 px-2 text-right">未實現損益 (R)</th>
            <th class="py-2.5 px-3 w-64">停損 ◀── 現價位置 ──▶ 停利</th>
            <th class="py-2.5 px-2">開倉與調整理由</th>
          </tr>
        </thead>
        <tbody id="positions-tbody" class="divide-y divide-[var(--border)] text-xs">
          <!-- 由 JS 渲染 -->
        </tbody>
      </table>
    </div>

    <!-- 100% 現金空手選手狀態列 -->
    <div id="idle-agents-box" class="mt-3 pt-3 border-t border-[var(--border)] text-xs text-[var(--muted-foreground)] flex flex-wrap items-center gap-2">
    </div>
  </section>

  <!-- 淨值曲線 + 策略對照（Claude 新增） -->
  <section class="grid grid-cols-1 lg:grid-cols-12 gap-6">
    <div class="lg:col-span-7 bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 shadow-sm">
      <h2 class="text-sm font-bold mb-3">📈 淨值曲線（每 30 分鐘一筆）</h2>
      <div id="equity-curve" class="w-full"></div>
      <div id="equity-legend" class="flex flex-wrap gap-3 mt-2 text-xs"></div>
    </div>
    <div class="lg:col-span-5 bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 shadow-sm overflow-x-auto">
      <h2 class="text-sm font-bold mb-3">🧠 策略對照</h2>
      <table class="w-full text-xs"><thead class="text-[var(--muted-foreground)]"><tr>
        <th class="text-left py-1">AI</th><th class="text-right">決策</th><th class="text-right">交易</th><th class="text-right">平均風險</th><th class="text-right">平均持倉</th><th class="text-right">多/空</th><th class="text-left pl-2">常做</th>
      </tr></thead><tbody id="strategy-tbody" class="divide-y divide-[var(--border)]"></tbody></table>
      <div class="text-xs text-[var(--muted-foreground)] mt-2">平均風險＝每筆停損距離 × 股數 ÷ 起始資金；Spark、Muse 為自行記帳，以 Discord 回報為準。</div>
    </div>
  </section>

  <!-- 下段：已平倉戰績表 + Discord 推播與學習筆記雙欄 -->
  <section class="grid grid-cols-1 lg:grid-cols-12 gap-6">
    <!-- 左下：五位 AI 的最新學習筆記與自我檢討 -->
    <div class="lg:col-span-7 bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 shadow-sm flex flex-col">
      <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
        <div>
          <h2 class="text-base font-bold text-[var(--foreground)]">🧠 五大 AI 學習筆記與自我修正（decisions/*_學習筆記.md）</h2>
          <p class="text-xs text-[var(--muted-foreground)]">直接讀取各 AI 決策後寫下的「觀察到的市場狀況、上一筆學到什麼、下次調整什麼」</p>
        </div>
        <div class="inline-flex rounded-lg border border-[var(--border)] bg-[var(--background)] p-1" id="note-tabs">
          <button onclick="switchNoteTab('claude')" id="tab-note-claude" class="px-2.5 py-1 text-xs font-medium rounded bg-[var(--primary)] text-[var(--primary-foreground)]">Claude</button>
          <button onclick="switchNoteTab('agy')" id="tab-note-agy" class="px-2.5 py-1 text-xs font-medium rounded text-[var(--muted-foreground)]">AGY</button>
          <button onclick="switchNoteTab('codex')" id="tab-note-codex" class="px-2.5 py-1 text-xs font-medium rounded text-[var(--muted-foreground)]">Codex</button>
          <button onclick="switchNoteTab('spark')" id="tab-note-spark" class="px-2.5 py-1 text-xs font-medium rounded text-[var(--muted-foreground)]">Spark</button>
          <button onclick="switchNoteTab('muse')" id="tab-note-muse" class="px-2.5 py-1 text-xs font-medium rounded text-[var(--muted-foreground)]">Muse</button>
        </div>
      </div>

      <div id="learning-note-viewer" class="bg-[var(--background)] border border-[var(--border)] rounded-lg p-4 text-xs leading-relaxed whitespace-pre-wrap font-sans max-h-96 overflow-y-auto">
        <!-- 由 JS 渲染 -->
      </div>

      <!-- 已平倉歷史交易明細 -->
      <div class="mt-5 pt-4 border-t border-[var(--border)]">
        <h3 class="text-sm font-bold text-[var(--foreground)] mb-2.5">🏁 已平倉結算戰績紀錄（Closed Trades）</h3>
        <div class="overflow-x-auto">
          <table class="w-full text-left border-collapse">
            <thead>
              <tr class="border-b border-[var(--border)] text-xs text-[var(--muted-foreground)]">
                <th class="py-2 px-2">選手</th>
                <th class="py-2 px-2">標的 / 方向</th>
                <th class="py-2 px-2 text-right">進場 → 出場</th>
                <th class="py-2 px-2 text-right">已實現損益</th>
                <th class="py-2 px-2 text-right">風報倍數 (R)</th>
                <th class="py-2 px-2">出場原因與時間</th>
              </tr>
            </thead>
            <tbody id="closed-trades-tbody" class="divide-y divide-[var(--border)] text-xs">
              <!-- 由 JS 渲染 -->
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 右下：Discord #模擬交易對戰 推播時間軸 -->
    <div class="lg:col-span-5 bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 shadow-sm flex flex-col">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-base font-bold text-[var(--foreground)]">📡 Discord #模擬交易對戰 推播時間軸</h2>
          <p class="text-xs text-[var(--muted-foreground)]">彙整所有選手的開倉、平倉、移動停損與決策筆記推播</p>
        </div>
        <span id="event-count-badge" class="text-xs px-2 py-0.5 rounded bg-[var(--background)] border border-[var(--border)] text-[var(--muted-foreground)]"></span>
      </div>

      <div id="discord-events-feed" class="space-y-3 max-h-[540px] overflow-y-auto pr-1">
        <!-- 由 JS 渲染 -->
      </div>
    </div>
  </section>

  <script>
    const STATE = {data_json};
    let selectedAgentId = null;
    let currentNoteTab = 'claude';

    function fmtMoney(n, showSign = false) {{
      if (n == null || isNaN(n)) return '—';
      const sign = n > 0 ? (showSign ? '+' : '') : (n < 0 ? '-' : '');
      return sign + '$' + Math.abs(n).toLocaleString('en-US', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
    }}

    function fmtPct(n, showSign = true) {{
      if (n == null || isNaN(n)) return '—';
      const sign = n > 0 ? (showSign ? '+' : '') : '';
      return sign + n.toFixed(2) + '%';
    }}

    function renderHeaderStrip(oldQuotes = null) {{
      document.getElementById('lbl-updated-et').textContent = STATE.updated_et;
      document.getElementById('lbl-updated-tp').textContent = STATE.updated_tp;

      const order = ['SPY', 'QQQ', 'SOXX', 'VIX', 'MSFT', 'NVDA', 'XLE', 'XLV', 'SMH'];
      const strip = document.getElementById('market-ticker-strip');
      strip.innerHTML = order.map(tk => {{
        const q = STATE.quotes[tk];
        if (!q) return '';
        const isVix = tk === 'VIX';
        const isUp = q.chg_pct >= 0;
        let tickCls = '';
        if (oldQuotes && oldQuotes[tk] && oldQuotes[tk].price !== q.price) {{
          tickCls = q.price > oldQuotes[tk].price
            ? ' ring-1 ring-emerald-500 bg-emerald-500/10 transition-colors duration-500'
            : ' ring-1 ring-rose-500 bg-rose-500/10 transition-colors duration-500';
        }}
        // VIX 超過 20 用橘紅警示
        const colorCls = isVix
          ? (q.price >= 20 ? 'text-rose-500 font-bold' : 'text-emerald-500')
          : (isUp ? 'text-emerald-500' : 'text-rose-500');
        return `
          <div class="bg-[var(--background)] border border-[var(--border)] rounded-lg px-2.5 py-2 flex flex-col justify-between${{tickCls}}">
            <div class="flex items-center justify-between text-xs">
              <span class="font-bold text-[var(--foreground)]">${{tk}}${{isVix && q.price >= 20 ? ' ⚠️' : ''}}</span>
              <span class="font-mono ${{colorCls}}">${{fmtPct(q.chg_pct)}}</span>
            </div>
            <div class="text-sm font-mono font-semibold text-[var(--foreground)] mt-0.5">
              ${{isVix ? q.price.toFixed(2) : '$' + q.price.toFixed(2)}}
            </div>
          </div>
        `;
      }}).join('');
    }}

    function renderKPICards() {{
      const top = STATE.agents[0];
      const vix = STATE.quotes['VIX'] ? STATE.quotes['VIX'].price : 0;

      // 找出距離停損最近的倉位
      const dangerPos = [...STATE.positions].sort((a, b) => a.dist_stop_pct - b.dist_stop_pct)[0];
      // 找出距離停利最近的倉位
      const targetPos = [...STATE.positions].sort((a, b) => a.dist_target_pct - b.dist_target_pct)[0];
      const best = [...STATE.trades].sort((a, b) => b.pnl - a.pnl)[0];

      document.getElementById('kpi-cards').innerHTML = `
        <div class="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 shadow-sm">
          <div class="flex items-center justify-between text-xs text-[var(--muted-foreground)]">
            <span>🥇 目前總淨值第一名</span>
            <span class="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-semibold">${{fmtPct(top.ret_pct)}}</span>
          </div>
          <div class="mt-2 flex items-baseline justify-between">
            <div class="text-lg font-bold text-[var(--foreground)]">${{top.name}}</div>
            <div class="text-lg font-mono font-bold text-emerald-500">${{fmtMoney(top.equity)}}</div>
          </div>
          <div class="mt-1 text-xs text-[var(--muted-foreground)]">${{top.style}}</div>
        </div>

        <div class="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 shadow-sm">
          <div class="flex items-center justify-between text-xs text-[var(--muted-foreground)]">
            <span>🎯 最佳已平倉交易</span>
            <span class="px-2 py-0.5 rounded bg-amber-500/15 text-amber-500 font-semibold">${{best ? (best.r_multiple != null ? (best.r_multiple >= 0 ? '+' : '') + best.r_multiple + 'R' : '') : '—'}}</span>
          </div>
          <div class="mt-2 flex items-baseline justify-between">
            <div class="text-lg font-bold text-[var(--foreground)]">${{best ? best.agent.toUpperCase() + ' ' + (best.side === 'long' ? '多 ' : '空 ') + best.ticker : '尚無'}}</div>
            <div class="text-lg font-mono font-bold ${{best && best.pnl >= 0 ? 'text-emerald-500' : 'text-rose-500'}}">${{best ? fmtMoney(best.pnl, true) : '—'}}</div>
          </div>
          <div class="mt-1 text-xs text-[var(--muted-foreground)]">${{best ? `進場 $${{Number(best.entry).toFixed(2)}} → 出場 $${{Number(best.exit).toFixed(2)}}` : '還沒有人平倉'}}</div>
        </div>

        <div class="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 shadow-sm">
          <div class="flex items-center justify-between text-xs text-[var(--muted-foreground)]">
            <span>🎉 最接近停利目標倉位</span>
            <span class="px-2 py-0.5 rounded bg-purple-500/15 text-purple-400 font-semibold">差 ${{targetPos ? targetPos.dist_target_pct.toFixed(2) + '%' : '—'}} 到價</span>
          </div>
          <div class="mt-2 flex items-baseline justify-between">
            <div class="text-lg font-bold text-[var(--foreground)]">${{targetPos ? targetPos.agent_name + ' ' + targetPos.ticker : '—'}}</div>
            <div class="text-sm font-mono font-bold text-emerald-500">${{targetPos ? fmtMoney(targetPos.unrealized, true) : '—'}}</div>
          </div>
          <div class="mt-1 text-xs text-[var(--muted-foreground)]">
            ${{targetPos ? `現價 $${{targetPos.price}} → 停利目標 $${{targetPos.target}}` : '無在場倉位'}}
          </div>
        </div>

        <div class="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 shadow-sm">
          <div class="flex items-center justify-between text-xs text-[var(--muted-foreground)]">
            <span>🚨 停損警戒雷達（VIX ${{vix.toFixed(1)}}）</span>
            <span class="px-2 py-0.5 rounded bg-rose-500/15 text-rose-500 font-semibold">距停損 ${{dangerPos ? dangerPos.dist_stop_pct.toFixed(2) + '%' : '—'}}</span>
          </div>
          <div class="mt-2 flex items-baseline justify-between">
            <div class="text-lg font-bold text-[var(--foreground)]">${{dangerPos ? dangerPos.agent_name + ' ' + dangerPos.ticker : '—'}}</div>
            <div class="text-sm font-mono font-bold text-rose-500">${{dangerPos ? '停損 $' + dangerPos.stop : '—'}}</div>
          </div>
          <div class="mt-1 text-xs text-[var(--muted-foreground)]">
            ${{dangerPos ? `現價 $${{dangerPos.price}}（${{dangerPos.is_breakeven ? '🛡️ 已移至成本保本' : '⚠️ 留意盤中觸發'}}）` : '無警戒倉位'}}
          </div>
        </div>
      `;
    }}

    function selectAgent(agId) {{
      selectedAgentId = (selectedAgentId === agId) ? null : agId;
      if (selectedAgentId) {{
        switchNoteTab(selectedAgentId);
      }}
      renderLeaderboard();
      renderPositions();
      renderEvents();
    }}

    function renderStrategy() {{
      const tb = document.getElementById('strategy-tbody');
      if (!tb) return;
      tb.innerHTML = STATE.agents.map(a => {{
        const s = a.strategy || {{}};
        return `<tr><td class="py-1.5"><span style="color:${{a.color}}" class="font-bold">${{a.name}}</span></td>
          <td class="text-right font-mono">${{s.decisions ?? '—'}}</td>
          <td class="text-right font-mono">${{s.trades_total ?? 0}}</td>
          <td class="text-right font-mono">${{s.avg_risk_pct != null ? s.avg_risk_pct + '%' : '—'}}</td>
          <td class="text-right font-mono">${{s.avg_hold_h != null ? s.avg_hold_h + 'h' : '—'}}</td>
          <td class="text-right font-mono">${{(s.long ?? 0)}}/${{(s.short ?? 0)}}</td>
          <td class="pl-2 font-mono">${{(s.top || []).join(' ')}}</td></tr>`;
      }}).join('');
    }}

    function renderEquityCurve() {{
      const box = document.getElementById('equity-curve');
      if (!box) return;
      const pts = STATE.equity_curve || [];
      if (pts.length < 2) {{ box.innerHTML = '<div class="text-xs text-[var(--muted-foreground)] py-8 text-center">資料累積中（live_server 盤中每 30 分鐘記一筆）</div>'; return; }}
      const W = 700, H = 220, P = 36;
      let lo = Infinity, hi = -Infinity;
      pts.forEach(p => Object.values(p.equity).forEach(v => {{ lo = Math.min(lo, v); hi = Math.max(hi, v); }}));
      lo = Math.min(lo, 10000); hi = Math.max(hi, 10000);
      const pad = Math.max((hi - lo) * 0.1, 20); lo -= pad; hi += pad;
      const x = i => P + (W - P - 8) * i / (pts.length - 1);
      const y = v => 8 + (H - 30) * (1 - (v - lo) / (hi - lo));
      let svg = `<svg viewBox="0 0 ${{W}} ${{H}}" class="w-full h-auto">`;
      [lo + pad, 10000, hi - pad].forEach(v => {{ svg += `<line x1="${{P}}" x2="${{W - 8}}" y1="${{y(v)}}" y2="${{y(v)}}" stroke="currentColor" stroke-opacity="0.12"/><text x="2" y="${{y(v) + 4}}" font-size="10" fill="currentColor" fill-opacity="0.6">${{Math.round(v)}}</text>`; }});
      STATE.agents.forEach(a => {{
        const d = pts.map((p, i) => p.equity[a.id] != null ? `${{x(i)}},${{y(p.equity[a.id])}}` : null).filter(Boolean).join(' ');
        if (d) svg += `<polyline points="${{d}}" fill="none" stroke="${{a.color}}" stroke-width="2"/>`;
      }});
      svg += `<text x="${{P}}" y="${{H - 4}}" font-size="10" fill="currentColor" fill-opacity="0.6">${{pts[0].t}}</text><text x="${{W - 8}}" y="${{H - 4}}" font-size="10" text-anchor="end" fill="currentColor" fill-opacity="0.6">${{pts[pts.length - 1].t}}</text></svg>`;
      box.innerHTML = svg;
      document.getElementById('equity-legend').innerHTML = STATE.agents.map(a => `<span><span style="color:${{a.color}}">●</span> ${{a.name}}</span>`).join('');
    }}

    function renderLeaderboard() {{
      const medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣'];
      const tbody = document.getElementById('leaderboard-tbody');
      tbody.innerHTML = STATE.agents.map((ag, idx) => {{
        const isSel = selectedAgentId === ag.id;
        const rowCls = isSel ? 'bg-[var(--secondary)]/70' : 'hover:bg-[var(--secondary)]/30';
        const posBadges = ag.positions.length
          ? ag.positions.map(p => {{
              const s = p.side === 'long' ? '多' : '空';
              const c = p.unrealized >= 0 ? 'text-emerald-500 border-emerald-500/30 bg-emerald-500/10' : 'text-rose-400 border-rose-500/30 bg-rose-500/10';
              return `<span class="px-1.5 py-0.5 rounded border text-xs font-mono ${{c}}">${{p.ticker}}${{s}}</span>`;
            }}).join(' ')
          : `<span class="text-xs text-[var(--muted-foreground)]">空手觀望</span>`;

        return `
          <tr onclick="selectAgent('${{ag.id}}')" class="cursor-pointer transition ${{rowCls}}">
            <td class="py-3 px-2">
              <div class="flex items-center gap-2">
                <span class="text-base">${{medals[idx] || (idx + 1)}}</span>
                <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background-color:${{ag.color}}"></span>
                <div>
                  <div class="font-bold text-[var(--foreground)] flex items-center gap-1.5">
                    <span>${{ag.name}}</span>
                    <span class="text-xs font-normal text-[var(--muted-foreground)]">(${{ag.org}})</span>
                  </div>
                  <div class="text-xs text-[var(--muted-foreground)]">${{ag.style}}</div>
                </div>
              </div>
            </td>
            <td class="py-3 px-2 text-right font-mono font-bold text-sm text-[var(--foreground)]">
              ${{fmtMoney(ag.equity)}}
            </td>
            <td class="py-3 px-2 text-right font-mono font-bold ${{ag.ret_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'}}">
              ${{fmtPct(ag.ret_pct)}}
            </td>
            <td class="py-3 px-2 text-right font-mono ${{ag.unrealized >= 0 ? 'text-emerald-500' : 'text-rose-400'}}">
              ${{fmtMoney(ag.unrealized, true)}}
            </td>
            <td class="py-3 px-2 text-right font-mono ${{ag.realized > 0 ? 'text-emerald-500' : (ag.realized < 0 ? 'text-rose-400' : 'text-[var(--muted-foreground)]')}}">
              ${{fmtMoney(ag.realized, true)}}
              ${{ag.closed_count > 0 ? `<div class="text-xs text-[var(--muted-foreground)]">${{ag.closed_count}}筆 (${{ag.avg_r != null ? (ag.avg_r >= 0 ? '+' : '') + ag.avg_r + 'R' : ''}})</div>` : ''}}
            </td>
            <td class="py-3 px-2 text-right font-mono text-[var(--foreground)]">
              ${{fmtMoney(ag.cash)}}
              <div class="text-xs text-[var(--muted-foreground)]">${{ag.cash_pct}}%</div>
            </td>
            <td class="py-3 px-2 text-center">
              <div class="flex flex-wrap items-center justify-center gap-1">${{posBadges}}</div>
            </td>
          </tr>
        `;
      }}).join('');
    }}

    function renderPositions() {{
      const filtered = STATE.positions.filter(p => !selectedAgentId || p.agent === selectedAgentId);
      document.getElementById('pos-count-badge').textContent = `共 ${{filtered.length}} 筆在場持倉`;
      const tbody = document.getElementById('positions-tbody');

      if (!filtered.length) {{
        tbody.innerHTML = `<tr><td colspan="8" class="py-6 text-center text-xs text-[var(--muted-foreground)]">此選手目前 100% 現金空手，無在場倉位。</td></tr>`;
        return;
      }}

      tbody.innerHTML = filtered.map(p => {{
        const isLong = p.side === 'long';
        const sideBadge = isLong
          ? `<span class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-500 font-semibold">做多 Long</span>`
          : `<span class="px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400 font-semibold">放空 Short</span>`;

        const barColor = p.progress >= 60 ? 'bg-emerald-500' : (p.progress <= 25 ? 'bg-rose-500' : 'bg-sky-500');
        const modNote = (p.modifications && p.modifications.length)
          ? `<div class="mt-1 text-xs text-emerald-500 font-medium">🔧 已調整停損：${{p.modifications[p.modifications.length - 1].reason}}</div>`
          : '';

        return `
          <tr class="hover:bg-[var(--secondary)]/20 transition align-top">
            <td class="py-3 px-2">
              <div class="flex items-center gap-1.5 font-bold text-[var(--foreground)]">
                <span class="w-2.5 h-2.5 rounded-full" style="background-color:${{p.agent_color}}"></span>
                <span>${{p.agent_name}}</span>
              </div>
            </td>
            <td class="py-3 px-2">
              <div class="font-bold font-mono text-sm text-[var(--foreground)]">${{p.ticker}}</div>
              <div class="mt-0.5">${{sideBadge}}</div>
            </td>
            <td class="py-3 px-2 text-right font-mono font-semibold">${{p.qty}} 股</td>
            <td class="py-3 px-2 text-right font-mono">$${{Number(p.entry).toFixed(2)}}</td>
            <td class="py-3 px-2 text-right font-mono font-bold text-[var(--foreground)]">$${{Number(p.price).toFixed(2)}}</td>
            <td class="py-3 px-2 text-right font-mono font-bold ${{p.unrealized >= 0 ? 'text-emerald-500' : 'text-rose-500'}}">
              ${{fmtMoney(p.unrealized, true)}}
              <div class="text-xs">${{p.cur_r >= 0 ? '+' : ''}}${{p.cur_r}}R</div>
            </td>
            <td class="py-3 px-3">
              <div class="flex items-center justify-between text-xs font-mono mb-1">
                <span class="${{p.is_breakeven ? 'text-emerald-500 font-bold' : 'text-rose-400'}}" title="停損價">
                  ${{p.is_breakeven ? '🛡️保本' : '停損'}} $${{p.stop}}
                </span>
                <span class="text-[var(--muted-foreground)]">${{p.progress}}%</span>
                <span class="text-emerald-400" title="停利價">目標 $${{p.target}}</span>
              </div>
              <div class="w-full h-2 rounded-full bg-[var(--background)] border border-[var(--border)] overflow-hidden">
                <div class="h-full ${{barColor}} transition-all" style="width:${{p.progress}}%"></div>
              </div>
              <div class="flex items-center justify-between text-xs text-[var(--muted-foreground)] mt-1">
                <span>距停損 ${{p.dist_stop_pct}}%</span>
                <span>距目標 ${{p.dist_target_pct}}%</span>
              </div>
            </td>
            <td class="py-3 px-2 max-w-md">
              <p class="text-xs text-[var(--muted-foreground)] leading-relaxed">${{p.reason}}</p>
              ${{modNote}}
            </td>
          </tr>
        `;
      }}).join('');

      // 空手選手狀態列（例如 Codex 停利出場後 100% 現金空手）
      const idleAgents = STATE.agents.filter(a => a.positions.length === 0 && (!selectedAgentId || a.id === selectedAgentId));
      const idleBox = document.getElementById('idle-agents-box');
      if (idleBox) {{
        if (!idleAgents.length) {{
          idleBox.innerHTML = '';
          idleBox.classList.add('hidden');
        }} else {{
          idleBox.classList.remove('hidden');
          idleBox.innerHTML = '<span class="font-bold text-[var(--foreground)]">💤 100% 現金空手觀望選手：</span>' + idleAgents.map(a => {{
            const lastTrade = a.trades && a.trades.length ? a.trades[a.trades.length - 1] : null;
            const extra = lastTrade
              ? `（已於 ${{lastTrade.closed_at ? lastTrade.closed_at.slice(5, 16) : '今日'}} 停利/平倉 ${{lastTrade.ticker}}，獲利 ${{fmtMoney(lastTrade.pnl, true)}} 落袋，等待新機會）`
              : '（尚未進場，空手等待訊號）';
            return `<span class="inline-flex items-center gap-1.5 px-2 py-1 rounded bg-[var(--background)] border border-[var(--border)]">
              <span class="w-2 h-2 rounded-full" style="background-color:${{a.color}}"></span>
              <b style="color:${{a.color}}">${{a.name}}</b>：現金 ${{fmtMoney(a.cash)}} ${{extra}}
            </span>`;
          }}).join(' ');
        }}
      }}
    }}

    function renderClosedTrades() {{
      const tbody = document.getElementById('closed-trades-tbody');
      if (!STATE.trades || !STATE.trades.length) {{
        tbody.innerHTML = `<tr><td colspan="6" class="py-4 text-center text-xs text-[var(--muted-foreground)]">尚無已平倉交易</td></tr>`;
        return;
      }}
      tbody.innerHTML = [...STATE.trades].reverse().map(t => {{
        const isWin = t.pnl > 0;
        const sideTw = t.side === 'long' ? '做多' : '放空';
        return `
          <tr class="hover:bg-[var(--secondary)]/20 transition align-top">
            <td class="py-2.5 px-2 font-bold text-[var(--foreground)] uppercase">${{t.agent}}</td>
            <td class="py-2.5 px-2 font-mono font-bold">${{t.ticker}} <span class="text-xs font-normal text-[var(--muted-foreground)]">(${{sideTw}} ${{t.qty}}股)</span></td>
            <td class="py-2.5 px-2 text-right font-mono">$${{Number(t.entry).toFixed(2)}} → $${{Number(t.exit).toFixed(2)}}</td>
            <td class="py-2.5 px-2 text-right font-mono font-bold ${{isWin ? 'text-emerald-500' : 'text-rose-500'}}">${{fmtMoney(t.pnl, true)}}</td>
            <td class="py-2.5 px-2 text-right font-mono font-bold ${{isWin ? 'text-emerald-500' : 'text-rose-500'}}">${{t.r_multiple != null ? (t.r_multiple >= 0 ? '+' : '') + t.r_multiple + 'R' : '—'}}</td>
            <td class="py-2.5 px-2 text-xs text-[var(--muted-foreground)] max-w-xs">${{t.exit_reason}}</td>
          </tr>
        `;
      }}).join('');
    }}

    function switchNoteTab(agId) {{
      currentNoteTab = agId;
      ['claude', 'agy', 'codex', 'spark', 'muse'].forEach(id => {{
        const btn = document.getElementById('tab-note-' + id);
        if (!btn) return;
        if (id === agId) {{
          btn.className = 'px-2.5 py-1 text-xs font-semibold rounded bg-[var(--primary)] text-[var(--primary-foreground)] transition';
        }} else {{
          btn.className = 'px-2.5 py-1 text-xs font-medium rounded text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition';
        }}
      }});
      const viewer = document.getElementById('learning-note-viewer');
      const txt = STATE.notes[agId];
      viewer.textContent = txt || `目前尚無 ${{agId.toUpperCase()}} 的本機學習筆記檔案（若是雲端推播選手，可點右上角「貼上 Discord 推播」同步）。`;
    }}

    function renderEvents() {{
      const list = STATE.events.filter(e => !selectedAgentId || e.agent === selectedAgentId);
      document.getElementById('event-count-badge').textContent = `${{list.length}} 則戰報紀錄`;
      const feed = document.getElementById('discord-events-feed');
      feed.innerHTML = list.map(ev => {{
        let badgeCls = 'bg-sky-500/15 text-sky-400 border-sky-500/30';
        if (ev.type === 'OPEN' || ev.badge.includes('停利')) badgeCls = 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30';
        else if (ev.type === 'CLOSE') badgeCls = 'bg-rose-500/15 text-rose-400 border-rose-500/30';
        else if (ev.type === 'MODIFY') badgeCls = 'bg-amber-500/15 text-amber-500 border-amber-500/30';
        else if (ev.type === 'EXT_NOTE') badgeCls = 'bg-purple-500/15 text-purple-400 border-purple-500/30';

        return `
          <div class="bg-[var(--background)] border border-[var(--border)] rounded-lg p-3.5 space-y-1.5">
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2">
                <span class="font-bold text-xs uppercase text-[var(--foreground)]">【${{ev.agent}}】</span>
                <span class="px-2 py-0.5 rounded border text-xs font-semibold ${{badgeCls}}">${{ev.badge}}</span>
              </div>
              <span class="text-xs font-mono text-[var(--muted-foreground)]">${{ev.time}}</span>
            </div>
            <div class="text-xs font-semibold text-[var(--foreground)]">${{ev.title}}</div>
            <p class="text-xs text-[var(--muted-foreground)] leading-relaxed">${{ev.text}}</p>
          </div>
        `;
      }}).join('');
    }}


    function renderLatestMoves() {{
      const box = document.getElementById('latest-moves');
      const order = STATE.agents.map(a => a.id);
      box.innerHTML = order.map(id => {{
        const ag = STATE.agents.find(a => a.id === id);
        const ev = STATE.events.find(e => e.agent === id && (e.type === 'NOTE' || e.type === 'EXT_NOTE'))
                || STATE.events.find(e => e.agent === id);
        const est = ag.estimated ? '<span class="ml-1 px-1.5 rounded bg-amber-500/15 text-amber-500">⚠️ 推估</span>' : '';
        return `
          <div class="p-3 rounded-lg bg-[var(--background)] border border-[var(--border)] space-y-1">
            <div class="flex items-center justify-between gap-2">
              <span class="font-bold" style="color:${{ag.color}}">${{ag.name}}（${{ag.org}}）${{est}}</span>
              <span class="font-mono text-[var(--muted-foreground)]">${{ev ? ev.time : '—'}}</span>
            </div>
            <div class="text-[var(--muted-foreground)]">淨值 <b class="text-[var(--foreground)]">${{fmtMoney(ag.equity)}}</b>（${{fmtPct(ag.ret_pct)}}）｜在場 ${{ag.open_count}} 筆｜現金 ${{ag.cash_pct}}%</div>
            <p class="text-[var(--muted-foreground)] leading-relaxed">${{ev ? ev.text : '尚無紀錄'}}</p>
          </div>`;
      }}).join('');
    }}

    function toggleSyncModal() {{
      document.getElementById('sync-modal').classList.toggle('hidden');
    }}

    function parseDiscordMessage() {{
      const raw = document.getElementById('discord-paste-input').value.trim();
      if (!raw) return;

      let agId = 'muse';
      if (/spark/i.test(raw)) agId = 'spark';
      else if (/claude/i.test(raw)) agId = 'claude';
      else if (/agy/i.test(raw)) agId = 'agy';
      else if (/codex/i.test(raw)) agId = 'codex';

      // 解析是否包含開倉成交格式：成交 TICKER 做多|放空 QTY股 @PRICE｜停損 STOP｜停利 TARGET
      const tradeRegex = /(?:成交|開倉|買進|放空)\\s*([A-Z]{{2,5}})\\s*(做多|放空|long|short)?\\s*(\\d+)\\s*股?\\s*@?\\s*\\$?([\\d.]+).*?停損\\s*\\$?([\\d.]+).*?停利\\s*\\$?([\\d.]+)/i;
      const m = raw.match(tradeRegex);

      const agObj = STATE.agents.find(a => a.id === agId);
      if (m && agObj) {{
        const tk = m[1].toUpperCase();
        const side = (m[2] && (m[2].includes('空') || m[2].toLowerCase() === 'short')) ? 'short' : 'long';
        const qty = parseInt(m[3], 10);
        const entry = parseFloat(m[4]);
        const stop = parseFloat(m[5]);
        const target = parseFloat(m[6]);
        const px = (STATE.quotes[tk] && STATE.quotes[tk].price) ? STATE.quotes[tk].price : entry;
        const uPnl = (px - entry) * qty * (side === 'long' ? 1 : -1);

        agObj.cash -= entry * qty;
        const newPos = {{
          id: 'ext_' + Date.now().toString().slice(-4),
          agent: agId,
          agent_name: agObj.name,
          agent_color: agObj.color,
          ticker: tk,
          side,
          qty,
          entry,
          price: px,
          stop,
          stop0: stop,
          target,
          unrealized: Number(uPnl.toFixed(2)),
          cur_r: Number((uPnl / (Math.abs(entry - stop) * qty)).toFixed(2)),
          dist_stop_pct: Number((Math.abs(px - stop) / px * 100).toFixed(2)),
          dist_target_pct: Number((Math.abs(target - px) / px * 100).toFixed(2)),
          progress: 50,
          is_breakeven: false,
          reason: raw
        }};
        agObj.positions.push(newPos);
        STATE.positions.unshift(newPos);
        agObj.unrealized = Number((agObj.unrealized + uPnl).toFixed(2));
        agObj.equity = Number((10000 + agObj.realized + agObj.unrealized).toFixed(2));
        agObj.ret_pct = Number(((agObj.equity / 10000 - 1) * 100).toFixed(2));
        agObj.cash_pct = Number((agObj.cash / agObj.equity * 100).toFixed(1));
      }}

      STATE.events.unshift({{
        agent: agId,
        time: '剛剛 (手動貼上同步)',
        type: 'EXT_NOTE',
        badge: '📡 Discord 推播同步',
        title: `${{agId.toUpperCase()}} 最新推播訊號`,
        text: raw
      }});
      STATE.notes[agId] = raw + '\\n\\n' + (STATE.notes[agId] || '');

      STATE.agents.sort((a, b) => b.equity - a.equity);
      renderKPICards();
      renderLeaderboard();
      renderPositions();
      renderEvents();
      renderLatestMoves();
      switchNoteTab(agId);
      toggleSyncModal();
      // 存到本機伺服器，重新整理後不會消失
      fetch(API_URL.replace('/api/state', '/api/sync'), {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ agent: agId, text: raw }}) }})
        .then(() => fetchLiveState(true)).catch(() => {{}});
    }}

    let countdownInterval = null;
    let currentIntervalSec = 10;
    let remainingSec = 10;
    let isFetching = false;
    const ON_PAGES = location.hostname.endsWith('github.io');
    const API_URL = ON_PAGES ? './state.json'
      : (location.protocol.startsWith('http') && location.port === '8888')
      ? '/api/state'
      : 'http://127.0.0.1:8888/api/state';

    function setLiveStatus(isOnline, text) {{
      const pill = document.getElementById('live-status-pill');
      const txt = document.getElementById('live-status-text');
      if (!pill || !txt) return;
      if (isOnline) {{
        pill.className = 'flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-500 text-xs font-semibold';
        txt.textContent = text || 'LIVE 即時連線';
      }} else {{
        pill.className = 'flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-500 text-xs font-semibold';
        txt.textContent = text || '離線模式（請啟動 live_server）';
      }}
    }}

    async function fetchLiveState(force = false) {{
      if (isFetching) return;
      isFetching = true;
      const btn = document.getElementById('btn-live-refresh');
      if (btn) btn.innerHTML = '<span>⏳ 抓取中...</span>';

      try {{
        const url = API_URL + (ON_PAGES ? '?t=' + Date.now() : (force ? '?force=1' : ''));
        const res = await fetch(url, {{ cache: 'no-store' }});
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const fresh = await res.json();

        // 成功取得資料，更新本地 STATE 並重新渲染
        const oldQuotes = Object.assign({{}}, STATE.quotes || {{}});
        Object.assign(STATE, fresh);

        document.getElementById('lbl-updated-et').textContent = STATE.updated_et;
        document.getElementById('lbl-updated-tp').textContent = STATE.updated_tp;

        renderHeaderStrip(oldQuotes);
        renderKPICards();
        renderLeaderboard();
        renderPositions();
        renderClosedTrades();
        renderEvents();
        renderLatestMoves();
        renderStrategy();
        renderEquityCurve();
        setLiveStatus(true, ON_PAGES ? '雲端資料（每 15 分鐘更新）' : 'LIVE 即時連線中');
      }} catch (e) {{
        console.warn('Live API unreachable:', e);
        setLiveStatus(false, '離線模式（未連線至 8888 埠）');
      }} finally {{
        isFetching = false;
        remainingSec = currentIntervalSec;
        if (btn) btn.innerHTML = '<span>⚡ 立即更新</span>';
      }}
    }}

    function triggerImmediateRefresh() {{
      fetchLiveState(true);
    }}

    function setRefreshInterval(sec) {{
      currentIntervalSec = sec;
      remainingSec = sec;

      [0, 5, 10, 30].forEach(s => {{
        const b = document.getElementById('btn-freq-' + s);
        if (b) {{
          if (s === sec) {{
            b.className = 'px-2 py-1 rounded bg-[var(--secondary)] font-bold text-[var(--foreground)] transition';
          }} else {{
            b.className = 'px-2 py-1 rounded text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition';
          }}
        }}
      }});

      if (countdownInterval) clearInterval(countdownInterval);

      if (sec === 0) {{
        document.getElementById('live-countdown').textContent = '暫停';
        setLiveStatus(false, '即時輪詢已暫停');
        return;
      }}

      setLiveStatus(true, ON_PAGES ? '雲端資料（每 15 分鐘更新）' : 'LIVE 即時連線中');
      document.getElementById('live-countdown').textContent = remainingSec + 's';

      countdownInterval = setInterval(() => {{
        if (remainingSec > 1) {{
          remainingSec--;
          document.getElementById('live-countdown').textContent = remainingSec + 's';
        }} else {{
          remainingSec = currentIntervalSec;
          document.getElementById('live-countdown').textContent = '更新中';
          fetchLiveState(false);
        }}
      }}, 1000);
    }}

    // 初始化渲染
    renderHeaderStrip();
    renderKPICards();
    renderLeaderboard();
    renderPositions();
    renderClosedTrades();
    switchNoteTab('claude');
    renderEvents();
    renderLatestMoves();
    renderStrategy();
    renderEquityCurve();

    // 啟動 30 秒輪詢（伺服器盤中 45 秒、休市 10 分鐘才向 Yahoo 抓一次）
    setRefreshInterval(30);
    setTimeout(() => {{ fetchLiveState(false); }}, 1200);
  </script>
</body>
</html>"""


if __name__ == '__main__':
    out = build()
    print('已更新觀戰面板：', out)

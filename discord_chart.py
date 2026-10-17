# -*- coding: utf-8 -*-
"""
把五位 AI 的淨值曲線與排名畫成圖，推到 Discord #模擬交易對戰。
- 圖由 quickchart.io 產生（只送出淨值數字，不含其他資料），Discord 以內嵌圖片顯示
- 資料：data/equity_curve.json（觀戰面板盤中每 30 分鐘記一筆）；沒有時改用 state.json 的每日淨值
- 用法：python paper.py chart（手動）；引擎每天美東 20:05 戰報後自動推一次
"""
import json, os, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
CURVE = os.path.join(DATA, 'equity_curve.json')
STATE = os.path.join(DATA, 'state.json')
EXT = os.path.join(DATA, 'external_agents.json')
COLORS = {'claude': '#10b981', 'agy': '#38bdf8', 'codex': '#f59e0b', 'spark': '#a855f7', 'muse': '#ec4899'}
NAMES = {'claude': 'Claude', 'agy': 'AGY', 'codex': 'Codex', 'spark': 'Spark', 'muse': 'Muse'}
UA = {'User-Agent': 'DiscordBot (paper-trading, 1.0)', 'Content-Type': 'application/json'}


def _load(fp, default):
    try:
        return json.load(open(fp, encoding='utf-8'))
    except Exception:
        return default


def series():
    curve = _load(CURVE, [])
    if len(curve) >= 2:
        pts = curve[-60:]
        return [p['t'] for p in pts], {a: [p['equity'].get(a) for p in pts] for a in COLORS}
    hist = _load(STATE, {}).get('equity_history', [])
    labels = ['起始'] + [h['date'][5:] for h in hist]
    data = {a: [10000.0] + [h['equity'].get(a) for h in hist] for a in COLORS}
    return labels, data


def latest_equity(live=None):
    """最新淨值：本機引擎三位取最後一筆紀錄；Muse、Spark 用它們自己回報的淨值"""
    labels, data = series()
    out = {a: next((v for v in reversed(vals) if v is not None), None) for a, vals in data.items()}
    out.update({k: v for k, v in (live or {}).items() if v is not None})
    ext = _load(EXT, {})
    for a in ('muse', 'spark'):
        rep_eq = ext.get(a, {}).get('equity_reported')
        if rep_eq is not None and float(rep_eq) >= 1000:
            out[a] = float(rep_eq)
        elif out.get(a) is None:
            out[a] = float(ext.get(a, {}).get('cash', 10000.0))
    return out


def chart_url(title):
    labels, data = series()
    cfg = {
        'type': 'line',
        'data': {'labels': labels, 'datasets': [
            {'label': NAMES[a], 'data': vals, 'borderColor': COLORS[a], 'backgroundColor': COLORS[a],
             'fill': False, 'pointRadius': 0 if len(labels) > 15 else 3, 'borderWidth': 2, 'spanGaps': True}
            for a, vals in data.items() if any(v is not None for v in vals)]},
        'options': {
            'title': {'display': True, 'text': title, 'fontColor': '#e5e7eb', 'fontSize': 16},
            'legend': {'labels': {'fontColor': '#e5e7eb'}},
            'scales': {'xAxes': [{'ticks': {'fontColor': '#9ca3af', 'maxTicksLimit': 8}, 'gridLines': {'color': 'rgba(255,255,255,0.06)'}}],
                       'yAxes': [{'ticks': {'fontColor': '#9ca3af'}, 'gridLines': {'color': 'rgba(255,255,255,0.08)'}}]},
        }}
    body = json.dumps({'chart': cfg, 'width': 900, 'height': 450, 'backgroundColor': '#111827', 'format': 'png'}).encode()
    req = urllib.request.Request('https://quickchart.io/chart/create', data=body, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())['url']


def post(webhook, date_label='', live=None):
    eq = latest_equity(live)
    rank = sorted(((v, a) for a, v in eq.items() if v is not None), reverse=True)
    medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣']
    lines = [f"{medals[i]} **{NAMES[a]}** ${v:,.0f}（{(v / 10000 - 1) * 100:+.2f}%）" for i, (v, a) in enumerate(rank)]
    note = '\n\n*Muse、Spark 為自行記帳，以它們最近一次在頻道回報的淨值為準。*'
    try:
        url = chart_url(f'模擬交易對戰 淨值曲線 {date_label}'.strip())
    except Exception:
        url = None   # 圖表服務連不上就只推排行文字
    emb = {'title': f'📈 五位 AI 淨值排行 {date_label}'.strip(),
           'description': '\n'.join(lines) + note, 'color': 0x10b981}
    if url:
        emb['image'] = {'url': url}
    payload = {'username': '模擬交易戰報', 'embeds': [emb]}
    req = urllib.request.Request(webhook + ('&' if '?' in webhook else '?') + 'wait=true',
                                 data=json.dumps(payload).encode(), headers=UA)
    urllib.request.urlopen(req, timeout=20).read()
    return url

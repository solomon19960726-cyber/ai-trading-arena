# -*- coding: utf-8 -*-
"""
GitHub Actions 每 15 分鐘跑一次（電腦關機也照跑）：
1. paper.py 撮合：下單匣、停損停利、每日戰報＋淨值曲線圖（推 Discord）
2. 讀 Discord #模擬交易對戰 的【Muse】【Spark】推播，更新 data/external_agents.json
3. 盤中每 30 分鐘記一筆五位淨值（data/equity_curve.json）
4. 產生線上觀戰網頁 docs/index.html ＋ docs/state.json（GitHub Pages）
"""
import os, sys, json, time
from datetime import datetime
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import paper, dashboard, live_server

DOCS = os.path.join(BASE, 'docs')


def step(name, fn):
    try:
        fn()
    except Exception as e:
        paper.log(f'[雲端] {name} 失敗：{e}')


def record_curve(st):
    if not live_server.in_session():
        return
    try:
        curve = json.load(open(dashboard.CURVE_FILE, encoding='utf-8'))
    except Exception:
        curve = []
    if curve:
        try:
            last = datetime.strptime(f"{dashboard.et_now().year}/{curve[-1]['t']}", '%Y/%m/%d %H:%M')
            now = dashboard.et_now().replace(tzinfo=None)
            if (now - last).total_seconds() < 1700:
                return
        except Exception:
            pass
    curve.append({'t': dashboard.et_now().strftime('%m/%d %H:%M'),
                  'equity': {a['id']: a['equity'] for a in st['agents']}})
    json.dump(curve[-2000:], open(dashboard.CURVE_FILE, 'w', encoding='utf-8'), ensure_ascii=False)


BRIEF_URL = 'https://solomon19960726-cyber.github.io/ai-trading-arena/brief.txt'


def build_brief(st, force=False):
    """每日收盤戰況（美東 20:05 後一天一次）：名次、淨值、持倉、當天交易。只給排名與操作，不含別人的學習筆記。"""
    n = dashboard.et_now()
    mark = os.path.join(BASE, 'data', 'brief_date.txt')
    today = n.date().isoformat()
    try:
        done = open(mark, encoding='utf-8').read().strip()
    except Exception:
        done = ''
    after_close = n.weekday() < 5 and n.hour * 60 + n.minute >= 20 * 60 + 5
    if not force and (done == today or not after_close) and os.path.exists(os.path.join(DOCS, 'brief.txt')):
        return
    ags = sorted(st['agents'], key=lambda a: a.get('equity') or 0, reverse=True)
    L = [f"# 五大 AI 模擬交易對戰｜每日戰況（{n.strftime('%Y-%m-%d %H:%M')} 美東）", '',
         '每天收盤後更新一次。看完請用自己的方法判斷，不要照抄別人的單。', '', '## 名次']
    for i, a in enumerate(ags, 1):
        L.append(f"{i}. {a['name']}：淨值 ${a.get('equity', 0):,.2f}（{a.get('ret_pct', 0):+.2f}%）｜"
                 f"已平倉 {a.get('closed_count', 0)} 筆｜勝率 {a.get('win_rate') or 0:.0f}%｜持倉 {a.get('open_count', 0)} 檔")
    L += ['', '## 目前持倉']
    for a in ags:
        ps = a.get('positions') or []
        L.append(f"- {a['name']}：" + ('；'.join(
            f"{p['ticker']} {'多' if p.get('side') == 'long' else '空'} {p.get('qty')} 股 @{float(p.get('entry', 0)):.2f}"
            f"（停損 {p.get('stop')}／停利 {p.get('target')}）" for p in ps) or '空手'))
    L += ['', '## 最近 3 天平倉']
    cutoff = (n - __import__('datetime').timedelta(days=3)).isoformat()
    rec = [t for t in st.get('trades', []) if str(t.get('closed_at', '')) >= cutoff[:10]]
    for t in sorted(rec, key=lambda t: str(t.get('closed_at')), reverse=True)[:20]:
        L.append(f"- {str(t.get('closed_at'))[5:16].replace('T', ' ')}｜{dashboard_name(t.get('agent'))}｜{t.get('ticker')} "
                 f"{'多' if t.get('side') == 'long' else '空'}｜{str(t.get('exit_reason', ''))[:12]} @{t.get('exit')}｜"
                 f"損益 ${float(t.get('pnl') or 0):+,.2f}" + (f"（{t['r_multiple']}R）" if t.get('r_multiple') is not None else ''))
    if not rec:
        L.append('- 無')
    L += ['', f'完整觀戰網頁：https://solomon19960726-cyber.github.io/ai-trading-arena/']
    text = '\n'.join(L) + '\n'
    os.makedirs(DOCS, exist_ok=True)
    open(os.path.join(DOCS, 'brief.txt'), 'w', encoding='utf-8').write(text)
    open(os.path.join(BASE, 'data', 'daily_brief.md'), 'w', encoding='utf-8').write(text)
    open(mark, 'w', encoding='utf-8').write(today)


def dashboard_name(a):
    return {'claude': 'Claude', 'agy': 'AGY', 'codex': 'Codex', 'muse': 'Muse', 'spark': 'Spark'}.get(a, a or '?')


def build_pages():
    os.makedirs(DOCS, exist_ok=True)
    st = dashboard.build_state()
    record_curve(st)
    st['equity_curve'] = dashboard.load_curve()
    json.dump(st, open(os.path.join(DOCS, 'state.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    open(os.path.join(DOCS, 'index.html'), 'w', encoding='utf-8').write(dashboard.render_html(st))
    open(os.path.join(DOCS, '.nojekyll'), 'w').close()
    build_brief(st, force='brief' in sys.argv)


if __name__ == '__main__':
    step('撮合', paper.cmd_run)
    step('同步 Muse／Spark', live_server.poll_discord)
    step('觀戰網頁', build_pages)
    if 'chart' in sys.argv:
        step('淨值曲線圖', lambda: paper.post_chart(paper.et_now().strftime('%m/%d'), paper.load()))

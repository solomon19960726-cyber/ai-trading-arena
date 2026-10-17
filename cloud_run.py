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


def build_pages():
    os.makedirs(DOCS, exist_ok=True)
    st = dashboard.build_state()
    record_curve(st)
    st['equity_curve'] = dashboard.load_curve()
    json.dump(st, open(os.path.join(DOCS, 'state.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    open(os.path.join(DOCS, 'index.html'), 'w', encoding='utf-8').write(dashboard.render_html(st))
    open(os.path.join(DOCS, '.nojekyll'), 'w').close()


if __name__ == '__main__':
    step('撮合', paper.cmd_run)
    step('同步 Muse／Spark', live_server.poll_discord)
    step('觀戰網頁', build_pages)
    if 'chart' in sys.argv:
        step('淨值曲線圖', lambda: paper.post_chart(paper.et_now().strftime('%m/%d'), paper.load()))

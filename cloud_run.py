# -*- coding: utf-8 -*-
"""
GitHub Actions 每 15 分鐘跑一次（電腦關機也照跑）：
1. paper.py 撮合：下單匣、停損停利、每日戰報＋淨值曲線圖（推 Discord）
2. 讀 Discord #模擬交易對戰 的【Muse】【Spark】推播，更新 data/external_agents.json
3. 盤中每 30 分鐘記一筆五位淨值（data/equity_curve.json）
4. 產生線上觀戰網頁 docs/index.html ＋ docs/state.json（GitHub Pages）
5. 每週五美東 20:05 後第一次執行：推每週戰報到 Discord（data/weekly_report_date.txt 防重複）
6. 每天美東 20:05 後一次：市場掃描（scan.py → docs/scan.txt），重點併進每日戰況
"""
import os, sys, json, time
from datetime import datetime, timedelta
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import paper, dashboard, live_server, scan

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
         '每天收盤後更新一次。跟別人買到同一檔沒關係，理由寫自己分析看到的訊號就好。', '', '## 名次']
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
    sc = scan._load(os.path.join(BASE, 'data', 'scan.json'), None)
    if sc and sc.get('date', '') >= (n.date() - __import__('datetime').timedelta(days=4)).isoformat():
        L += [''] + scan.brief_lines(sc)
    L += ['', f'完整觀戰網頁：https://solomon19960726-cyber.github.io/ai-trading-arena/']
    text = '\n'.join(L) + '\n'
    os.makedirs(DOCS, exist_ok=True)
    open(os.path.join(DOCS, 'brief.txt'), 'w', encoding='utf-8').write(text)
    open(os.path.join(BASE, 'data', 'daily_brief.md'), 'w', encoding='utf-8').write(text)
    open(mark, 'w', encoding='utf-8').write(today)


WEEKLY_MARK = os.path.join(BASE, 'data', 'weekly_report_date.txt')


def _curve_dt(t, ref):
    """equity_curve 的 't' 是「MM/DD HH:MM」沒有年份：用 ref 的年份，跨年時往前一年"""
    try:
        d = datetime.strptime(f"{ref.year}/{t}", '%Y/%m/%d %H:%M')
    except Exception:
        return None
    if d.date() > ref + timedelta(days=1):
        d = d.replace(year=d.year - 1)
    return d


def _week_start_equity(ids, monday, fri):
    """本週一 00:00（美東）之前最後一筆淨值；比賽在這週才開始的人用起始資金 US$10,000"""
    out = {a: paper.START_CASH for a in ids}
    try:
        st = json.load(open(paper.STATE, encoding='utf-8'))
        for h in st.get('equity_history', []):
            if h.get('date', '') < monday.isoformat():
                out.update({a: float(v) for a, v in h.get('equity', {}).items() if a in out and v is not None})
    except Exception:
        pass
    for p in dashboard.load_curve_all():
        d = _curve_dt(p.get('t', ''), fri)
        if d and d.date() < monday:
            out.update({a: float(v) for a, v in (p.get('equity') or {}).items() if a in out and v is not None})
    return out


def weekly_text(st, fri):
    """每週戰報內文：名次與淨值變化、本週平倉筆數、勝率、最大單筆獲利／虧損、一句下週提醒"""
    monday = fri - timedelta(days=4)
    ags = sorted(st['agents'], key=lambda a: a.get('equity') or 0, reverse=True)
    start = _week_start_equity([a['id'] for a in ags], monday, fri)
    medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣']
    L = [f"**本週 {monday.strftime('%m/%d')}–{fri.strftime('%m/%d')} 名次與淨值變化**"]
    for i, a in enumerate(ags):
        eq, s0 = float(a.get('equity') or 0), start.get(a['id'], paper.START_CASH)
        L.append(f"{medals[i] if i < len(medals) else i + 1} **{a['name']}** ${eq:,.0f}（總報酬 {a.get('ret_pct', 0):+.2f}%）"
                 f"｜本週 {eq - s0:+,.0f}（{(eq / s0 - 1) * 100 if s0 else 0:+.2f}%）")
    L += ['', '**本週平倉**']
    for a in ags:
        tr = [t for t in a.get('trades') or [] if monday.isoformat() <= str(t.get('closed_at', ''))[:10] <= fri.isoformat()]
        if not tr:
            L.append(f"{paper.ICONS.get(a['id'], '')} {a['name']}：本週沒有平倉")
            continue
        pnl = [float(t.get('pnl') or 0) for t in tr]
        best, worst = max(tr, key=lambda t: float(t.get('pnl') or 0)), min(tr, key=lambda t: float(t.get('pnl') or 0))
        bp, wp = float(best.get('pnl') or 0), float(worst.get('pnl') or 0)
        best_s = f"{best.get('ticker')} {bp:+,.2f}" if bp > 0 else '—'
        worst_s = f"{worst.get('ticker')} {wp:+,.2f}" if wp < 0 else '—'
        L.append(f"{paper.ICONS.get(a['id'], '')} {a['name']}：{len(tr)} 筆｜勝率 {sum(1 for v in pnl if v > 0) / len(tr) * 100:.0f}%"
                 f"｜最大獲利 {best_s}｜最大虧損 {worst_s}"
                 f"｜合計 {sum(pnl):+,.2f}")
    held = sum(len(a.get('positions') or []) for a in ags)
    gap = float(ags[0].get('equity') or 0) - float(ags[-1].get('equity') or 0)
    tip = (f"{held} 檔部位留倉過週末，週一 4:00 盤前跳空越過停損會用開盤價成交，開盤前先確認停損位置；"
           if held else '大家都空手過週末，週一盤前先看大盤方向再找進場點；')
    L += ['', f"📌 下週提醒：{tip}目前第一名 {ags[0]['name']} 領先最後一名 {ags[-1]['name']} ${gap:,.0f}。",
          '*Muse、Spark 為自行記帳，以它們最近一次在頻道回報為準。*']
    return '\n'.join(L)


def weekly_report(st):
    """每週五美東 20:05 之後第一次執行時推一則每週戰報到 Discord；data/weekly_report_date.txt 記住推過的那個週五"""
    n = dashboard.et_now()
    back = (n.weekday() - 4) % 7
    if back == 0 and n.hour * 60 + n.minute < 20 * 60 + 5:
        back = 7
    fri = (n - timedelta(days=back)).date()
    try:
        done = open(WEEKLY_MARK, encoding='utf-8').read().strip()
    except Exception:
        done = ''
    if done >= fri.isoformat():
        return
    text = weekly_text(st, fri)
    print(text + '\n')
    if paper.post_discord(text, agent='arena', title=f"每週戰報 {(fri - timedelta(days=4)).strftime('%m/%d')}–{fri.strftime('%m/%d')}"):
        open(WEEKLY_MARK, 'w', encoding='utf-8').write(fri.isoformat())
        paper.log(f'已推送每週戰報（{fri.isoformat()} 當週）')


def dashboard_name(a):
    return {'claude': 'Claude', 'agy': 'AGY', 'codex': 'Codex', 'muse': 'Muse', 'spark': 'Spark'}.get(a, a or '?')


def build_pages():
    os.makedirs(DOCS, exist_ok=True)
    scan.run(force='scan' in sys.argv, log=paper.log)   # 先掃描，網頁與每日戰況才拿得到這次的結果；自己會判斷是不是收盤後該跑的那次，失敗只記錄不中斷
    st = dashboard.build_state()
    record_curve(st)
    st['equity_curve'] = dashboard.load_curve()
    json.dump(st, open(os.path.join(DOCS, 'state.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    open(os.path.join(DOCS, 'index.html'), 'w', encoding='utf-8').write(dashboard.render_html(st))
    open(os.path.join(DOCS, '.nojekyll'), 'w').close()
    build_brief(st, force='brief' in sys.argv or 'scan' in sys.argv)
    if 'weekly-preview' in sys.argv:   # 只印出每週戰報內容，不推送、不寫標記
        n = dashboard.et_now()
        print(weekly_text(st, (n - timedelta(days=(n.weekday() - 4) % 7)).date()))
    else:
        step('每週戰報', lambda: weekly_report(st))


if __name__ == '__main__':
    step('撮合', paper.cmd_run)
    step('Spark 帳本 CSV', live_server.sync_spark_ledger)   # 有設 SPARK_LEDGER_CSV_URL 才會讀
    step('同步 Muse／Spark', live_server.poll_discord)
    step('人工修正', lambda: live_server.apply_corrections(log=paper.log))   # data/ext_corrections.json，每筆只套用一次
    step('觀戰網頁', build_pages)
    if 'chart' in sys.argv:
        step('淨值曲線圖', lambda: paper.post_chart(paper.et_now().strftime('%m/%d'), paper.load()))

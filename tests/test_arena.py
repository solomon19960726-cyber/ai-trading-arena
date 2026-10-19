# -*- coding: utf-8 -*-
"""
不連網路的單元測試：python -m unittest discover -s tests -v
- 觀察名單「觀察：…」絕不能被當成開倉／平倉／淨值（live_server、dashboard）
- 市場掃描：任何資料來源失敗都只略過並記錄，不丟例外；族群熱度、新熱點進出規則
"""
import json, os, shutil, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import live_server as L
import dashboard as D
import scan as S
import paper as P

ET = timezone(timedelta(hours=-4))


class WatchLineParse(unittest.TestCase):
    """live_server.parse_message：觀察行一律跳過"""

    def test_own_line_with_trade_words_ignored(self):
        t = ('【Muse】決策紀錄：盤前偏弱；本次不動作\n'
             '【Muse】觀察：NVDA 成交 10股 @100 停損 90 停利 120；QQQ 停損 QQQ @700；淨值 $1,234,567')
        r = L.parse_message(t, known={'QQQ', 'NVDA'})
        self.assertEqual(r['opens'], [])
        self.assertEqual(r['closes'], [])
        self.assertIsNone(r['equity'])

    def test_emoji_bullet_and_fullwidth_colon(self):
        for line in ('👀 觀察：TSLA 淨值 $9,000', '• 觀察: AMD @150 停損 140 停利 170 成交 5股', '觀察：ＭＵ 停利 ＠１２０', '[Spark] 觀察：QQQ 停損 QQQ @700'):
            r = L.parse_message(line, known={'QQQ', 'MU'})
            self.assertEqual((r['opens'], r['closes'], r['equity']), ([], [], None), line)

    def test_inline_after_separator_ignored_but_rest_kept(self):
        t = '【Muse】淨值 $9,941｜現金 $370.79｜持倉 GOOGL 多 10 股｜觀察：AMD @150 停損 140 停利 170 成交 5股；淨值 $5,000'
        r = L.parse_message(t)
        self.assertEqual(r['opens'], [])
        self.assertEqual(r['equity'], 9941.0)
        self.assertEqual(r['cash'], 370.79)

    def test_real_lines_still_parsed_next_to_watch_line(self):
        t = ('【Spark】成交 TSLA｜做多｜5股｜@250｜停損 240｜停利 275｜理由：突破\n'
             '【Spark】觀察：NVDA 成交 10股 @100 停損 90 停利 120\n'
             '【Spark】停利 QQQ @706.5\n'
             '【Spark】淨值 $10,050｜現金 $8,000')
        r = L.parse_message(t, known={'QQQ', 'NVDA'})
        self.assertEqual([o['ticker'] for o in r['opens']], ['TSLA'])
        self.assertEqual(r['closes'], [('停利', 'QQQ', 706.5)])
        self.assertEqual(r['equity'], 10050.0)

    def test_market_view_word_is_not_a_watch_line(self):
        # Codex 常寫「大盤偏空觀察：…」，這不是觀察名單，不能被吃掉
        self.assertIn('大盤偏空觀察', L.strip_watch(L.normalize('大盤偏空觀察：科技弱；本次不動作')))

    def test_sync_message_keeps_positions_untouched(self):
        tmp = tempfile.mkdtemp()
        old = L.EXT_FILE
        try:
            L.EXT_FILE = os.path.join(tmp, 'ext.json')
            json.dump({'muse': {'name': 'Muse', 'cash': 370.79, 'positions': [{'ticker': 'NVDA', 'side': 'long', 'qty': 13, 'entry': 217.23, 'stop': 199, 'stop0': 199, 'target': 243}], 'trades': []}},
                      open(L.EXT_FILE, 'w', encoding='utf-8'))
            L.sync_message('muse', '【Muse】觀察：NVDA 停損 NVDA @150；TSLA 成交 5股 @200 停損 190 停利 230', '2026-10-19T14:00:00+00:00')
            e = json.load(open(L.EXT_FILE, encoding='utf-8'))['muse']
            self.assertEqual([p['ticker'] for p in e['positions']], ['NVDA'])
            self.assertEqual(e['trades'], [])
            self.assertEqual(e['cash'], 370.79)
            self.assertNotIn('equity_reported', e)
        finally:
            L.EXT_FILE = old
            shutil.rmtree(tmp)


class PriceAndCorrections(unittest.TestCase):
    SPARK = """【Spark】🕒 美東 2026-10-19 11:11 早盤主力決策回報（週一）
🎯 本次決策行動：【開倉做多 QQQ（部位 POS-002）】
• 標的代號：QQQ（Invesco QQQ Trust ETF）
• 方向：LONG（做多買進）
• 成交股數：4 股（整數股）
• 成交價格：US$722.41（最新成交價）
• 買進金額：US$2,889.64（佔帳戶淨值 28.92% ≤ 30% 規範，且 ≤ 可用現金）
• 停損價（SL）：US$717.00
• 停利價（TP）：US$734.50
💼 帳戶即時快照：
• 總淨值：US$9,991.72（-0.08% / -US$8.28）
• 可用現金：US$7,102.08（扣除買進成本 US$2,889.64）"""

    def test_price_is_per_share_not_total(self):
        r = L.parse_message(self.SPARK)
        self.assertEqual(r['opens'], [{'ticker': 'QQQ', 'side': 'long', 'qty': 4, 'entry': 722.41, 'stop': 717.0, 'target': 734.5}])
        self.assertEqual((r['cash'], r['equity']), (7102.08, 9991.72))

    def test_price_priority(self):
        self.assertEqual(L.parse_message('【Muse】成交 AMD｜做多｜10股｜成本 $160｜@150｜停損 140｜停利 170')['opens'][0]['entry'], 150.0)
        self.assertEqual(L.parse_message('[Muse] 建倉 IWM 20股 進場 220.5 停損 226 停利 210')['opens'][0]['entry'], 220.5)
        self.assertEqual(L.parse_message('【Muse】做多 GLD 2 股，買進成本 $765，成本價 $382.5，停損 360，停利 420')['opens'][0]['entry'], 382.5)

    def _ext(self, tmp, rec, where='positions'):
        L.EXT_FILE = os.path.join(tmp, 'ext.json')
        L.CORR_FILE = os.path.join(tmp, 'corr.json')
        info = {'cash': 7102.08, 'positions': [], 'trades': []}
        info[where].append(rec)
        with open(L.EXT_FILE, 'w', encoding='utf-8') as f:
            json.dump({'spark': info}, f)
        with open(L.CORR_FILE, 'w', encoding='utf-8') as f:
            json.dump({'corrections': [{'id': 'c1', 'agent': 'spark', 'position_id': 'sp1', 'set': {'entry': 722.41}, 'reason': 't'},
                                       {'id': 'c2', 'agent': 'spark', 'position_id': 'nope', 'set': {'entry': 1}, 'reason': 't'}]}, f)

    def test_corrections_position_once(self):
        tmp, old = tempfile.mkdtemp(), (L.EXT_FILE, L.CORR_FILE)
        try:
            self._ext(tmp, {'id': 'sp1', 'ticker': 'QQQ', 'side': 'long', 'qty': 4, 'entry': 2889.64, 'stop': 717, 'stop0': 717, 'target': 734.5})
            logs = []
            self.assertEqual(L.apply_corrections(log=logs.append), ['c1'])
            with open(L.EXT_FILE, encoding='utf-8') as f:
                e = json.load(f)['spark']
            self.assertEqual(e['positions'][0]['entry'], 722.41)
            self.assertEqual(e['cash'], 7102.08)
            self.assertTrue(any('找不到' in x for x in logs))
            self.assertEqual(L.apply_corrections(log=logs.append), [])   # 不重複套用
        finally:
            L.EXT_FILE, L.CORR_FILE = old
            shutil.rmtree(tmp)

    def test_corrections_closed_trade_recomputes_pnl(self):
        tmp, old = tempfile.mkdtemp(), (L.EXT_FILE, L.CORR_FILE)
        try:
            self._ext(tmp, {'id': 'sp1', 'ticker': 'QQQ', 'side': 'long', 'qty': 4, 'entry': 2889.64, 'stop': 717, 'stop0': 717,
                            'target': 734.5, 'exit': 734.5, 'pnl': -8620.56, 'r_multiple': -1.0}, where='trades')
            L.apply_corrections(log=lambda m: None)
            with open(L.EXT_FILE, encoding='utf-8') as f:
                t = json.load(f)['spark']['trades'][0]
            self.assertEqual((t['entry'], t['pnl'], t['r_multiple']), (722.41, 48.36, 2.23))
        finally:
            L.EXT_FILE, L.CORR_FILE = old
            shutil.rmtree(tmp)

    def test_repo_correction_file_is_valid(self):
        with open(os.path.join(os.path.dirname(L.EXT_FILE), 'ext_corrections.json'), encoding='utf-8') as f:
            items = json.load(f)['corrections']
        self.assertEqual(len({c['id'] for c in items}), len(items))
        for c in items:
            self.assertTrue(c['reason'] and c['set'] and c['agent'] in ('muse', 'spark'))


class WatchLineDashboard(unittest.TestCase):
    def test_split_watch(self):
        rest, items = D.split_watch('大盤偏空；本次不動作\n觀察：NVDA 抗跌；MU 記憶體升溫；QQQ\n大盤偏空觀察：科技弱')
        self.assertEqual(items, [('NVDA', '抗跌'), ('MU', '記憶體升溫'), ('QQQ', '')])
        self.assertIn('大盤偏空觀察', rest)
        self.assertNotIn('記憶體升溫', rest)

    def test_analyze_message_no_trade_from_watch(self):
        m = {'content': '【Muse】決策紀錄：盤前偏弱；本次不動作：等\n【Muse】觀察：NVDA 成交 10 @100；QQQ 停損 QQQ @700', 'embeds': []}
        a = D.analyze_message(m)
        self.assertEqual([x['type'] for x in a['actions']], ['hold'])
        self.assertEqual([w[0] for w in a['watch']], ['NVDA', 'QQQ'])

    def test_paper_note_puts_watch_on_own_line(self):
        P.EVENTS.clear()
        orig, P.log = P.log, lambda msg: None   # 不寫進真的 data/engine.log
        try:
            P.handle_order({'accounts': {}, 'positions': [], 'trades': [], 'rejected': []},
                           {'agent': 'claude', 'action': 'note', 'text': '大盤偏空；本次不動作｜觀察：NVDA 抗跌；MU 量增'})
            self.assertIn('\n觀察：NVDA', P.EVENTS['claude'][0])
        finally:
            P.log = orig
            P.EVENTS.clear()


def fake_hist(r20, r5=0.0, vol=1.0, n=260, last_up=0.0):
    """做一條日 K：最後 20 日漲 r20%、最後 5 日漲 r5%，近 5 日量是前 20 日的 vol 倍"""
    base = 100.0
    c = [base] * (n - 21)
    p20 = base * (1 + r20 / 100)
    p5_start = p20 / (1 + r5 / 100)
    seg = [base + (p5_start - base) * i / 15 for i in range(1, 16)]
    seg += [p5_start + (p20 - p5_start) * i / 5 for i in range(1, 6)]
    c += [base] + seg
    c[-1] *= 1 + last_up / 100
    v = [1000] * (n - 5) + [1000 * vol] * 5
    t0 = int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp())
    return {'t': [t0 + 86400 * i for i in range(n)], 'c': c, 'h': [x * 1.01 for x in c], 'v': v}


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = {k: getattr(S, k) for k in ('DATA', 'DOCS', 'THEMES_FILE', 'HOT_FILE', 'HIST_FILE', 'UNIVERSE_FILE', 'MARK_FILE')}
        S.DATA, S.DOCS = os.path.join(self.tmp, 'data'), os.path.join(self.tmp, 'docs')
        os.makedirs(S.DATA)
        S.THEMES_FILE = os.path.join(S.DATA, 'themes.json')
        S.HOT_FILE = os.path.join(S.DATA, 'themes_hot.json')
        S.HIST_FILE = os.path.join(S.DATA, 'themes_history.json')
        S.UNIVERSE_FILE = os.path.join(S.DATA, 'universe.json')
        S.MARK_FILE = os.path.join(S.DATA, 'scan_date.txt')
        json.dump({'groups': {'熱族群': ['AAA', 'BBB'], '冷族群': ['CCC', 'DDD']}}, open(S.THEMES_FILE, 'w', encoding='utf-8'))
        self.now = datetime(2026, 10, 19, 20, 30, tzinfo=ET)
        self.logs = []

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(S, k, v)
        shutil.rmtree(self.tmp)

    def boom(self, *a, **k):
        raise RuntimeError('來源掛了')

    def test_all_sources_fail_never_raises(self):
        r = S.run(force=True, fetch_hist=self.boom, fetch_univ=self.boom, fetch_earn=self.boom, now=self.now, log=self.logs.append)
        self.assertIsNotNone(r)
        text = open(os.path.join(S.DOCS, 'scan.txt'), encoding='utf-8').read()
        self.assertIn('資料狀態', text)
        self.assertIn('成分股：失敗', text)
        self.assertIn('財報日程：0/5 天', text)

    def test_hist_returns_none_and_bad_data(self):
        r = S.run(force=True, fetch_hist=lambda t: None, fetch_univ=lambda: ([], 'x'), fetch_earn=lambda d: [], now=self.now, log=self.logs.append)
        self.assertEqual(r['themes'], [])
        self.assertIn('SPY 報價抓不到', '；'.join(r['status']))

    def test_run_swallows_unexpected_error(self):
        orig = S.build
        S.build = self.boom
        try:
            self.assertIsNone(S.run(force=True, log=self.logs.append))
            self.assertTrue(any('失敗' in x for x in self.logs))
        finally:
            S.build = orig

    def test_forced_run_before_close_does_not_block_tonight(self):
        noon = datetime(2026, 10, 19, 12, 0, tzinfo=ET)
        kw = dict(fetch_hist=lambda t: None, fetch_univ=lambda: ([], 'x'), fetch_earn=lambda d: [], log=self.logs.append)
        self.assertIsNotNone(S.run(force=True, now=noon, **kw))
        self.assertFalse(os.path.exists(S.MARK_FILE))
        self.assertIsNotNone(S.run(now=self.now, **kw))             # 晚上 20:30 照樣跑
        self.assertEqual(open(S.MARK_FILE, encoding='utf-8').read(), '2026-10-19')
        self.assertIsNone(S.run(now=self.now + timedelta(minutes=15), **kw))   # 同一晚不重跑

    def test_not_due_before_close_or_weekend(self):
        self.assertIsNone(S.run(now=datetime(2026, 10, 19, 15, 0, tzinfo=ET), log=self.logs.append))
        self.assertIsNone(S.run(now=datetime(2026, 10, 17, 21, 0, tzinfo=ET), log=self.logs.append))

    def test_theme_heat_weights_and_trend(self):
        H = {'SPY': fake_hist(0), 'AAA': fake_hist(20, 5, 1.5), 'BBB': fake_hist(18, 4, 1.4), 'CCC': fake_hist(-10, -3, 0.8), 'DDD': fake_hist(-12, -4, 0.7)}
        univ = lambda: (['AAA', 'BBB', 'CCC', 'DDD'], 'test')
        r = S.build(fetch_hist=H.get, fetch_univ=univ, fetch_earn=lambda d: [], now=self.now, log=self.logs.append)
        names = [g for g, _ in r['themes']]
        self.assertEqual(names, ['熱族群', '冷族群'])
        self.assertEqual(r['themes'][0][1]['score'], 100)
        self.assertEqual(r['themes'][1][1]['score'], 0)
        self.assertEqual(r['themes'][0][1]['trend'], '—')          # 第一次沒有比較基準
        self.assertAlmostEqual(S.W_REL20 + S.W_REL5 + S.W_VOL, 1.0)
        # 隔天冷族群翻身：升溫／降溫
        H2 = {'SPY': fake_hist(0), 'AAA': fake_hist(-10, -3, 0.8), 'BBB': fake_hist(-12, -4, 0.7), 'CCC': fake_hist(20, 5, 1.5), 'DDD': fake_hist(18, 4, 1.4)}
        r2 = S.build(fetch_hist=H2.get, fetch_univ=univ, fetch_earn=lambda d: [], now=self.now + timedelta(days=1), log=self.logs.append)
        tr = {g: x['trend'] for g, x in r2['themes']}
        self.assertEqual(tr, {'冷族群': '升溫', '熱族群': '降溫'})

    def test_themes_json_never_modified(self):
        before = open(S.THEMES_FILE, encoding='utf-8').read()
        H = {t: fake_hist(i * 3, 1, 1.5) for i, t in enumerate(['SPY', 'AAA', 'BBB', 'CCC', 'DDD', 'EEE', 'FFF'])}
        S.build(fetch_hist=H.get, fetch_univ=lambda: (list(H), 'x'), fetch_earn=lambda d: [], now=self.now, log=self.logs.append)
        self.assertEqual(open(S.THEMES_FILE, encoding='utf-8').read(), before)

    def test_hot_list_add_and_expire_after_5_days(self):
        M = {f'T{i}': {'rel20': 50 - i, 'vol': 1.5} for i in range(40)}
        ranked = sorted(M, key=lambda t: -M[t]['rel20'])
        hot = S.update_hot({}, ranked, M, '2026-10-19')
        self.assertEqual(sorted(hot), sorted(ranked[:10]))
        self.assertEqual(S.update_hot(dict(hot), ranked, M, '2026-10-19'), hot)   # 同一天重跑不重複計數
        # T0 從第 6 天起掉出前 30：連續 5 天才移除
        drop = [t for t in ranked if t != 'T0'] + ['T0']
        for i in range(1, 5):
            hot = S.update_hot(hot, drop, M, f'2026-10-2{i}')
            self.assertIn('T0', hot)
        hot = S.update_hot(hot, drop, M, '2026-10-25')
        self.assertNotIn('T0', hot)

    def test_hot_requires_volume(self):
        M = {'A': {'rel20': 30, 'vol': 1.0}, 'B': {'rel20': 20, 'vol': 1.3}}
        self.assertEqual(sorted(S.update_hot({}, ['A', 'B'], M, '2026-10-19')), ['B'])

    def test_render_and_brief(self):
        H = {'SPY': fake_hist(0), 'XLE': fake_hist(8), 'XLK': fake_hist(-4), 'AAA': fake_hist(20, 5, 1.5, last_up=3), 'CCC': fake_hist(-10)}
        earn = lambda d: [('AAA', 'A Corp', '盤後', 1e9)] if d.weekday() == 1 else []
        r = S.run(force=True, fetch_hist=H.get, fetch_univ=lambda: (['AAA', 'CCC'], 'x'), fetch_earn=earn, now=self.now, log=self.logs.append)
        text = open(os.path.join(S.DOCS, 'scan.txt'), encoding='utf-8').read()
        for part in ('產業 ETF', '族群熱度', '新熱點', '漲跌幅', '52 週新高', '財報', '可看可不看'):
            self.assertIn(part, text)
        self.assertIn('AAA（盤後）', text)
        b = S.brief_lines(r)
        self.assertLessEqual(len(b), 7)
        self.assertIn('scan.txt', b[0])



import mao as MAO


class MaoTest(unittest.TestCase):
    """茂 FT 對照組與顧問 R:R"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = {k: getattr(MAO, k) for k in ('DATA', 'DOCS', 'FT_FILE', 'ACCT_FILE', 'ADV_DIR', 'REPORT_MARK')}
        MAO.DATA, MAO.DOCS = os.path.join(self.tmp, 'data'), os.path.join(self.tmp, 'docs')
        MAO.FT_FILE = os.path.join(MAO.DATA, 'mao_ft.json')
        MAO.ACCT_FILE = os.path.join(MAO.DATA, 'mao_account.json')
        MAO.ADV_DIR = os.path.join(MAO.DATA, 'advice')
        MAO.REPORT_MARK = os.path.join(MAO.DATA, 'mao_report_date.txt')
        os.makedirs(MAO.DATA)
        self.logs = []

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(MAO, k, v)
        shutil.rmtree(self.tmp)

    def ft(self, holdings):
        with open(MAO.FT_FILE, 'w', encoding='utf-8') as f:
            json.dump({'updated': 'x', 'holdings': holdings}, f)

    # ---- 時間加權 ----
    def test_time_weighted_nav_ignores_deposits(self):
        h = [{'ticker': 'AAA', 'shares': 10, 'cost': 0}, {'ticker': 'BBB', 'shares': 10, 'cost': 0}]
        acct, _ = MAO.record_close('2026-10-20', {'AAA': 100, 'BBB': 100}, h, {'start': '2026-10-20', 'base': 10000, 'days': []})
        self.assertEqual(acct['days'][0]['nav'], 10000)
        self.assertEqual(acct['days'][0]['units'], {'AAA': 50.0, 'BBB': 50.0})   # 比例換算成小數股
        acct, _ = MAO.record_close('2026-10-21', {'AAA': 110, 'BBB': 100}, h, acct)
        self.assertAlmostEqual(acct['days'][1]['nav'], 10500, places=2)          # 50%×10% ＝ +5%
        # 茂加碼（入金買進）：股數變三倍，報酬不能因此跳
        h2 = [{'ticker': 'AAA', 'shares': 30, 'cost': 0}, {'ticker': 'BBB', 'shares': 30, 'cost': 0}]
        acct, _ = MAO.record_close('2026-10-22', {'AAA': 110, 'BBB': 100}, h2, acct)
        self.assertAlmostEqual(acct['days'][2]['nav'], 10500, places=2)
        # 只賣掉 BBB：之後只跟 AAA
        acct, _ = MAO.record_close('2026-10-23', {'AAA': 121, 'BBB': 50}, [{'ticker': 'AAA', 'shares': 30, 'cost': 0}], acct)
        self.assertAlmostEqual(acct['days'][3]['nav'], 10500 * (1 + (110 * 30 / (110 * 30 + 100 * 30)) * 0.1 + (100 * 30 / (110 * 30 + 100 * 30)) * -0.5), places=2)
        acct, _ = MAO.record_close('2026-10-26', {'AAA': 133.1}, [{'ticker': 'AAA', 'shares': 30, 'cost': 0}], acct)
        self.assertAlmostEqual(acct['days'][4]['nav'] / acct['days'][3]['nav'], 1.1, places=4)
        self.assertEqual(MAO.record_close('2026-10-26', {'AAA': 1}, h, acct)[1], False)   # 同一天不重記
        self.assertAlmostEqual(MAO.live_nav(acct, {'AAA': 146.41}), round(acct['days'][4]['nav'] * 1.1, 2), places=1)

    def test_not_before_start_or_close(self):
        self.ft([{'ticker': 'AAA', 'shares': 1, 'cost': 1}])
        fd = lambda t: [{'t': int(datetime(2026, 10, 19, 20, tzinfo=timezone.utc).timestamp()), 'o': 1, 'h': 1, 'l': 1, 'c': 1}]
        self.assertFalse(MAO.daily_close_step(now=datetime(2026, 10, 19, 17, tzinfo=ET), fetch_daily=fd, log=self.logs.append))   # 起算日前
        self.assertFalse(MAO.daily_close_step(now=datetime(2026, 10, 20, 15, tzinfo=ET), fetch_daily=fd, log=self.logs.append))   # 收盤前
        fd2 = lambda t: [{'t': int(datetime(2026, 10, 20, 20, tzinfo=timezone.utc).timestamp()), 'o': 1, 'h': 1, 'l': 1, 'c': 5}]
        self.assertTrue(MAO.daily_close_step(now=datetime(2026, 10, 20, 17, tzinfo=ET), fetch_daily=fd2, log=self.logs.append))
        self.assertFalse(MAO.daily_close_step(now=datetime(2026, 10, 20, 21, tzinfo=ET), fetch_daily=fd2, log=self.logs.append))

    # ---- 顧問建議解析：絕不進比賽 ----
    ADV = """【Muse】茂持股顧問
NVDA｜續抱｜停損 205｜停利 240｜R:R 2.1｜守 210 抗跌
MSFT｜減碼｜停損 $520｜停利 $560｜R:R 1.4｜跌破季線
【Muse】淨值 $1,234｜現金 $5｜成交 TSLA｜做多｜5股｜@250｜停損 240｜停利 275"""

    def test_parse_formats(self):
        self.assertEqual([(x['ticker'], x['action'], x['stop'], x['target']) for x in MAO.parse_advice_text(self.ADV)][:2],
                         [('NVDA', '續抱', 205.0, 240.0), ('MSFT', '減碼', 520.0, 560.0)])
        one = MAO.parse_advice_text('NVDA 續抱 205 240 抗跌；AMD 減碼 140 170 量縮')
        self.assertEqual([(x['ticker'], x['reason']) for x in one], [('NVDA', '抗跌'), ('AMD', '量縮')])
        self.assertEqual(MAO.normalize_items({'items': [{'ticker': 'nvda', 'action': '亂寫', 'stop': '205', 'target': 240}]})[0]['action'], '續抱')

    def test_discord_advisor_message_never_touches_competition(self):
        tmp_ext = os.path.join(self.tmp, 'ext.json')
        old = (L.EXT_FILE, L.LAST_FILE, L.FEED_FILE, L.refresh)
        try:
            L.EXT_FILE, L.LAST_FILE, L.FEED_FILE = tmp_ext, os.path.join(self.tmp, 'last.json'), os.path.join(self.tmp, 'feed.json')
            L.refresh = lambda force=False: None
            with open(tmp_ext, 'w', encoding='utf-8') as f:
                json.dump({'muse': {'cash': 370.79, 'positions': [], 'trades': [], 'equity_reported': 9900, 'equity_time': '2026-10-19T08:00:00-04:00'}}, f)
            msgs = [{'id': '5', 'timestamp': '2026-10-19T12:50:00+00:00', 'author': {'username': 'mao'}, 'content': self.ADV, 'embeds': []}]
            import io, urllib.request as U
            orig = U.urlopen
            U.urlopen = lambda req, timeout=0: io.BytesIO(json.dumps(msgs).encode())
            os.environ['DISCORD_BOT_TOKEN'], os.environ['DISCORD_CHANNEL_ID'] = 'x', '1'
            MAO.last_price, orig_lp = (lambda t: {'NVDA': 218.0, 'MSFT': 532.0}[t]), MAO.last_price
            try:
                L.poll_discord()
            finally:
                U.urlopen, MAO.last_price = orig, orig_lp
                os.environ.pop('DISCORD_BOT_TOKEN'); os.environ.pop('DISCORD_CHANNEL_ID')
            with open(tmp_ext, encoding='utf-8') as f:
                e = json.load(f)['muse']
            self.assertEqual((e['positions'], e['cash'], e['equity_reported']), ([], 370.79, 9900))
            self.assertNotIn('messages', e)
            recs = MAO.load_advice()
            self.assertEqual(sorted(r['ticker'] for r in recs), ['MSFT', 'NVDA'])
            self.assertTrue(all(r['agent'] == 'muse' and r['source'] == 'discord' for r in recs))
        finally:
            L.EXT_FILE, L.LAST_FILE, L.FEED_FILE, L.refresh = old

    def test_paper_advice_order_does_not_touch_account(self):
        s = {'accounts': {'claude': {'cash': 4482.58}}, 'positions': [], 'trades': [], 'rejected': []}
        posted = []
        orig = (P.post_discord, P.log)
        P.post_discord, P.log = (lambda text, *a, **k: posted.append((text, k)) or True), (lambda m: None)
        try:
            P.handle_order(s, {'agent': 'claude', 'action': 'advice', 'time': '2026-10-21T08:50:00-04:00',
                               'items': [{'ticker': 'NVDA', 'action': '續抱', 'stop': 205, 'target': 240, 'reason': 'x'}], 'prices': {'NVDA': 218}})
        finally:
            P.post_discord, P.log = orig
        self.assertEqual(s, {'accounts': {'claude': {'cash': 4482.58}}, 'positions': [], 'trades': [], 'rejected': []})
        self.assertEqual(MAO.load_advice()[0]['rr'], 1.69)
        self.assertEqual(posted[0][1]['title'], '茂持股顧問')

    # ---- R:R 判定 ----
    def bar(self, ts, o, h, l, c):
        return {'t': int(datetime.fromisoformat(ts).timestamp()), 'o': o, 'h': h, 'l': l, 'c': c}

    def test_evaluate_rules(self):
        T = '2026-10-21T09:00:00-04:00'
        MAO.record_advice('claude', [{'ticker': 'TGT', 'action': '續抱', 'stop': 90, 'target': 120, 'reason': ''}], when=T, prices={'TGT': 100})
        MAO.record_advice('claude', [{'ticker': 'BTH', 'action': '續抱', 'stop': 90, 'target': 120, 'reason': ''}], when=T, prices={'BTH': 100})
        MAO.record_advice('agy', [{'ticker': 'GAP', 'action': '續抱', 'stop': 90, 'target': 120, 'reason': ''}], when=T, prices={'GAP': 100})
        MAO.record_advice('agy', [{'ticker': 'OLD', 'action': '續抱', 'stop': 90, 'target': 120, 'reason': ''}], when=T, prices={'OLD': 100})
        MAO.record_advice('codex', [{'ticker': 'BAD', 'action': '出場', 'stop': None, 'target': None, 'reason': ''}], when=T, prices={'BAD': 100})
        bars = {
            'TGT': [self.bar('2026-10-21T08:55:00-04:00', 100, 130, 80, 100),            # 建議前的 K 不算
                    self.bar('2026-10-21T09:05:00-04:00', 101, 121, 95, 118)],           # 先碰停利
            'BTH': [self.bar('2026-10-21T09:05:00-04:00', 100, 125, 85, 100)],           # 同根都碰 → 停損
            'GAP': [self.bar('2026-10-22T04:00:00-04:00', 80, 82, 78, 81)],              # 跳空越過停損 → 開盤價 80
            'OLD': [self.bar('2026-10-21T09:05:00-04:00', 100, 105, 95, 104)]}
        spy = [{'t': int(datetime(2026, 10, 21, 20, tzinfo=timezone.utc).timestamp()) + 86400 * i, 'o': 1, 'h': 1, 'l': 1, 'c': 1} for i in range(30)]
        MAO.evaluate(now=datetime(2026, 11, 30, 12, tzinfo=ET), fetch_intraday=lambda t: bars[t], fetch_daily=lambda t: spy, log=self.logs.append)
        by = {r['ticker']: r for r in MAO.load_advice()}
        self.assertEqual((by['TGT']['status'], by['TGT']['exit'], by['TGT']['r']), ('target', 120, 2.0))
        self.assertEqual((by['BTH']['status'], by['BTH']['exit'], by['BTH']['r']), ('stop', 90, -1.0))
        self.assertEqual((by['GAP']['status'], by['GAP']['exit'], by['GAP']['r']), ('stop', 80, -2.0))
        self.assertEqual((by['OLD']['status'], by['OLD']['exit'], by['OLD']['r']), ('expired', 104, 0.4))   # 20 個交易日到期，以當時價格
        self.assertEqual(by['BAD']['status'], 'unscored')
        lb = {x['agent']: x for x in MAO.leaderboard()}
        self.assertEqual((lb['claude']['hit_rate'], lb['claude']['avg_r'], lb['claude']['sim_pnl']), (50.0, 0.5, 100.0))
        self.assertEqual((lb['agy']['avg_r'], lb['agy']['expired']), (-0.8, 1))
        self.assertEqual(lb['codex']['scored'], 0)

    def test_new_advice_supersedes_old_at_current_price(self):
        MAO.record_advice('spark', [{'ticker': 'NVDA', 'action': '續抱', 'stop': 200, 'target': 240, 'reason': ''}], when='2026-10-21T08:50:00-04:00', prices={'NVDA': 210})
        MAO.record_advice('spark', [{'ticker': 'NVDA', 'action': '加碼', 'stop': 205, 'target': 250, 'reason': ''}], when='2026-10-21T13:50:00-04:00', prices={'NVDA': 215})
        recs = sorted(MAO.load_advice(), key=lambda r: r['time'])
        self.assertEqual((recs[0]['status'], recs[0]['exit'], recs[0]['r']), ('superseded', 215, 0.5))
        self.assertEqual(recs[1]['status'], 'open')
        MAO.record_advice('muse', [{'ticker': 'NVDA', 'action': '續抱', 'stop': 200, 'target': 240, 'reason': ''}], when='2026-10-21T14:00:00-04:00', prices={'NVDA': 216})
        self.assertEqual(sorted(r['status'] for r in MAO.load_advice() if r['agent'] == 'spark'), ['open', 'superseded'])   # 別人的不受影響

    def test_ranking_needs_min_settled(self):
        recs = [{'agent': 'claude', 'scored': True, 'status': 'target', 'r': 3.0, 'ret': 5, 'ticker': 'A', 'price': 1, 'stop': 0.5}] + \
               [{'agent': 'agy', 'scored': True, 'status': s, 'r': r, 'ret': 1, 'ticker': 'A', 'price': 1, 'stop': 0.5} for s, r in (('target', 1.0), ('stop', -1.0), ('target', 1.5))]
        lb = MAO.leaderboard(recs)
        self.assertEqual([x['agent'] for x in lb][:2], ['agy', 'claude'])   # Claude 平均 R 較高但只結算 1 筆，排在後面

    # ---- 隱私：茂只出現在觀戰網頁與 Discord 每日戰報 ----
    def test_mao_not_in_ai_readable_outputs(self):
        import cloud_run as C
        st = {'agents': [{'id': 'claude', 'name': 'Claude', 'equity': 10000, 'ret_pct': 0, 'positions': [], 'trades': []}], 'trades': [],
              'mao': {'nav': 10500, 'holdings': [{'ticker': 'NVDA'}]}}
        tmp_docs = os.path.join(self.tmp, 'd')
        old = (C.DOCS, C.BASE)
        try:
            C.DOCS, C.BASE = tmp_docs, self.tmp
            os.makedirs(os.path.join(self.tmp, 'data'), exist_ok=True)
            C.build_brief(st, force=True)
            with open(os.path.join(tmp_docs, 'brief.txt'), encoding='utf-8') as f:
                self.assertNotIn('茂（FT', f.read())
        finally:
            C.DOCS, C.BASE = old
        self.assertNotIn('茂', S.render_text({'date': '2026-10-21', 'generated': '2026-10-21 20:10 ET', 'status': [], 'sectors': [], 'themes': [],
                                              'hot': {}, 'gainers': [], 'losers': [], 'new_highs': [], 'earnings': []}, groups={}))
        self.assertNotIn('mao', P.AGENTS)
        self.assertNotIn('mao', [a for a in D.AGENT_META])
        import bridge
        self.assertFalse(any('mao' in x for x in bridge.PULL))   # 不會被同步回 AI 的電腦

    def test_daily_report_once(self):
        posted = []
        post = lambda text, **k: posted.append(text) or True
        now = datetime(2026, 10, 21, 20, 30, tzinfo=ET)
        self.assertTrue(MAO.daily_report(post, now=now, log=self.logs.append))
        self.assertFalse(MAO.daily_report(post, now=now, log=self.logs.append))
        self.assertIn('茂（FT 對照組）', posted[0])
        self.assertFalse(MAO.daily_report(post, now=datetime(2026, 10, 22, 19, 0, tzinfo=ET), log=self.logs.append))


if __name__ == '__main__':
    unittest.main()

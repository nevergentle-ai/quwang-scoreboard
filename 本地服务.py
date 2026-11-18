# -*- coding: utf-8 -*-
"""
本地服务：让记分台网页"点一下就拉战绩"

数据源：League 客户端本地接口（LCU）
    官方 match-v5 不收录「海克斯大乱斗」（queueId 2400 / gameMode KIWI），
    所以只能走客户端。客户端里看得见的对局，这里都拿得到，还不需要 API Key。

前置条件:
    League 客户端必须开着（登录进大厅即可）。

用法:
    python 本地服务.py
    浏览器打开  http://127.0.0.1:8765/

接口:
    GET /api/ping                健康检查
    GET /api/matches?today=1&count=6&only=2400
"""
import os
import sys
import json
import time
import importlib.util
import socketserver
import http.server
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
HTML_NAME = '蛆王争霸赛.html'


def load_module(fname, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(BASE_DIR, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CLI = load_module('客户端拉取.py', 'lcupull')


def _game_block(g, lcu, champs, my_puuid, log=None):
    """一条战绩摘要 + 详情 → 前端要的 block；找不到自己那一方就返回 None。

    collect（按天）与 collect_all（全量）共用这一份，避免两处解析逻辑跑偏。
    """
    log = log or (lambda *a, **k: None)
    gid = g.get('gameId')
    try:
        det = lcu.get(f'/lol-match-history/v1/games/{gid}')
    except Exception as e:
        log(f'  [跳过] {gid}: {e}')
        return None

    ident = {x.get('participantId'): (x.get('player') or {})
             for x in (det.get('participantIdentities') or [])}
    parts = det.get('participants') or []
    mine = None
    for p in parts:
        if ident.get(p.get('participantId'), {}).get('puuid') == my_puuid:
            mine = p
            break
    if not mine:
        return None
    team = mine.get('teamId')

    rows = []
    for p in parts:
        if p.get('teamId') != team:
            continue
        st = p.get('stats') or {}
        pl = ident.get(p.get('participantId'), {})
        cid = str(p.get('championId') or '')
        cname, role = champs.get(cid, [f'英雄{cid}', '战'])
        # 国服/外服/新版客户端字段名不完全一致，逐个兜：gameName → riotIdGameName
        # → summonerName → displayName，最后才用座位号占位（绝不都叫「未知」）
        nm = (pl.get('gameName') or pl.get('riotIdGameName')
              or pl.get('summonerName') or pl.get('displayName') or '')
        nm = str(nm).strip()
        if not nm:
            nm = '玩家' + str(p.get('participantId') or '')
        rows.append({
            'name': nm,
            'champ': cname, 'role': role,
            'k': int(st.get('kills', 0)), 'd': int(st.get('deaths', 0)),
            'a': int(st.get('assists', 0)),
            'dmg': int(st.get('totalDamageDealtToChampions', 0)),
            'taken': int(st.get('totalDamageTaken', 0)),
            'heal': int(st.get('totalHeal', 0) or 0),
            'gold': int(st.get('goldEarned', 0)),
        })
    ts = int(g.get('gameCreation', 0)) // 1000
    qid = g.get('queueId')
    return {
        'date': time.strftime('%Y-%m-%d', time.localtime(ts)) if ts else '',
        'when': time.strftime('%m-%d %H:%M', time.localtime(ts)) if ts else '?',
        'queue': CLI.QUEUE_NAME.get(qid, f'队列{qid}'),
        'queueId': qid,
        'duration': max(1, round(int(det.get('gameDuration', 0)) / 60)),
        'win': bool((mine.get('stats') or {}).get('win')),
        'rows': rows,
        'gameId': gid,
    }


def collect(count=6, only=2400, today=False, date=None, verbose=True):
    """从客户端取对局。

    返回 (blocks, who, account)：
        blocks  [{date,when,queue,duration,win,rows,...}]
        who     当前登录账号的名字（兼容旧调用）
        account {name, tag, puuid} —— 前端靠 puuid 把不同账号的数据分开存
    """
    log = print if verbose else (lambda *a, **k: None)
    lcu = CLI.LCU()
    me = lcu.get('/lol-summoner/v1/current-summoner')
    my_puuid = me.get('puuid')

    # 一次翻 100 局摘要，才能在客户端历史里找到指定日期
    games, used = CLI.fetch_recent_games(lcu, me, end=100)
    if not games:
        log('  [警告] 三种战绩接口都没返回数据')
    champs = CLI.load_champion_meta()

    today_str = time.strftime('%Y-%m-%d')
    out = []
    for g in games:
        qid = g.get('queueId')
        if only is not None and qid != only:
            continue
        ts = int(g.get('gameCreation', 0)) // 1000
        d = time.strftime('%Y-%m-%d', time.localtime(ts)) if ts else ''
        if date and d != date:
            continue
        if today and d != today_str:
            continue
        blk = _game_block(g, lcu, champs, my_puuid, log)
        if not blk:
            continue
        out.append(blk)
        if len(out) >= count:
            break
    name = me.get('gameName') or me.get('displayName') or '?'
    account = {'name': name, 'tag': me.get('tagLine') or '',
               'puuid': me.get('puuid') or ''}
    return out, name, account


def all_limit():
    """全量拉取的局数上限，防止客户端历史特别长时一直翻；config.json 里可写 all_limit 调"""
    try:
        v = CLI.load_config().get('all_limit')
        return max(50, min(2000, int(v))) if v else 500
    except Exception:
        return 500


def collect_all(only=None, max_games=None, progress=None, verbose=True):
    """一次性把客户端能翻到的全部对局（指定队列）转成 blocks。

    返回 (blocks, who, account, stats)：blocks 保持客户端原序（最新在前），
    stats 记录翻页/接口/成功失败情况，前端用来告诉用户到底覆盖了多少。
    详情请求用少量并发，否则几百局要串行等好几分钟。
    """
    from concurrent.futures import ThreadPoolExecutor
    log = print if verbose else (lambda *a, **k: None)
    limit = max_games or all_limit()
    lcu = CLI.LCU()
    me = lcu.get('/lol-summoner/v1/current-summoner')
    my_puuid = me.get('puuid')
    games, meta = CLI.fetch_all_games(lcu, me, page_size=100, max_games=limit)
    log(f'  → 全量：翻到 {meta["total"]} 局摘要（{meta["pages"]} 页 / {meta["stop"]}）')
    champs = CLI.load_champion_meta()
    picked = [g for g in games if only is None or g.get('queueId') == only]
    stats = {
        'histTotal': meta['total'], 'pages': meta['pages'], 'stop': meta['stop'],
        'truncated': meta['truncated'], 'source': meta['source'],
        'queueTotal': len(picked), 'ok': 0, 'failed': 0, 'limit': limit,
        'retried': meta['retried'], 'firstCount': meta['firstCount'],
    }
    blocks = []
    noop = lambda *a, **k: None
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(_game_block, g, lcu, champs, my_puuid, noop) for g in picked]
        for i, f in enumerate(futs):
            try:
                b = f.result()
            except Exception as e:
                log(f'  [跳过] 详情异常：{type(e).__name__}: {e}')
                b = None
            if b:
                blocks.append(b)
                stats['ok'] += 1
            else:
                stats['failed'] += 1
            if progress and ((i + 1) % 10 == 0 or i + 1 == len(futs)):
                try:
                    progress(i + 1, len(futs))
                except Exception:
                    pass
    name = me.get('gameName') or me.get('displayName') or '?'
    account = {'name': name, 'tag': me.get('tagLine') or '',
               'puuid': me.get('puuid') or ''}
    return blocks, name, account, stats


def available_dates(only=None):
    """扫描客户端历史，返回 [{date, count}]，按日期倒序。前端用它做下拉选项。"""
    if only is None:
        only = configured_queue()
    lcu = CLI.LCU()
    me = lcu.get('/lol-summoner/v1/current-summoner')
    games, _used = CLI.fetch_recent_games(lcu, me, end=100)
    cnt = {}
    for g in games:
        if only is not None and g.get('queueId') != only:
            continue
        ts = int(g.get('gameCreation', 0)) // 1000
        if not ts:
            continue
        d = time.strftime('%Y-%m-%d', time.localtime(ts))
        cnt[d] = cnt.get(d, 0) + 1
    # 今天永远在列表里（哪怕还没打），否则用户选不到今天
    today = time.strftime('%Y-%m-%d')
    if today not in cnt:
        cnt[today] = 0
    rows = [{'date': k, 'count': cnt[k]} for k in sorted(cnt.keys(), reverse=True)]
    account = {'name': me.get('gameName') or me.get('displayName') or '?',
               'tag': me.get('tagLine') or '',
               'puuid': me.get('puuid') or ''}
    return rows, account


def configured_queue():
    """海克斯大乱斗的队列号。国服若不一致，可在 config.json 里写 queue_id 覆盖"""
    try:
        v = CLI.load_config().get('queue_id')
        return int(v) if v else 2400
    except Exception:
        return 2400


def queue_stats():
    """最近 100 局里各队列的数量，用来排查「为什么一局都没拉到」"""
    lcu = CLI.LCU()
    me = lcu.get('/lol-summoner/v1/current-summoner')
    games, _used = CLI.fetch_recent_games(lcu, me, end=100)
    cnt = {}
    for g in games:
        q = g.get('queueId')
        cnt[q] = cnt.get(q, 0) + 1
    rows = sorted(cnt.items(), key=lambda kv: -kv[1])
    return [{'queueId': q, 'count': n,
             'name': CLI.QUEUE_NAME.get(q, f'未知队列 {q}')} for q, n in rows]


def diagnose():
    """把取数链路每一环都摊开，用于跨服（尤其国服）排查"""
    lines = []

    def add(s=''):
        lines.append(str(s))

    add(f'时间：{time.strftime("%Y-%m-%d %H:%M:%S")}')
    add(f'Python：{sys.version.split()[0]}')

    try:
        d, src = CLI.find_install_dir()
        add(f'安装目录：{d}（来源：{src}）')
    except Exception as e:
        add(f'安装目录：检测失败 {type(e).__name__}: {e}')
    add()
    add('lockfile 候选（格式合法还不够，端口必须真的在听）：')
    any_shell = False
    for p in CLI.lockfile_candidates():
        if not os.path.isfile(p):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            size = -1
        info = CLI._parse_lockfile(p)
        if info:
            live = CLI._port_open(info['port'])
            state = '内容有效 / port=%s / %s' % (
                info['port'], '在听 ✓' if live else '端口已失效（残留，不能用）')
        elif size == 0:
            state = '空文件'
            any_shell = True
        else:
            state = '内容不合格式'
        add(f'  · {p}　[{size} 字节 / {state}]')
    chosen = CLI.find_lockfile()
    add(f'采用：{chosen or "（无有效且活着的 lockfile，改走客户端日志 / 进程命令行）"}')
    if not chosen and any_shell:
        add('  ↑ 有 0 字节的 lockfile：国服客户端常年如此，属正常，程序会改从客户端日志取凭据')
        add('    外服目录里「内容有效但端口已失效」的残留文件同样不会被采用（换账号后旧端口必死）')

    add()
    add(f'权限：{"管理员 ✓" if CLI.is_admin() else "普通用户（可能读不到客户端命令行）"}')
    try:
        d0, _src0 = CLI.find_install_dir()
        if d0:
            deep = CLI.find_lockfiles_deep(d0)
            add(f'深扫 lockfile（安装目录内 3 层）：{len(deep)} 个')
            for p, sz in deep[:10]:
                add(f'  · {p}　[{sz} 字节]')
    except Exception as e:
        add(f'深扫失败：{type(e).__name__}: {e}')

    # 进程枚举：区分「客户端没开」和「取不到命令行」
    add()
    try:
        allp = CLI.list_processes()
        league = CLI.league_processes()
        add(f'进程枚举：共 {len(allp)} 个（tasklist）')
        if league:
            add('疑似客户端进程：')
            for n, p in league[:8]:
                add(f'  · {n}　pid={p}')
            bodies = CLI.client_running(league)
            if bodies:
                add('游戏本体：' + '、'.join(bodies))
            else:
                add('游戏本体：❌ 没在跑（多半只开了 WeGame，没真正启动游戏）')
        else:
            add('疑似客户端进程：一个都没有 ⇒ 客户端 / WeGame 都没启动')
    except Exception as e:
        add(f'进程枚举失败：{type(e).__name__}: {e}')

    try:
        lcu = CLI.LCU()
    except SystemExit as e:
        add(f'客户端：未就绪 → {e}')
        add('凭据探活明细：')
        for x in CLI.CRED_DEBUG:
            add('  · ' + x)
        add('进程命令行明细：' + ('; '.join(CLI._PROC_DEBUG) or '未尝试'))
        add('提示：若「PEB 直读」读到 0 个命令行，多半是客户端以更高权限运行，'
            '点界面上的「🔑 以管理员重启」再诊断一次。')
        return _save_diag('\n'.join(lines))
    except Exception as e:
        add(f'客户端：连接失败 {type(e).__name__}: {e}')
        add('凭据探活明细：')
        for x in CLI.CRED_DEBUG:
            add('  · ' + x)
        return _save_diag('\n'.join(lines))

    add(f'客户端端口：{lcu.port}（凭据来源：{getattr(lcu, "source", "?")}）')
    add()
    add('凭据探活（只有真连接通过的那张才会被采用）：')
    for x in CLI.CRED_DEBUG:
        add('  · ' + x)

    me = {}
    try:
        me = lcu.get('/lol-summoner/v1/current-summoner')
        add(f'当前账号：{me.get("gameName") or me.get("displayName") or "?"}'
            f'   puuid={(str(me.get("puuid")) or "")[:12]}…'
            f'   accountId={me.get("accountId")}')
    except Exception as e:
        add(f'当前账号：读取失败 {type(e).__name__}: {e}')

    tried = []
    try:
        games, used = CLI.fetch_recent_games(lcu, me, end=100, tried=tried)
    except Exception as e:
        add(f'战绩接口：整体失败 {type(e).__name__}: {e}')
        return _save_diag('\n'.join(lines))

    add()
    add('战绩接口尝试：')
    for p, n in tried:
        add(f'  · {p.split("?")[0]}   →   {n}')
    add(f'命中：{used or "（三种接口都没有数据）"}')
    add(f'拿到对局：{len(games)} 条')

    if games:
        hist = {}
        for g in games:
            q = g.get('queueId')
            hist[q] = hist.get(q, 0) + 1
        add()
        add('队列分布（最近 100 局）：')
        for q, n in sorted(hist.items(), key=lambda kv: -kv[1]):
            add(f'  · queueId={q}　{CLI.QUEUE_NAME.get(q, "未知队列")}　× {n} 局')
        add(f'当前筛选队列：{configured_queue()}（海克斯大乱斗默认 2400）')
        add()
        add('最近一局字段：' + ', '.join(sorted(games[0].keys())))
        add('最近一局：queueId=%s  gameMode=%s  gameCreation=%s'
            % (games[0].get('queueId'), games[0].get('gameMode'),
               games[0].get('gameCreation')))
    return _save_diag('\n'.join(lines))


def _save_diag(report):
    try:
        os.makedirs(CLI.CFG_DIR, exist_ok=True)
        p = os.path.join(CLI.CFG_DIR, 'diag.txt')
        open(p, 'w', encoding='utf-8').write(report)
        report += f'\n\n（已保存到 {p}）'
    except Exception:
        pass
    return report


def gameflow_phase():
    """当前客户端阶段：None / Lobby / Matchmaking / ChampSelect / InProgress / EndOfGame ..."""
    lcu = CLI.LCU()
    ph = lcu.get('/lol-gameflow/v1/gameflow-phase')
    return ph if isinstance(ph, str) else str(ph)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BASE_DIR, **kw)

    def log_message(self, fmt, *args):
        msg = fmt % args
        if '/api/' in msg:
            print('  ' + msg)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        if path == '/api/ping':
            return self._json({'ok': True, 'service': 'quwang-local', 'source': 'lcu', 'port': PORT})

        if path == '/api/dates':
            try:
                rows, acct = available_dates()
                return self._json({'ok': True, 'dates': rows, 'account': acct,
                                   'puuid': acct.get('puuid', '')})
            except SystemExit as e:
                return self._json({'ok': False, 'error': str(e)}, 200)
            except Exception as e:
                return self._json({'ok': False, 'error': f'{type(e).__name__}: {e}'}, 200)

        if path == '/api/live':
            try:
                return self._json({'ok': True, 'phase': gameflow_phase()})
            except SystemExit as e:
                return self._json({'ok': False, 'error': str(e)}, 200)
            except Exception as e:
                return self._json({'ok': False, 'error': f'{type(e).__name__}: {e}'}, 200)

        if path == '/api/queues':
            try:
                return self._json({'ok': True, 'queues': queue_stats(),
                                   'configured': configured_queue()})
            except SystemExit as e:
                return self._json({'ok': False, 'error': str(e)}, 200)
            except Exception as e:
                return self._json({'ok': False, 'error': f'{type(e).__name__}: {e}'}, 200)

        if path == '/api/diag':
            try:
                return self._json({'ok': True, 'text': diagnose()})
            except SystemExit as e:
                return self._json({'ok': False, 'error': str(e)}, 200)
            except Exception as e:
                return self._json({'ok': False, 'error': f'{type(e).__name__}: {e}'}, 200)

        if path == '/api/matches':
            return self._handle_matches(urllib.parse.parse_qs(parsed.query))

        if path in ('/', '/index.html'):
            self.send_response(302)
            self.send_header('Location', '/' + urllib.parse.quote(HTML_NAME))
            self.end_headers()
            return

        return super().do_GET()

    def _handle_matches(self, qs):
        today = qs.get('today', ['0'])[0] not in ('0', 'false', '')
        date = (qs.get('date', [''])[0] or '').strip() or None
        try:
            count = max(1, min(50, int(qs.get('count', ['20'])[0])))
        except ValueError:
            count = 20
        only_raw = qs.get('only', [str(configured_queue())])[0]
        only = None if only_raw in ('', 'all') else int(only_raw)

        if qs.get('all', ['0'])[0] not in ('0', 'false', ''):
            print(f'  → 全量拉取 only={only}（不限日期）')
            try:
                blocks, who, acct, stats = collect_all(only=only)
            except SystemExit as e:
                print(f'  ✗ {e}')
                return self._json({'ok': False, 'error': str(e)}, 200)
            except Exception as e:
                print(f'  ✗ {e}')
                return self._json({'ok': False, 'error': f'{type(e).__name__}: {e}'}, 200)
            print(f'  ✓ 全量返回 {len(blocks)} 局')
            return self._json({'ok': True, 'count': len(blocks), 'riotId': who,
                               'puuid': acct.get('puuid', ''), 'account': acct,
                               'matches': blocks, 'stats': stats})

        print(f'  → 客户端拉取 date={date or "(不限)"} today={today} count={count} only={only}')
        try:
            blocks, who, acct = collect(count=count, only=only, today=today, date=date)
        except SystemExit as e:
            print(f'  ✗ {e}')
            return self._json({'ok': False, 'error': str(e)}, 200)
        except Exception as e:
            print(f'  ✗ {e}')
            return self._json({'ok': False, 'error': f'{type(e).__name__}: {e}'}, 200)

        if not blocks and today and not date:
            print('  · 今天暂无对局，回退拉最近几局')
            try:
                blocks, who, acct = collect(count=count, only=only, today=False)
            except Exception:
                pass

        print(f'  ✓ 返回 {len(blocks)} 局')
        return self._json({'ok': True, 'count': len(blocks), 'riotId': who,
                           'puuid': acct.get('puuid', ''), 'account': acct,
                           'matches': blocks})


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    try:
        cr = CLI.resolve_credentials()
        cli_state = ('已连接 port=%s（凭据来源：%s）'
                     % (cr['port'], cr.get('source', '?'))) if cr else \
                    '未检测到 —— 请先打开 League 客户端并登录进大厅'
    except Exception as e:
        cli_state = '检测失败：%s: %s' % (type(e).__name__, e)
    print('=' * 60)
    print('  蛆王争霸赛 · 本地服务')
    print('=' * 60)
    print(f'  数据源 : League 客户端本地接口（不是官方 API）')
    print(f'  客户端 : {cli_state}')
    print(f'  端口   : {PORT}')
    print()
    print('  浏览器打开：')
    print(f'    http://127.0.0.1:{PORT}/')
    print()
    print('  按 Ctrl+C 停止')
    print('=' * 60)

    try:
        with Server(('127.0.0.1', PORT), Handler) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print('\n  已停止。')
    except OSError as e:
        print(f'\n  [错误] 端口 {PORT} 起不来：{e}')
        print('  可能已经有一个在跑了，或者端口被占用。')
        sys.exit(1)


if __name__ == '__main__':
    main()

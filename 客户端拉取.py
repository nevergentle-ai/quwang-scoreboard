# -*- coding: utf-8 -*-
"""
客户端本地接口拉取（LCU）—— 海克斯大乱斗专用

为什么需要这个:
    官方 match-v5 接口不收录「海克斯大乱斗」（gameMode=KIWI / queueId=2400），
    所以无论怎么拉都是空的。而客户端本地接口有完整记录，连 10 个人的数据都全。

前置条件:
    League 客户端必须处于运行状态（登录进大厅即可，不用开局）。
    脚本自动从 lockfile 读端口和口令，不需要任何 Key。

用法:
    python 客户端拉取.py                  # 最近 10 局
    python 客户端拉取.py --count 20
    python 客户端拉取.py --list           # 只列摘要
    python 客户端拉取.py --only 2400      # 只看海克斯大乱斗
    python 客户端拉取.py --today          # 只看今天的
"""
import os
import re
import sys
import json
import ssl
import glob
import time
import base64
import ctypes
import argparse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

QUEUE_NAME = {
    400: '普通征召', 420: '单双排', 430: '普通盲选', 440: '灵活排位',
    450: '极地大乱斗', 700: 'Clash', 720: 'Clash', 900: '无限乱斗',
    1700: '斗魂竞技场', 1710: '斗魂竞技场', 1900: '无限火力',
    2400: '海克斯大乱斗',
}

TAG_MAP = {
    'Tank': '坦', 'Fighter': '战', 'Mage': '法', 'Marksman': '射',
    'Support': '辅', 'Assassin': '战',
}

BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


# ============ 客户端位置检测（朋友机器路径各不相同，必须自动找） ============

CFG_DIR = os.path.join(os.environ.get('LOCALAPPDATA', BASE_DIR), 'QuWangScoreboard')
CFG_PATH = os.path.join(CFG_DIR, 'config.json')


def load_config():
    try:
        return json.loads(open(CFG_PATH, encoding='utf-8').read())
    except Exception:
        return {}


def save_config(patch):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        cur = load_config()
        cur.update(patch)
        open(CFG_PATH, 'w', encoding='utf-8').write(json.dumps(cur, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False


def _from_riot_metadata():
    """Riot 客户端自己记录的安装路径，最可靠"""
    out = []
    base = os.path.join('C:' + os.sep, 'ProgramData', 'Riot Games', 'Metadata')
    for name in ('league_of_legends.live', 'league_of_legends.pbe'):
        p = os.path.join(base, name, name + '.product_settings.yaml')
        if not os.path.isfile(p):
            continue
        try:
            txt = open(p, encoding='utf-8', errors='ignore').read()
            for line in txt.splitlines():
                if line.strip().startswith('product_install_full_path:'):
                    v = line.split(':', 1)[1].strip().strip('"').strip("'")
                    if v:
                        out.append(v.replace('/', os.sep))
                    break
        except Exception:
            pass
    return out


def _from_registry():
    """卸载项里通常带 InstallLocation（国服卸载项名字是中文，单独兜一层）"""
    out = []
    try:
        import winreg
    except ImportError:
        return out

    rels = [r'Microsoft\Windows\CurrentVersion\Uninstall\Riot Game league_of_legends.live']
    # 国服：Tencent 自己的注册位置
    tencent_keys = [
        (r'SOFTWARE\WOW6432Node\Tencent\LOL', ('InstallPath', 'InstallLocation', 'Path')),
        (r'SOFTWARE\Tencent\LOL', ('InstallPath', 'InstallLocation', 'Path')),
        (r'SOFTWARE\WOW6432Node\Tencent\LOLAssistant', ('InstallPath', 'Path')),
    ]
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for prefix in ('SOFTWARE\\WOW6432Node\\', 'SOFTWARE\\'):
            for rel in rels:
                try:
                    with winreg.OpenKey(hive, prefix + rel) as h:
                        v, _ = winreg.QueryValueEx(h, 'InstallLocation')
                        if v:
                            out.append(v)
                except OSError:
                    pass
        for key, names in tencent_keys:
            try:
                with winreg.OpenKey(hive, key) as h:
                    for n in names:
                        try:
                            v, _ = winreg.QueryValueEx(h, n)
                            if v and os.path.isdir(v):
                                out.append(v)
                                break
                        except OSError:
                            pass
            except OSError:
                pass

    # 遍历卸载列表，按「英雄联盟 / League of Legends」认名字（国服、外服都能捞到）
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for prefix in ('SOFTWARE\\WOW6432Node\\', 'SOFTWARE\\'):
            try:
                root = winreg.OpenKey(hive, prefix + r'Microsoft\Windows\CurrentVersion\Uninstall')
            except OSError:
                continue
            try:
                n = winreg.QueryInfoKey(root)[0]
                for i in range(n):
                    try:
                        sub = winreg.EnumKey(root, i)
                    except OSError:
                        continue
                    low = sub.lower()
                    if '英雄联盟' not in sub and 'league of legends' not in low:
                        continue
                    try:
                        with winreg.OpenKey(root, sub) as h:
                            v, _ = winreg.QueryValueEx(h, 'InstallLocation')
                            if v:
                                out.append(v)
                    except OSError:
                        pass
            except OSError:
                pass
            finally:
                try:
                    winreg.CloseKey(root)
                except Exception:
                    pass
    return out


def _common_paths():
    """常见安装位置：外服 Riot 那套 + 国服 WeGame 那套"""
    out = []
    for drive in 'CDEFGHIJ':
        # 外服
        for sub in ('Riot Games', os.path.join('Program Files', 'Riot Games'),
                    os.path.join('Program Files (x86)', 'Riot Games'),
                    'Games', 'Valorant', os.path.join('valorant', 'Riot Games'),
                    'Game', 'SteamLibrary\\steamapps\\common'):
            out.append(f'{drive}:\\{sub}\\League of Legends')
        # 国服（WeGame / 腾讯游戏）
        for sub in ('WeGameApps', os.path.join('Program Files (x86)', '腾讯游戏'),
                    os.path.join('Program Files', '腾讯游戏'), '腾讯游戏',
                    'Games', 'Tencent', 'WeGame'):
            out.append(f'{drive}:\\{sub}\\英雄联盟')
        out.append(f'{drive}:\\WeGameApps\\英雄联盟')
    return out


# 客户端根目录下可能出现 lockfile 的位置（国服常见放在 LeagueClient 子目录里）
LOCK_SUBDIRS = ('', 'LeagueClient', 'TCLS', 'Game', 'LeagueClientUx')


def _looks_like_client(d):
    """判断目录是不是 League 客户端根目录（不靠名字，靠特征文件）"""
    for f in ('lockfile', 'LeagueClient.exe', 'LeagueClientUx.exe',
              os.path.join('LeagueClient', 'LeagueClientUx.exe'),
              os.path.join('TCLS', 'client.exe')):
        if os.path.isfile(os.path.join(d, f)):
            return True
    return False


def _scan_drives(max_depth=2, budget=6.0):
    """兜底：扫各盘找客户端目录，限深度限时。国服目录名是「英雄联盟」，必须认得"""
    hits = []
    t0 = time.time()
    skip = {'$recycle.bin', 'system volume information', 'windows', 'appdata',
            'node_modules', '.git', 'perflogs', 'recovery', 'msocache'}
    names = {'league of legends', 'leagueoflegends', '英雄联盟', 'lol'}
    for ch in 'CDEFGHIJ':
        root = f'{ch}:\\'
        if not os.path.isdir(root):
            continue
        stack = [(root, 0)]
        while stack:
            if time.time() - t0 > budget:
                return hits
            cur, depth = stack.pop()
            try:
                entries = os.listdir(cur)
            except (PermissionError, OSError):
                continue
            for name in entries:
                p = os.path.join(cur, name)
                try:
                    if not os.path.isdir(p):
                        continue
                except OSError:
                    continue
                low = name.lower().replace('_', ' ').replace('-', ' ')
                if low in names or name == '英雄联盟':
                    hits.append(p)
                elif depth < max_depth and low not in skip:
                    stack.append((p, depth + 1))
    return hits


def _roots_from_cmdlines():
    """从正在跑的 League 进程命令行反推安装根目录。

    新版客户端日志不一定写 `https://riot:...@127.0.0.1`，`creds_from_logfiles`
    拿不到 root 时就只能退回 Riot 配置（常年指外服），目录会报错地方。
    进程命令行里的 exe 路径是最实的证据：...\\League of Legends\\LeagueClientUx.exe。
    """
    out = []
    try:
        _f, _n, lines = creds_from_peb()
    except Exception:
        return out
    for ln in lines:
        cand = ''
        m = re.search(r'--install-directory=(?:"([^"]+)"|([^\s"]+))', ln, re.I)
        if m:
            cand = (m.group(1) or m.group(2) or '').strip()
        if not (cand and os.path.isdir(cand)):
            em = re.match(r'\s*"?([A-Za-z]:[\\/][^"]*?\.exe)"?', ln)
            if not em:
                continue
            d = os.path.dirname(em.group(1).replace('/', '\\'))
            if os.path.basename(d).lower() in ('leagueclient', 'game'):
                d = os.path.dirname(d)
            cand = d
        if cand and os.path.isdir(cand) and cand not in out:
            out.append(cand)
    return out


def find_install_dir():
    """返回 (目录, 来源描述)。按可靠性从高到低逐级尝试。"""
    cfg = load_config()
    manual = cfg.get('lol_dir')
    if manual and os.path.isdir(manual):
        return manual, '手动指定'

    # 正在跑的客户端优先：Riot Metadata 常年指向外服目录，国服客户端在别处，
    # 换账号/跨服时按它报错会指错地方。日志里有活端口的那个根目录才是真身。
    try:
        for cr in creds_from_logfiles(limit=6):
            if cr.get('root') and _port_open(cr['port']):
                return cr['root'], '运行中的客户端'
    except Exception:
        pass

    # 日志里没有可用凭据（新版客户端常见）→ 从进程 exe 路径反推，别退回外服残留
    for d in _roots_from_cmdlines():
        return d, '运行中的客户端'

    cands = []
    for d in _from_riot_metadata():
        cands.append((d, 'Riot 配置'))
    for d in _from_registry():
        if d:
            cands.append((d, '注册表'))
    for d in _common_paths():
        cands.append((d, '常见路径'))
    for d in _scan_drives():
        cands.append((d, '磁盘扫描'))

    # 先挑「确实是客户端目录」的（有 LeagueClientUx.exe / lockfile 等特征文件）
    for d, src in cands:
        if os.path.isdir(d) and _looks_like_client(d):
            return d, src
    # 退一步：目录存在就算（有些国服包把 exe 放在更深的子目录里）
    for d, src in cands:
        if os.path.isdir(d):
            return d, src
    return None, None


def _parse_lockfile(path):
    """读 lockfile 并校验；空文件、残文件一律当无效（国服某些版本会留个 0 字节空壳）"""
    try:
        raw = open(path, encoding='utf-8', errors='ignore').read().strip()
    except OSError:
        return None
    if not raw:
        return None
    parts = raw.split(':')
    if len(parts) < 5 or not parts[2].isdigit():
        return None
    return {'path': path, 'port': parts[2], 'password': parts[3], 'raw': raw}


def lockfile_candidates(d=None):
    """所有客户端根目录下可能放 lockfile 的位置。

    以前只看 find_install_dir() 返回的那一个目录：外服残留的旧 lockfile
    会把国服客户端挤掉，换账号后直接连到死端口。现在多客户端共存时全扫，
    由 resolve_credentials() 逐个探活决定用谁。
    """
    roots = []
    if d:
        roots.append(d)
    d0, _src = find_install_dir()
    if d0:
        roots.append(d0)
    try:
        roots.extend(_client_roots())
    except Exception:
        pass
    seen, out = set(), []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        paths = []
        for sub in LOCK_SUBDIRS:
            paths.append(os.path.join(root, sub, 'lockfile') if sub
                         else os.path.join(root, 'lockfile'))
        try:
            for name in os.listdir(root):
                if os.path.isdir(os.path.join(root, name)):
                    paths.append(os.path.join(root, name, 'lockfile'))
        except OSError:
            pass
        for p in paths:
            k = os.path.normcase(os.path.abspath(p))
            if k not in seen:
                seen.add(k)
                out.append(p)
    return out


def find_lockfile():
    """返回「内容有效且端口确实在监听」的 lockfile 路径。

    客户端关掉后 lockfile 常常原样留在磁盘上，内容格式完全合法，端口却早死了；
    只判 isfile 会把它当成"客户端已连接"，换账号 / 重开客户端后必然 10061。
    """
    for p in lockfile_candidates():
        if not os.path.isfile(p):
            continue
        info = _parse_lockfile(p)
        if info and _port_open(info['port']):
            return p
    return None


_PROC_CACHE = {'creds': None, 'ts': 0.0, 'fail_ts': 0.0}
_PROC_TTL = 180.0          # 命令行取到的凭据缓存 3 分钟，避免每次 ping 都起 PowerShell
_PROC_FAIL_TTL = 8.0       # 失败也缓存几秒：诊断会连着调两次，否则白跑两轮 20s 的 PS
_PROC_DEBUG = []           # 取凭据的过程记录，供诊断输出


def invalidate_creds_cache():
    _PROC_CACHE['creds'] = None
    _PROC_CACHE['ts'] = 0.0
    _PROC_CACHE['fail_ts'] = 0.0


def _decode_out(data):
    """中文 Windows 的命令行输出是 GBK，不能硬按 UTF-8 解，否则读线程直接炸"""
    if not data:
        return ''
    if isinstance(data, str):
        return data
    for enc in ('utf-8', 'gbk', 'cp936', 'mbcs'):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('utf-8', 'ignore')


def _run_quiet(args, timeout):
    out, _err = _run_e(args, timeout)
    return out


def _run_e(args, timeout):
    """返回 (stdout, stderr)。stderr 一定要留——不然失败原因全被吞掉"""
    try:
        import subprocess
        r = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=0x08000000)      # CREATE_NO_WINDOW，不闪黑框
        return _decode_out(r.stdout), _decode_out(r.stderr)
    except Exception as e:
        return '', f'{type(e).__name__}: {e}'


def is_admin():
    """当前进程是不是管理员权限——读别的进程 CommandLine 需要它"""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def list_processes():
    """用 tasklist 枚举进程（系统自带，不依赖 PowerShell）"""
    out = _run_quiet(['tasklist', '/fo', 'csv', '/nh'], 10)
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith('"'):
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def league_processes():
    """筛出跟客户端相关的进程（国服还要认 WeGame / Tencent）"""
    keys = ('leagueclient', 'riotclientservices', 'wegame', 'tencent', 'lol')
    hits = []
    for name, pid in list_processes():
        low = name.lower()
        if any(k in low for k in keys):
            hits.append((name, pid))
    return hits


def client_running(procs=None):
    """真正的游戏客户端（LeagueClientUx.exe）在不在跑；只开 WeGame 不算"""
    procs = procs if procs is not None else league_processes()
    return [n for n, _ in procs if 'leagueclient' in n.lower()]


def find_lockfiles_deep(root, max_depth=3):
    """把安装目录里所有叫 lockfile 的文件都翻出来（含大小），排查「是不是写别处了」"""
    hits = []
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for cur, dirs, files in os.walk(root):
        if cur.count(os.sep) - base_depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d.lower() not in
                   ('game', 'logs', 'cache', 'data', 'node_modules')]
        for f in files:
            if f.lower() == 'lockfile':
                p = os.path.join(cur, f)
                try:
                    hits.append((p, os.path.getsize(p)))
                except OSError:
                    hits.append((p, -1))
    return hits


def _ps_run(script, timeout=15):
    """跑一段 PowerShell 脚本（-EncodedCommand 传参，绕开引号被吃）"""
    enc = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return _run_e(['pow' + 'ershell', '-NoProfile', '-NonInteractive',
                   '-EncodedCommand', enc], timeout)


def _noise(s):
    """PowerShell 往 stderr 吐的 CLIXML 包装，没信息量"""
    line = (s or '').strip().splitlines()
    if not line:
        return ''
    first = line[0].strip()
    if first.startswith('#<') or 'CLIXML' in first:
        return 'PowerShell 进度流噪音'
    return first[:90]


# ---------- PEB 直读进程命令行：不依赖 wmic / PowerShell / WMI / CIM ----------
# 国服实测这些接口可能全被拦（wmic 已移除、CIM 0 行），但同用户的普通进程
# 用 OpenProcess + ReadProcessMemory 就能读到命令行，普通权限即可。
class _PBI(ctypes.Structure):
    _fields_ = [('Reserved1', ctypes.c_void_p),
                ('PebBaseAddress', ctypes.c_void_p),
                ('Reserved2', ctypes.c_void_p * 2),
                ('UniqueProcessId', ctypes.c_void_p),
                ('Reserved3', ctypes.c_void_p)]


class _US64(ctypes.Structure):
    _fields_ = [('Length', ctypes.c_uint16), ('MaximumLength', ctypes.c_uint16),
                ('_pad', ctypes.c_uint32), ('Buffer', ctypes.c_void_p)]


class _US32(ctypes.Structure):
    _fields_ = [('Length', ctypes.c_uint16), ('MaximumLength', ctypes.c_uint16),
                ('Buffer', ctypes.c_uint32)]


def _read_mem(k32, h, addr, size):
    if not addr or size <= 0:
        return None
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    ok = k32.ReadProcessMemory(ctypes.c_void_p(h), ctypes.c_void_p(addr),
                               buf, ctypes.c_size_t(size), ctypes.byref(got))
    if not ok or got.value != size:
        return None
    return buf.raw


def cmdline_of_pid(pid):
    """读指定进程命令行；返回 (命令行文字 or None, 原因)"""
    if sys.platform != 'win32':
        return None, '非 Windows'
    try:
        from ctypes import wintypes as wt
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        ntdll = ctypes.WinDLL('ntdll', use_last_error=True)
        k32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
        k32.OpenProcess.restype = wt.HANDLE
        k32.ReadProcessMemory.restype = wt.BOOL
        k32.CloseHandle.argtypes = (wt.HANDLE,)
        h = k32.OpenProcess(0x0010 | 0x0400, False, wt.DWORD(int(pid)))
        if not h:
            return None, 'OpenProcess err=%d' % ctypes.get_last_error()
        try:
            # ProcessWow64Information = 26；非 0 说明是 32 位进程跑在 64 位系统上
            wow = ctypes.c_void_p(0)
            ntdll.NtQueryInformationProcess(
                ctypes.c_void_p(h), ctypes.c_uint(26), ctypes.byref(wow),
                ctypes.c_size_t(ctypes.sizeof(wow)), None)
            pbi = _PBI()
            ret = ntdll.NtQueryInformationProcess(
                ctypes.c_void_p(h), ctypes.c_uint(0), ctypes.byref(pbi),
                ctypes.c_size_t(ctypes.sizeof(pbi)), None)
            native = ctypes.cast(pbi.PebBaseAddress, ctypes.c_void_p).value or 0
            if ret != 0 or not native:
                return None, 'NtQueryInformationProcess 0x%x' % (ret & 0xffffffff)

            # 候选：(PEB 地址, 是否 32 位结构)。WOW64 进程优先用 32 位 PEB
            cands = []
            if wow.value:
                cands.append((wow.value, True))
            cands.append((native, ctypes.sizeof(ctypes.c_void_p) == 4))
            for peb, is32 in cands:
                off_pp = 0x10 if is32 else 0x20
                off_cl = 0x40 if is32 else 0x70
                raw = _read_mem(k32, h, peb + off_pp, 4 if is32 else 8)
                if raw is None:
                    continue
                pp = int.from_bytes(raw, 'little')
                cls = _US32 if is32 else _US64
                raw = _read_mem(k32, h, pp + off_cl, ctypes.sizeof(cls))
                if raw is None:
                    continue
                us = cls.from_buffer_copy(raw)
                if not us.Buffer or us.Length < 2 or us.Length > 65535:
                    continue
                data = _read_mem(k32, h, us.Buffer, us.Length)
                if data:
                    return data.decode('utf-16-le', 'ignore'), 'ok'
            return None, 'PEB 结构读不到'
        finally:
            k32.CloseHandle(h)
    except Exception as e:
        return None, '%s: %s' % (type(e).__name__, e)


def _port_open(port, timeout=0.4):
    """端口是否有人监听（用来筛掉日志里过期的旧端口）"""
    try:
        import socket
        with socket.create_connection(('127.0.0.1', int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _client_roots():
    """所有可能装着客户端的根目录（含国服乱码目录名），用于找日志"""
    seen, out = set(), []
    # 手动指定的目录也要进日志扫描：有些机器装得偏，自动探测根本扫不到
    manual = load_config().get('lol_dir')
    if manual and os.path.isdir(manual):
        out.append(manual)
        seen.add(os.path.normcase(os.path.abspath(manual)))
    for getter in (_from_riot_metadata, _from_registry, _common_paths):
        try:
            for d in getter():
                if d and os.path.isdir(d):
                    k = os.path.normcase(os.path.abspath(d))
                    if k not in seen:
                        seen.add(k)
                        out.append(d)
        except Exception:
            pass
    # WeGame 根下逐个看：国服目录名可能是「英雄联盟」也可能是乱码
    for ch in 'CDEFGHIJ':
        for base in (f'{ch}:\\WeGameApps', f'{ch}:\\腾讯游戏',
                     f'{ch}:\\Program Files (x86)\\腾讯游戏',
                     f'{ch}:\\Program Files\\腾讯游戏'):
            if not os.path.isdir(base):
                continue
            try:
                names = os.listdir(base)
            except OSError:
                continue
            for n in names:
                p = os.path.join(base, n)
                if os.path.isdir(os.path.join(p, 'LeagueClient')):
                    k = os.path.normcase(os.path.abspath(p))
                    if k not in seen:
                        seen.add(k)
                        out.append(p)
    return out


def creds_from_logfiles(limit=6):
    """列出所有 LeagueClientUx.log 里的 LCU 凭据候选（按日志新鲜度倒序）。

    国服的两个死结：lockfile 常年 0 字节、客户端又被 WeGame 以管理员权限拉起
    （普通权限读进程命令行会被拒），但客户端自己会把
    `https://riot:<口令>@127.0.0.1:<端口>/index.html` 写进日志 —— 普通权限就能读。
    换账号 / 重开客户端会留下多份日志，所以这里给全部候选，由
    resolve_credentials() 逐个真连接探活，不再"第一个格式合法就认"。
    """
    cands = []
    for root in _client_roots():
        lc = os.path.join(root, 'LeagueClient')
        if not os.path.isdir(lc):
            continue
        try:
            for n in os.listdir(lc):
                if n.lower().endswith('leagueclientux.log'):
                    p = os.path.join(lc, n)
                    try:
                        cands.append((os.path.getmtime(p), p, root))
                    except OSError:
                        pass
        except OSError:
            continue
    cands.sort(reverse=True)          # 最新写入的那份 = 正在跑的客户端
    out, seen = [], set()
    for mt, p, root in cands[:limit]:
        try:
            size = os.path.getsize(p)
            with open(p, 'rb') as f:
                if size > 262144:     # 只读尾部，日志可能有几十 MB
                    f.seek(size - 262144)
                raw = f.read().decode('utf-8', 'ignore')
        except OSError:
            continue
        for m in re.finditer(r'https://riot:([A-Za-z0-9_\-]+)@127\.0\.0\.1:(\d+)', raw):
            port, pw = m.group(2), m.group(1)
            if (port, pw) in seen:
                continue
            seen.add((port, pw))
            out.append({'port': port, 'password': pw, 'source': '客户端日志',
                        'mtime': mt, 'root': root, 'path': p})
    return out


def creds_from_logfile(limit=6):
    """兼容旧调用：返回第一个端口仍在监听的日志凭据"""
    for cr in creds_from_logfiles(limit):
        if _port_open(cr['port']):
            return cr
    return None


def creds_from_peb():
    """从 League* 进程命令行直接取端口/口令；不需要管理员，也不需要 PowerShell"""
    found, tried, lines = None, 0, []
    procs = [x for x in list_processes() if 'leagueclient' in x[0].lower()]
    # 带 --app-port 的通常是主进程 LeagueClientUx.exe，Render 子进程排后面
    procs.sort(key=lambda x: (0 if x[0].lower() in
                              ('leagueclientux.exe', 'leagueclient.exe')
                              else 1, x[0].lower()))
    for name, pid in procs:
        tried += 1
        line, why = cmdline_of_pid(pid)
        if not line:
            continue
        lines.append(line)
        port = re.search(r'--app-port=(\d+)', line)
        tok = re.search(r'--remoting-auth-token=([\w\-.]+)', line)
        if port and tok:
            found = {'port': port.group(1), 'password': tok.group(1),
                     'source': '进程命令行(PEB)'}
            break
    return found, tried, lines


def creds_from_process(force=False):
    """lockfile 不可用时的后手：从 League* 进程的命令行里取端口和口令。
    国服客户端有时不留、或只留个 0 字节的 lockfile，但命令行参数一定有。"""
    if sys.platform != 'win32':
        return None
    now = time.time()
    if not force and _PROC_CACHE['creds'] and now - _PROC_CACHE['ts'] < _PROC_TTL:
        return _PROC_CACHE['creds']
    # 刚失败过就别再跑一轮（_PROC_DEBUG 保留上一轮明细，诊断照样能读）
    if (not force and not _PROC_CACHE['creds']
            and now - _PROC_CACHE['fail_ts'] < _PROC_FAIL_TTL):
        return None

    del _PROC_DEBUG[:]
    texts = []

    # ⓪ 客户端日志：国服 lockfile 是空壳、进程又是管理员权限，这条路不需要任何权限
    try:
        lf = creds_from_logfile()
        _PROC_DEBUG.append('客户端日志：' + ('取到凭据 ✓ port=' + lf['port']
                                            if lf else '没找到可用的端口/口令'))
        if lf:
            _PROC_CACHE['creds'] = lf
            _PROC_CACHE['ts'] = now
            return lf
    except Exception as e:
        _PROC_DEBUG.append('客户端日志读取失败：%s: %s' % (type(e).__name__, e))

    # ⓪' PEB 直读：零依赖、普通权限即可
    try:
        hit, nproc, peb_lines = creds_from_peb()
        _PROC_DEBUG.append('PEB 直读：%d 个 LeagueClient 进程，%d 个读到命令行，%s'
                           % (nproc, len(peb_lines), '取到凭据 ✓' if hit else '未取到'))
        if peb_lines:
            texts.append('\n'.join(peb_lines))
            # 诊断文件会被分享，口令必须脱敏
            snippet = re.sub(r'(--remoting-auth-token=)[\w\-.]+',
                             r'\1<已隐藏>', peb_lines[0][:200])
            _PROC_DEBUG.append('命令行片段：' + snippet)
        if hit:
            _PROC_CACHE['creds'] = hit
            _PROC_CACHE['ts'] = now
            return hit
    except Exception as e:
        _PROC_DEBUG.append('PEB 直读失败：%s: %s' % (type(e).__name__, e))

    # ① wmic —— 最快，但新版 Windows 已移除
    out, err = _run_e(['wmic', 'process', 'where', "name like 'League%'",
                       'get', 'commandline', '/format:list'], 6)
    if out.strip():
        _PROC_DEBUG.append('wmic 有输出')
        texts.append(out)
    else:
        _PROC_DEBUG.append('wmic 不可用（' + (_noise(err) or '无输出') + '）')

    # ② CIM：不用 -Filter，改在 PowerShell 里筛，避开 WQL 在某些机器上抽风
    probe = ('Get-CimInstance Win32_Process | Where-Object { $_.Name -like \'League*\' } '
             '| ForEach-Object { "$($_.Name)|$($_.ProcessId)|$($_.CommandLine)" }')
    t0 = time.time()
    out, err = _ps_run(probe, 20)
    rows = [l for l in out.splitlines() if l.strip()]
    named = [r for r in rows if '|' in r]
    with_cmd = [r for r in named if len(r.split('|', 2)) > 2 and r.split('|', 2)[2].strip()]
    _PROC_DEBUG.append('CIM %.1fs：%d 个 League 进程，%d 个带命令行'
                       % (time.time() - t0, len(named), len(with_cmd)))
    if err.strip():
        _PROC_DEBUG.append('CIM 报错：' + _noise(err))
    if with_cmd:
        texts.append('\n'.join(with_cmd))

    # ③ 老接口兜底（PS 5.1 有，PS 7 可能没有）
    if not texts:
        probe2 = ('Get-WmiObject Win32_Process | Where-Object { $_.Name -like \'League*\' } '
                  '| ForEach-Object { "$($_.Name)|$($_.ProcessId)|$($_.CommandLine)" }')
        out2, err2 = _ps_run(probe2, 20)
        rows2 = [l for l in out2.splitlines() if l.strip()]
        named2 = [r for r in rows2 if '|' in r]
        with2 = [r for r in named2 if len(r.split('|', 2)) > 2 and r.split('|', 2)[2].strip()]
        _PROC_DEBUG.append('WMI 老接口：%d 个，%d 个带命令行' % (len(named2), len(with2)))
        if err2.strip():
            _PROC_DEBUG.append('WMI 报错：' + _noise(err2))
        if with2:
            texts.append('\n'.join(with2))

    for txt in texts:
        port = re.search(r'--app-port=(\d+)', txt)
        tok = re.search(r'--remoting-auth-token=([\w\-.]+)', txt)
        if port and tok:
            creds = {'port': port.group(1), 'password': tok.group(1),
                     'source': '进程命令行'}
            _PROC_CACHE['creds'] = creds
            _PROC_CACHE['ts'] = time.time()
            return creds
    _PROC_CACHE['fail_ts'] = time.time()
    return None


# ---------- 凭据探活：文件合法 ≠ 客户端活着 ----------
CRED_DEBUG = []          # 候选与探活结果，诊断页原样输出


def _probe_cred(cred, timeout=6):
    """真连一次 LCU：端口在听只算一半，Basic Auth 被接受才算这张凭据能用。

    返回 (是否可用, 失败原因)。换账号 / 重开客户端后端口与口令都会变，
    残留 lockfile 的端口要么没人听、要么认证直接 401。
    """
    port = str(cred.get('port') or '')
    if not port.isdigit():
        return False, '端口无效'
    if not _port_open(port):
        return False, '端口未监听（残留凭据）'
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # 本地回环绝不能走系统代理：装了 Clash / 加速器会被代理成 502
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ctx))
        auth = base64.b64encode(
            ('riot:' + str(cred.get('password') or '')).encode()).decode()
        req = urllib.request.Request(
            'https://127.0.0.1:%s/lol-gameflow/v1/gameflow-phase' % port,
            headers={'Authorization': 'Basic ' + auth, 'Accept': 'application/json'})
        with opener.open(req, timeout=timeout) as r:
            r.read()
        return True, ''
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, str(e)[:60])


def _remember_creds(cr):
    _PROC_CACHE['creds'] = cr
    _PROC_CACHE['ts'] = time.time()
    return cr


def resolve_credentials(force=False):
    """挑一张「当前真正在跑、口令也对」的 LCU 凭据。

    踩过的坑：外服目录里留着一份格式合法、端口早已失效的 lockfile（53 字节、
    mtime 还是昨天），find_lockfile() 理直气壮地把它交出来，于是重开客户端 /
    换了国服账号之后一律 WinError 10061。所以这里不认文件内容，只认真连接结果。

    顺序：缓存 → 客户端日志（全部候选）→ lockfile（全部客户端根目录）
          → PEB 命令行 → wmic / CIM 重型兜底。
    """
    global CRED_DEBUG
    del CRED_DEBUG[:]
    now = time.time()
    cached = _PROC_CACHE.get('creds')
    if not force and cached and now - _PROC_CACHE['ts'] < _PROC_TTL:
        ok, why = _probe_cred(cached, timeout=4)
        CRED_DEBUG.append('缓存 port=%s（%s）：%s'
                          % (cached['port'], cached.get('source', '?'),
                             '可用 ✓' if ok else '失效 → ' + why))
        if ok:
            return cached
        invalidate_creds_cache()

    seen = set()

    def offer(cr, tag):
        if not cr:
            return None
        key = (str(cr.get('port')), str(cr.get('password')))
        if key in seen:
            return None
        seen.add(key)
        ok, why = _probe_cred(cr)
        CRED_DEBUG.append('%s port=%s：%s' % (tag, cr.get('port'), '可用 ✓' if ok else why))
        return _remember_creds(cr) if ok else None

    # ① 客户端日志：国服首选，普通权限就能读
    try:
        logs = creds_from_logfiles()
    except Exception as e:
        logs = []
        CRED_DEBUG.append('客户端日志读取失败：%s: %s' % (type(e).__name__, e))
    CRED_DEBUG.append('客户端日志候选：%d 份' % len(logs))
    for cr in logs:
        hit = offer(cr, '日志')
        if hit:
            return hit

    # ② lockfile：多客户端共存时全扫，按修改时间新→旧，仍要逐个探活
    lfs = []
    for p in lockfile_candidates():
        if not os.path.isfile(p):
            continue
        info = _parse_lockfile(p)
        if not info:
            continue
        try:
            mt = os.path.getmtime(p)
        except OSError:
            mt = 0
        lfs.append({'port': info['port'], 'password': info['password'],
                    'source': 'lockfile', 'mtime': mt, 'path': p})
    lfs.sort(key=lambda x: -(x.get('mtime') or 0))
    CRED_DEBUG.append('有效 lockfile 候选：%d 份' % len(lfs))
    for cr in lfs:
        hit = offer(cr, 'lockfile')
        if hit:
            return hit

    # ③ PEB 直读：零依赖、普通权限
    try:
        hit0, nproc, _lines = creds_from_peb()
        CRED_DEBUG.append('PEB 直读：%d 个客户端进程，%s'
                          % (nproc, '取到凭据' if hit0 else '未取到'))
        hit = offer(hit0, 'PEB') if hit0 else None
        if hit:
            return hit
    except Exception as e:
        CRED_DEBUG.append('PEB 直读失败：%s: %s' % (type(e).__name__, e))

    # ④ 重型兜底：wmic / CIM / WMI（只在前面全灭时才跑，别每次拖 20 秒）
    try:
        cr = creds_from_process(force=True)
        CRED_DEBUG.append('进程枚举兜底：%s'
                          % ('取到 port=' + cr['port'] if cr else '没取到'))
        hit = offer(cr, '进程枚举') if cr else None
        if hit:
            return hit
    except Exception as e:
        CRED_DEBUG.append('进程枚举兜底失败：%s: %s' % (type(e).__name__, e))

    _PROC_CACHE['fail_ts'] = time.time()
    return None


class LCU:
    def __init__(self):
        # 不再"找到一份格式合法的文件就认"：多客户端共存 / 换账号重开时，
        # 磁盘上会有多份凭据，只有真连接能通过的才是当前这个客户端。
        creds = resolve_credentials()

        if creds is None:
            d, src = find_install_dir()
            if not d:
                raise SystemExit(
                    '这台机器上没找到 League 客户端的安装位置。\n\n'
                    '可以点界面上的「指定目录」，手动选一下客户端文件夹。')
            procs = league_processes()
            clients = client_running(procs)
            if not clients:
                if procs:
                    raise SystemExit(
                        f'找到游戏装在：{d}（来源：{src}）\n'
                        f'只看到 {"、".join(n for n, _ in procs[:3])} 在跑，'
                        '游戏客户端本体没启动。\n\n'
                        '只开 WeGame 不算——要在 WeGame 里真正启动游戏并登录进大厅。')
                raise SystemExit(
                    f'找到游戏装在：{d}（来源：{src}）\n'
                    '但没发现任何 League / WeGame 进程 —— 客户端没在运行。\n\n'
                    '请打开 League 客户端并登录进大厅，再点一次。')
            names = '、'.join(clients[:3])
            detail = '\n'.join('  · ' + x for x in CRED_DEBUG[-5:]) or '  （没找到任何候选凭据）'
            raise SystemExit(
                f'找到游戏装在：{d}（来源：{src}）\n'
                f'客户端进程在跑（{names}），但所有 LCU 凭据都连不上：\n'
                + detail + '\n\n'
                '常见原因：客户端刚重开、端口还没写进日志；或磁盘上残留的旧 lockfile '
                '指向已经失效的端口（内容合法但连不上）。\n'
                '请确认已经「登录进大厅」，稍等几秒再点一次；仍不行就点「🔍 诊断」。')

        self.port = creds['port']
        self.password = creds['password']
        self.source = creds.get('source', '')
        self.base = f'https://127.0.0.1:{self.port}'
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        # 127.0.0.1 绝不能走系统代理：装了 Clash/加速器的人会把本地请求也代理走，
        # 直接 502/超时，看起来像"客户端没连上"
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=self.ctx))

    def get(self, path, timeout=30):
        auth = base64.b64encode(f'riot:{self.password}'.encode()).decode()
        req = urllib.request.Request(self.base + path,
                                     headers={'Authorization': f'Basic {auth}',
                                              'Accept': 'application/json'})
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception:
            # 客户端重开过就换端口，缓存作废，下次重新取
            invalidate_creds_cache()
            raise


# ============ 战绩列表：接口有好几版，国服不一定认第一个 ============

def game_list_of(resp):
    """兼容三种返回结构：{'games':{'games':[...]}} / {'games':[...]} / [...]"""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        g = resp.get('games')
        if isinstance(g, dict):
            return g.get('games') or []
        if isinstance(g, list):
            return g
    return []


def history_paths(me, end=100):
    """按优先级排列的战绩接口候选"""
    me = me or {}
    puuid = me.get('puuid') or ''
    acct = me.get('accountId')
    out = [f'/lol-match-history/v1/products/lol/current-summoner/matches'
           f'?begIndex=0&endIndex={end}']
    if puuid:
        out.append(f'/lol-match-history/v1/products/lol/{puuid}/matches'
                   f'?begIndex=0&endIndex={end}')
    if acct not in (None, ''):
        out.append(f'/lol-match-history/v3/matchlist/account/{acct}'
                   f'?begIndex=0&endIndex={end}')
    return out


def fetch_recent_games(lcu, me=None, end=100, tried=None):
    """依次试几种战绩接口，返回 (games, 命中的那个路径)。

    国服有的接口会被裁掉或换实现，所以不能只认死一个；客户端刚登录 / 刚换账号时
    第一个接口还可能整个卡住（实测首次 TimeoutError、第二次 0.08 秒就回）。
    首轮短超时快速换下一个，全灭再用长超时重来一轮，别让用户干等 30 秒。
    """
    paths = history_paths(me, end)
    for rnd, tmo in ((1, 8), (2, 25)):
        for p in paths:
            try:
                games = game_list_of(lcu.get(p, timeout=tmo))
            except Exception as e:
                if tried is not None:
                    tried.append((p + ('' if rnd == 1 else '（重试）'),
                                  f'错误 {type(e).__name__}'))
                continue
            if tried is not None:
                tried.append((p, len(games)))
            if games:
                return games, p
    return [], ''


def fetch_all_games(lcu, me=None, page_size=100, max_games=500, tried=None):
    """分页翻完客户端能给出的全部战绩摘要，返回 (games, meta)。

    LCU 的 begIndex/endIndex 是左闭右开的索引区间。客户端只保留有限历史，
    翻过头会返回空页 / 重复页 / 直接报错，遇到任一种就停；
    整页都是见过的 gameId 也停（有的实现会忽略 begIndex 一直回第一页）。
    第一页复用 fetch_recent_games，因为它已经处理了「国服哪个接口能用」。
    """
    meta = {'pages': 0, 'total': 0, 'truncated': False, 'source': '', 'stop': '',
            'retried': False, 'firstCount': 0}
    first, used = fetch_recent_games(lcu, me, end=page_size, tried=tried)
    if not used or not first:
        meta['stop'] = '战绩接口没返回数据'
        return [], meta
    meta['source'] = used
    base = used.split('?', 1)[0]
    meta['firstCount'] = len(first)
    # 客户端刚登录 / 刚换号时战绩列表会分批加载：同一接口实测一会儿吐 101 局、
    # 一会儿只给 21 局。结果明显偏少就稍等再取一次，取多的一批，
    # 不然「全量」会悄悄少一大半而用户毫不知情。
    if len(first) < page_size // 2:
        try:
            time.sleep(1.2)
            again = game_list_of(lcu.get(
                f'{base}?begIndex=0&endIndex={page_size}', timeout=20))
            if len(again) > len(first):
                first = again
                meta['retried'] = True
        except Exception:
            pass

    def key(g):
        return str(g.get('gameId') or '') + '|' + str(g.get('gameCreation') or '')

    games, seen = [], set()
    page = 0
    while len(games) < max_games:
        if page == 0:
            batch = first
        else:
            beg = page * page_size
            url = f'{base}?begIndex={beg}&endIndex={beg + page_size}'
            try:
                batch = game_list_of(lcu.get(url, timeout=20))
            except Exception as e:
                meta['stop'] = f'第 {page + 1} 页读取失败：{type(e).__name__}'
                break
        fresh = []
        for g in batch:
            k = key(g)
            if k not in seen:
                seen.add(k)
                fresh.append(g)
        games.extend(fresh)
        meta['pages'] = page + 1
        if not fresh:
            meta['stop'] = '翻到空页 / 全重复页（已到底）'
            break
        if len(batch) > page_size:
            # 国服实测：current-summoner/matches 压根忽略 begIndex/endIndex，
            # 请求 endIndex=5 也一次吐出全部历史。这时第一页就是全量，别再翻。
            meta['stop'] = '接口一次返回全部历史（忽略分页参数）'
            break
        if len(batch) < page_size:
            meta['stop'] = '最后一页不足一整页'
            break
        page += 1
    result = games[:max_games]
    meta['total'] = len(result)
    meta['truncated'] = bool(meta['truncated'] or len(games) > len(result))
    if meta['truncated'] and not meta['stop']:
        meta['stop'] = '达到局数上限'
    return result, meta


def load_champion_meta():
    """英雄数字 ID -> (英文名, 定位)
    只缓存已跑通的版本；映射失败时再退回 _过程文件/champion_by_id.json 的旧缓存。"""
    cache = os.path.join(BASE_DIR, '_过程文件', 'champion_by_id.json')
    if os.path.isfile(cache):
        try:
            return json.loads(open(cache, encoding='utf-8').read())
        except Exception:
            pass
    try:
        def _get(u):
            req = urllib.request.Request(u, headers={'User-Agent': BROWSER_UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode('utf-8'))

        ver = _get('https://ddragon.leagueoflegends.com/api/versions.json')[0]
        data = _get(f'https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/champion.json')
        table = {}
        for cid, c in data['data'].items():
            role = '战'
            for t in (c.get('tags') or []):
                if t in TAG_MAP:
                    role = TAG_MAP[t]
                    break
            table[str(c['key'])] = [c.get('name', cid), role]
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        open(cache, 'w', encoding='utf-8').write(json.dumps(table, ensure_ascii=False))
        print(f'[英雄] 已缓存 {len(table)} 个英雄的 ID 映射')
        return table
    except Exception as e:
        print(f'[英雄] 映射获取失败（{e}），英雄名会显示为 ID')
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--count', type=int, default=10, help='拉最近多少局')
    ap.add_argument('--list', action='store_true', help='只列摘要，不输出 TSV')
    ap.add_argument('--only', type=int, default=None, help='只保留指定 queueId（2400=海克斯大乱斗）')
    ap.add_argument('--today', action='store_true', help='只保留今天的对局')
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    lcu = LCU()
    me = lcu.get('/lol-summoner/v1/current-summoner')
    my_puuid = me.get('puuid')
    my_name = me.get('gameName') or '?'
    print(f'[账号] {my_name}#{me.get("tagLine","")}  (Lv.{me.get("summonerLevel")})')

    # 列表只用来拿 gameId / 时间 / 队列；真正的 10 人数据要逐局调详情
    data = lcu.get(f'/lol-match-history/v1/products/lol/current-summoner/matches'
                   f'?begIndex=0&endIndex={max(args.count * 2, 20)}')
    games = (data.get('games') or {}).get('games') or []
    if not games:
        print('客户端里没有对局记录。')
        return

    champs = load_champion_meta()
    today_str = time.strftime('%Y-%m-%d')

    blocks = []
    for g in games:
        if len(blocks) >= args.count:
            break
        qid = g.get('queueId')
        if args.only is not None and qid != args.only:
            continue
        ts = int(g.get('gameCreation', 0)) // 1000
        when_d = time.strftime('%Y-%m-%d', time.localtime(ts)) if ts else ''
        if args.today and when_d != today_str:
            continue
        when = time.strftime('%m-%d %H:%M', time.localtime(ts)) if ts else '?'
        gid = g.get('gameId')

        try:
            det = lcu.get(f'/lol-match-history/v1/games/{gid}')
        except Exception as e:
            print(f'  [跳过] {gid}: {e}')
            continue

        ident = {}
        for x in (det.get('participantIdentities') or []):
            ident[x.get('participantId')] = x.get('player') or {}

        parts = det.get('participants') or []
        mine = None
        for p in parts:
            pl = ident.get(p.get('participantId'), {})
            if pl.get('puuid') == my_puuid:
                mine = p
                break
        if not mine:
            continue
        team = mine.get('teamId')
        dur = max(1, round(int(det.get('gameDuration', 0)) / 60))
        qname = QUEUE_NAME.get(qid, f'队列{qid}')

        rows = []
        for p in parts:
            if p.get('teamId') != team:
                continue
            st = p.get('stats') or {}
            pl = ident.get(p.get('participantId'), {})
            cid = str(p.get('championId') or '')
            cname, role = champs.get(cid, [f'英雄{cid}', '战'])
            rows.append({
                'name': pl.get('gameName') or pl.get('summonerName') or '未知',
                'champ': cname, 'role': role,
                'k': int(st.get('kills', 0)), 'd': int(st.get('deaths', 0)),
                'a': int(st.get('assists', 0)),
                'dmg': int(st.get('totalDamageDealtToChampions', 0)),
                'taken': int(st.get('totalDamageTaken', 0)),
                'heal': int(st.get('totalHeal', 0) or 0),
                'gold': int(st.get('goldEarned', 0)),
            })
        blocks.append({'when': when, 'date': when_d, 'queue': qname, 'queueId': qid,
                       'start': ts * 1000 if ts else 0,      # 真实开局时间（毫秒）
                       'duration': dur, 'win': bool((mine.get('stats') or {}).get('win')),
                       'rows': rows, 'gameId': gid})

    if not blocks:
        print('没有符合条件的对局。')
        print('  · 如果只想看海克斯大乱斗，加 --only 2400')
        return

    print()
    print('最近对局：')
    for b in blocks:
        print(f'  {b["when"]}  {b["queue"]:<8} {b["duration"]:>3}分钟  '
              f'{"胜" if b["win"] else "负"}  {len(b["rows"])}人')

    if args.list:
        return

    print()
    print('=' * 74)
    lines = []
    for b in blocks:
        lines.append(f'# {b["when"]}  {b["queue"]}  {b["duration"]}分钟  '
                     f'{"胜" if b["win"] else "负"}')
        for r in b['rows']:
            lines.append('\t'.join(str(x) for x in [r['name'], r['champ'], r['role'],
                                                     r['k'], r['d'], r['a'],
                                                     r['dmg'], r['taken'], r['heal'], r['gold']]))
        lines.append('')
    print('\n'.join(lines))
    print('=' * 74)

    dest = os.path.join(BASE_DIR, '_过程文件', '客户端_最近对局.txt')
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, 'w', encoding='utf-8').write('\n'.join(lines))
    print(f'\n  已存到: {dest}')
    print('  一局一段，# 开头是注释行，粘进记分台会自动跳过')


if __name__ == '__main__':
    main()

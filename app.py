# -*- coding: utf-8 -*-
"""
蛆王争霸赛 · 桌面版

双击运行，弹出独立窗口。不需要命令行，不需要浏览器。
数据源仍是 League 客户端本地接口（客户端要开着）。

打包命令（在项目目录执行）：
    pyinstaller --noconfirm --onefile --windowed ^
        --name 蛆王争霸赛 ^
        --add-data "蛆王争霸赛.html;." ^
        --add-data "本地服务.py;." ^
        --add-data "客户端拉取.py;." ^
        app.py
"""
import os
import sys
import time
import importlib.util

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS                      # PyInstaller 解包目录
    APP_DIR = os.path.dirname(sys.executable)    # exe 所在目录
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = BASE_DIR


def load_module(fname, alias):
    path = os.path.join(BASE_DIR, fname)
    if not os.path.isfile(path):
        path = os.path.join(APP_DIR, fname)
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SRV = load_module('本地服务.py', 'quwang_srv')


class Api:
    """暴露给网页调用的后端方法"""

    def ping(self, *args):
        # pywebview 调用时会额外塞一个参数，签名必须留 *args，否则 TypeError
        try:
            lcu = SRV.CLI.LCU()          # lockfile 坏了会自动退回进程命令行
            ph = lcu.get('/lol-gameflow/v1/gameflow-phase')
            # 顺手把当前登录账号带回去：多账号共用一台电脑时要靠它分区
            acct = {}
            try:
                me = lcu.get('/lol-summoner/v1/current-summoner')
                acct = {'name': me.get('gameName') or me.get('displayName') or '?',
                        'tag': me.get('tagLine') or '', 'puuid': me.get('puuid') or ''}
            except Exception:
                pass
            return {'ok': True, 'source': 'desktop', 'port': lcu.port,
                    'credSource': getattr(lcu, 'source', ''), 'phase': ph,
                    'admin': SRV.CLI.is_admin(), 'account': acct}
        except SystemExit as e:
            return {'ok': False, 'source': 'desktop', 'error': str(e),
                    'admin': SRV.CLI.is_admin()}
        except Exception as e:
            return {'ok': False, 'source': 'desktop',
                    'error': f'{type(e).__name__}: {e}',
                    'admin': SRV.CLI.is_admin()}

    def save_png(self, data_url, name='蛆王战报.png'):
        """保存战报图。

        WebView2 里 `<a download>` 会被拦：toBlob 的回调是异步的，a.click() 已经不在
        用户手势的调用栈里，浏览器判定为非用户触发的下载直接丢弃。
        改成把 dataURL 传回 Python，用原生保存对话框落盘。
        """
        import base64
        import webview
        try:
            s = str(data_url or '')
            if ',' not in s:
                return {'ok': False, 'error': '图片数据为空'}
            raw = base64.b64decode(s.split(',', 1)[1])
            win = webview.windows[0] if webview.windows else None
            if not win:
                return {'ok': False, 'error': '窗口没准备好'}
            dlg = getattr(getattr(webview, 'FileDialog', None), 'SAVE', None) or webview.SAVE_DIALOG
            res = win.create_file_dialog(dlg, save_filename=name,
                                         file_types=('PNG 图片 (*.png)',))
            if not res:
                return {'ok': False, 'cancelled': True}
            path = res if isinstance(res, str) else res[0]
            if not str(path).lower().endswith('.png'):
                path = str(path) + '.png'
            with open(path, 'wb') as f:
                f.write(raw)
            return {'ok': True, 'path': path, 'bytes': len(raw)}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    def choose_dir(self, *args):
        """弹窗让用户手动指定 League 安装目录（自动检测失败时的兜底）"""
        import webview
        try:
            win = webview.windows[0] if webview.windows else None
            if not win:
                return {'ok': False, 'error': '窗口没准备好'}
            res = win.create_file_dialog(webview.FOLDER_DIALOG)
            if not res:
                return {'ok': False, 'cancelled': True}
            d = res if isinstance(res, str) else res[0]
            d = d.strip()
            # 允许选到 League of Legends 上一层（国服目录名是「英雄联盟」）
            def _is_client_dir(p):
                for f in ('LeagueClient.exe', 'LeagueClientUx.exe',
                          os.path.join('LeagueClient', 'LeagueClientUx.exe'),
                          os.path.join('TCLS', 'client.exe'), 'lockfile'):
                    if os.path.isfile(os.path.join(p, f)):
                        return True
                return False

            if not _is_client_dir(d):
                sub = os.path.join(d, 'League of Legends')
                if _is_client_dir(sub):
                    d = sub
                else:
                    sub2 = os.path.join(d, '英雄联盟')
                    if _is_client_dir(sub2):
                        d = sub2
                    else:
                        return {'ok': False,
                                'error': f'这个目录里没找到 LeagueClient.exe / LeagueClientUx.exe：\n{d}\n\n'
                                         '请选包含客户端程序的那一层。'}
            SRV.CLI.save_config({'lol_dir': d})
            return {'ok': True, 'dir': d}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    def dates(self, *args):
        """返回客户端里有对局的日期列表"""
        try:
            rows, acct = SRV.available_dates()
            return {'ok': True, 'dates': rows, 'account': acct,
                    'puuid': acct.get('puuid', '')}
        except SystemExit as e:
            return {'ok': False, 'error': str(e)}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    def pull(self, date=None, *args):
        """date 格式 YYYY-MM-DD；传空表示不限日期"""
        try:
            date = (date or '').strip() or None
            blocks, who, acct = SRV.collect(count=30, only=SRV.configured_queue(), date=date)
            return {'ok': True, 'count': len(blocks), 'riotId': who,
                    'puuid': acct.get('puuid', ''), 'account': acct,
                    'matches': blocks}
        except SystemExit as e:
            return {'ok': False, 'error': str(e)}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    def pull_all(self, *args):
        """一键全量拉取：客户端能翻到的全部对局（当前配置队列），不限日期。

        局数多时耗时较长，用 evaluate_js 把进度写回按钮，别让界面像死了一样。
        """
        try:
            def progress(done, total):
                try:
                    import webview
                    win = webview.windows[0] if webview.windows else None
                    if win:
                        js = ("(function(){var b=document.querySelector('#btnPullAll');"
                              "if(b)b.textContent='全量拉取中… %d/%d 局';})();" % (done, total))
                        win.evaluate_js(js)
                except Exception:
                    pass

            blocks, who, acct, stats = SRV.collect_all(
                only=SRV.configured_queue(), progress=progress)
            return {'ok': True, 'count': len(blocks), 'riotId': who,
                    'puuid': acct.get('puuid', ''), 'account': acct,
                    'matches': blocks, 'stats': stats}
        except SystemExit as e:
            return {'ok': False, 'error': str(e)}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    def queues(self, *args):
        """客户端里实际存在哪些队列，排查「为什么拉不到」用"""
        try:
            return {'ok': True, 'queues': SRV.queue_stats(),
                    'configured': SRV.configured_queue()}
        except SystemExit as e:
            return {'ok': False, 'error': str(e)}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    def diag(self, *args):
        """把取数链路每一环摊开，供跨服排查"""
        try:
            return {'ok': True, 'text': SRV.diagnose()}
        except SystemExit as e:
            return {'ok': False, 'error': str(e)}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    def relaunch_admin(self, *args):
        """以管理员身份重启自己（读别的进程命令行需要管理员权限）"""
        if not getattr(sys, 'frozen', False):
            return {'ok': False, 'error': '只有打包后的 exe 支持一键提权。'}
        try:
            import ctypes
            import threading
            exe = sys.executable
            r = ctypes.windll.shell32.ShellExecuteW(
                None, 'runas', exe, '--elevated-relaunch', APP_DIR, 1)
            if r <= 32:
                return {'ok': False, 'error': f'提权被取消或失败（代码 {r}）'}
            # 让出互斥体：稍后硬退出，把位置留给新实例
            threading.Timer(0.3, lambda: os._exit(0)).start()
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    def live(self, *args):
        """当前客户端阶段，用来在界面上显示「对局进行中」"""
        try:
            return {'ok': True, 'phase': SRV.gameflow_phase()}
        except SystemExit as e:
            return {'ok': False, 'error': str(e)}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


def _tray_image():
    """托盘图标：深底绿环 + 三个节，远看像个虫"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((3, 3, 61, 61), fill=(16, 20, 28, 255), outline=(163, 241, 75, 255), width=3)
    for cy in (23, 32, 41):
        d.ellipse((26, cy - 5, 38, cy + 5), fill=(163, 241, 75, 255))
    return img


# ============ 单实例：只允许开一个 ============
MUTEX_NAME = 'QuWangScoreboard_SingleInstance_v1'
IPC_PORT = 47653                 # 让第二个实例能把已有窗口叫到前台
_MUTEX_HANDLE = None             # 全局持有，进程活着锁就在


def _already_running():
    """用 Windows 命名互斥体判重；拿不到锁说明已有实例在跑"""
    global _MUTEX_HANDLE
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = (wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR)
        h = k32.CreateMutexW(None, False, MUTEX_NAME)
        if not h:
            return False
        # 183 = ERROR_ALREADY_EXISTS
        if ctypes.get_last_error() == 183:
            k32.CloseHandle(h)
            return True
        _MUTEX_HANDLE = h        # 保留句柄，别让它被回收
        return False
    except Exception:
        return False


def _msgbox(text, title='蛆王争霸赛'):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)   # MB_ICONINFORMATION
    except Exception:
        print(text, flush=True)


def _signal_existing():
    """通知已经开着的那个实例：把窗口显示出来"""
    try:
        import socket
        with socket.create_connection(('127.0.0.1', IPC_PORT), timeout=1.5) as s:
            s.sendall(b'SHOW\n')
        return True
    except Exception:
        return False


def main():
    import webview
    import threading

    # 只许开一个：重复双击就把已开着的窗口叫出来，然后自己退出
    if _already_running():
        # 提权重启时旧实例正在退出，等它让出互斥体再继续
        if '--elevated-relaunch' in sys.argv:
            for _ in range(24):
                time.sleep(0.5)
                if not _already_running():
                    break
        if _already_running():
            if not _signal_existing():
                _msgbox('蛆王争霸赛已经在运行了。\n\n'
                        '看一下屏幕右下角的托盘图标，双击就能把窗口叫出来。')
            return

    html_path = os.path.join(BASE_DIR, '蛆王争霸赛.html')
    if not os.path.isfile(html_path):
        html_path = os.path.join(APP_DIR, '蛆王争霸赛.html')

    # localStorage 要持久化，必须关掉 private_mode 并指定存储目录
    data_dir = os.path.join(
        os.environ.get('LOCALAPPDATA', APP_DIR), 'QuWangScoreboard')
    os.makedirs(data_dir, exist_ok=True)

    state = {'tray': None, 'quitting': False}

    window = webview.create_window(
        '蛆王争霸赛 · 记分台',
        html_path,
        js_api=Api(),
        width=1260,
        height=920,
        min_size=(920, 640),
        text_select=True,
    )

    def show_window(icon=None, item=None):
        try:
            window.show()
            window.restore()
            # 隐藏期间页面定时器会被冻结，恢复后立刻补一次同步
            window.evaluate_js(
                'try{ if(typeof autoSyncTick==="function") autoSyncTick();'
                ' if(typeof refreshLive==="function") refreshLive(); }catch(e){}')
        except Exception:
            pass

    def quit_app(icon=None, item=None):
        state['quitting'] = True
        try:
            if state['tray']:
                state['tray'].stop()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass

    def on_closing():
        # 点 X 不退出，缩到托盘继续跑
        if state['quitting']:
            return True
        # 托盘没建成功就直接关，否则窗口一藏就找不回来了
        if not state['tray']:
            return True
        try:
            window.hide()
        except Exception:
            return True
        return False

    window.events.closing += on_closing

    def build_tray():
        try:
            import pystray
        except ImportError:
            return
        img = _tray_image()
        if img is None:
            return
        menu = pystray.Menu(
            pystray.MenuItem('显示主窗口', show_window, default=True),
            pystray.MenuItem('退出', quit_app),
        )
        icon = pystray.Icon('quwang', img, '蛆王争霸赛 · 记分台', menu)
        state['tray'] = icon
        try:
            print('[托盘] 图标线程启动', flush=True)
            icon.run()
        except Exception as e:
            print(f'[托盘] 启动失败: {e}', flush=True)

    def ipc_loop():
        """等第二个实例发来的 SHOW，把窗口从托盘里叫出来"""
        import socket
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.bind(('127.0.0.1', IPC_PORT))
            srv.listen(4)
        except OSError:
            try:
                srv.close()
            except Exception:
                pass
            return
        while not state.get('quitting'):
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            try:
                if b'SHOW' in conn.recv(64):
                    show_window()
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        try:
            srv.close()
        except Exception:
            pass

    threading.Thread(target=ipc_loop, daemon=True).start()
    threading.Thread(target=build_tray, daemon=True).start()

    webview.start(private_mode=False, storage_path=data_dir)


if __name__ == '__main__':
    main()

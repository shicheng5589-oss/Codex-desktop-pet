"""Codex 桌宠 —— 宿主程序。

一个贴在桌面上的透明小窗，里面跑 Live2D 形象：

* 状态来自 Codex 自己的本地记录（只读），见 codex_state.py
* 窗口无边框、置顶、不占任务栏；鼠标只有在形象身上才"抓住"窗口，其他位置点击穿透
* 跟着你在虚拟桌面之间移动，所以切到别的桌面也看得到进度
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes as wt
import functools
import http.server
import html
import json
import math
import os
import socketserver
import sys
import threading
import time

import settings_store

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")
WEB = os.path.join(HERE, "web")
POSITION_FILE = os.path.join(HERE, "pet_position.json")
PID_FILE = os.path.join(HERE, "pet.pid")
STOP_FILE = os.path.join(HERE, "stop.request")

sys.path.insert(0, VENDOR)
sys.path.insert(0, HERE)

WINDOW_W = 400     # 桌宠窗口：固定尺寸，下方放角色、上方放进度卡片（不再改尺寸）
WINDOW_H = 620
PANEL_W = 420
PANEL_H = 620
ICON_SIZE = 48  # 桌面图标大约这么大，"最小"就以它为参照
CHAR_BASE_H = 380  # 角色本身的基准高度（不含上方留给进度卡片的空间）
CARD_W = 380   # 进度卡片（CSS 像素）
CARD_H = 250
CARD_COMPACT_W = 300   # 桌宠缩得很小时的紧凑卡片
CARD_COMPACT_H = 108
CARD_RADIUS = 20       # 圆角半径（CSS 像素）
CARD_GAP = 4           # 卡片底部与"发梢/头顶"之间的空隙（CSS 像素，故意留得很小）
CARD_HEAD_TOP_RATIO = 0.18   # 卡片底边往下贴到模型高度的这个比例（压住头顶发丝，但不挡脸）
CARD_COMPACT_BELOW = 0.6   # 桌宠缩放小于这个值就用紧凑卡片
SCALE_MAX_WISH = 1.6       # 放大上限至少给到这里（以前按"不超过半屏"算，屏幕不高时只能到 120%~140%）

TOOL_BUTTONS = ("undo", "pencil", "eraser")
TOOL_EVENTS = ("single", "right")   # 三个图标：单击触发功能、右键出对应菜单
# 三个图标在"角色包围盒"里的横向中心位置（实测：把渲染出来的图按像素量出来的比例）。
# 注意：它们挤在角色中间偏左，一共只占 35%~56% 的宽度 —— 绝对不能按"三等分"去分，
# 以前就是这么分的，结果点最左边的撤回会落进中间那一格，提示"铅笔：未配置功能"。
TOOL_MARK_X = (("undo", 0.352), ("pencil", 0.457), ("eraser", 0.564))
TOOL_MARK_Y = 0.775      # 图标中心的纵向位置（相对角色包围盒高度）
TOOL_MARK_HALF_X = 0.085  # 横向：离最近的那个图标中心多近算点上了
TOOL_MARK_HALF_Y = 0.075  # 纵向：同上
TOOL_LABELS = {
    "undo": "↩️ 撤回",
    "pencil": "📝 铅笔",
    "eraser": "🧽 橡皮擦",
}
TOOL_ACTION_LABELS = {
    "none": "无功能",
    "open-settings": "打开设置面板",
    "open-menu": "打开右键菜单",
    "toggle-edit-mode": "切换编辑模式",
    "toggle-position-lock": "锁定 / 解锁位置",
    "reset-default": "恢复默认状态",
    "undo-expression": "撤销上一次表情",
    "clear-card": "清空进度卡片",
    "hide-pet": "隐藏到系统托盘",
    "mute-animations": "暂时静音动画",
    "codex-new-session": "新建 Codex 会话",
    "codex-focus": "切到 Codex / 定位输入框",
    "codex-rename-task": "重命名当前任务（复制新名称）",
    "codex-add-note": "添加备注",
    "codex-interrupt": "打断当前任务",
    "codex-undo-question": "撤回上一步输入",
    "codex-clear-context": "清空当前上下文（新建空白会话）",
    "codex-end-session": "结束当前会话（关闭 Codex 窗口）",
}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

# 缩放后的"重画补救"：改完窗口尺寸后按这个节奏连着重画，抹掉 Win11 留下的黑边。
# 前几次密一些（黑边基本都在换尺寸那一瞬间冒出来），后面越来越稀
# （有的机器要过一两百毫秒才把新表面准备好），最后一轮再把窗口"抖"1px，
# 逼 DWM 彻底重新合成一次。整套下来 ~1.15 秒，全是重绘，不改功能。
REPAIR_SCHEDULE_MS = (30, 30, 40, 60, 90, 130, 180, 250, 340)
# 上一版的做法：6 次固定 50ms、不抖窗口。放一个标记文件就退回它（见 repair_schedule）
REPAIR_SCHEDULE_LEGACY_MS = (50,) * 6
REPAIR_LEGACY_FLAG = os.path.join(HERE, "黑边修复-用旧版.txt")
REPAIR_KEEP_SECONDS = 3.0  # 这一段内的尺寸变化都算"同一次缩放"，并成一块一起刷


def repair_schedule() -> tuple:
    """当前用哪套补救节奏：默认新版；放了标记文件就退回上一版。"""
    try:
        if os.path.exists(REPAIR_LEGACY_FLAG):
            return REPAIR_SCHEDULE_LEGACY_MS, False
    except OSError:
        pass
    return REPAIR_SCHEDULE_MS, True

user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
user32.GetDpiForWindow.restype = ctypes.c_uint
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetForegroundWindow.restype = ctypes.c_void_p

log_lock = threading.Lock()
log_path = os.path.join(HERE, "pet.log")
LOG_MAX_BYTES = 512 * 1024     # 超过 512KB 就只保留最近 300 行
_log_size = 0
verbose_log = False            # 是否记录鼠标事件（排查用；默认关，设置里可开）


def log(message: str, force: bool = False) -> None:
    global _log_size
    if not force and not verbose_log:
        # 非强制日志里，只有鼠标事件会被跳过（它最频繁，也最没用）
        if message.startswith("鼠标事件"):
            return
    line = "%s  %s" % (time.strftime("%H:%M:%S"), message)
    with log_lock:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            _log_size += len(line.encode("utf-8")) + 1
            if _log_size > LOG_MAX_BYTES:
                with open(log_path, encoding="utf-8") as fh:
                    tail = fh.readlines()[-300:]
                with open(log_path, "w", encoding="utf-8") as fh:
                    fh.writelines(tail)
                _log_size = sum(len(t.encode("utf-8")) for t in tail)
        except OSError:
            pass
    print(line, flush=True)


def install_exception_logging() -> None:
    """pythonw 没有控制台，未捕获异常会凭空消失 —— 全部写进 pet.log。"""
    import traceback

    def handler(exc_type, exc_value, exc_tb):
        log("未捕获异常：%s" % "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))

    sys.excepthook = handler
    if hasattr(threading, "excepthook"):

        def thread_handler(args):
            handler(args.exc_type, args.exc_value, args.exc_traceback)

        threading.excepthook = thread_handler


def load_saved_position():
    try:
        with open(POSITION_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return int(data["x"]), int(data["y"])
    except Exception:
        return None


def save_position(x: int, y: int) -> None:
    try:
        with open(POSITION_FILE, "w", encoding="utf-8") as fh:
            json.dump({"x": int(x), "y": int(y)}, fh)
    except OSError:
        pass


def startup_link_path() -> str:
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
        "CodexPet.lnk",
    )


def codex_watch_link_path() -> str:
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
        "CodexPetWatcher.lnk",
    )


def codex_follow_enabled() -> bool:
    return os.path.exists(codex_watch_link_path())


def compute_card_position(pet, card_size, area, gap: int = CARD_GAP, head_offset: int = 0):
    """卡片位置：始终在**模型头顶正上方居中**；上面放不下就翻到桌宠下方。

    参数都是 (left, top, width, height) / 屏幕 (left, top, right, bottom)，
    head_offset = 模型实际可见顶部相对窗口顶部的距离（窗口比模型大，直接贴窗口会显得悬空）。
    返回卡片左上角 (x, y)。抽成纯函数是为了能单独验证"贴头、不重叠、不跑侧面"。
    """
    pet_left, pet_top, pet_w, pet_h = pet
    card_w, card_h = card_size
    area_left, area_top, area_right, area_bottom = area

    center = pet_left + pet_w // 2
    x = int(center - card_w / 2)
    x = max(area_left + 8, min(x, area_right - card_w - 8))

    y = pet_top + head_offset - card_h - gap   # 贴着模型头顶
    if y < area_top + 8:
        y = pet_top + pet_h + gap              # 顶上没空间 -> 放到脚下方，也不去侧面
    y = max(area_top + 8, min(y, area_bottom - card_h - 8))
    return x, y


def card_head_offset(char_rect) -> int:
    """卡片底边相对桌宠窗口顶部应该落在哪（物理像素）。

    模型外框顶部 + 18% 模型高度 —— 也就是允许卡片压住头顶那圈发丝/特效，
    但停在脸部之上。想再高/再低，就调 CARD_HEAD_TOP_RATIO。
    """
    if not char_rect:
        return 0
    _, top, _, height = char_rect
    return int(top + height * CARD_HEAD_TOP_RATIO)


class MouseTracker:
    """把鼠标按键状态翻译成动作。纯逻辑、不碰窗口，方便单独测。

    规则（按需求）：
      * 左键单击      -> pat（点在头部区域）或 poke（点在身体其他位置）
      * 左键双击      -> cycle（换一个表情）
      * 左键按住拖动  -> drag（超过阈值才算，避免和单击打架）
      * 右键单击      -> menu
      * 右键按住拖动  -> drag（并且不弹菜单）
    """

    DRAG_THRESHOLD = 5
    CLICK_MAX_SECONDS = 0.7
    DOUBLE_CLICK_SECONDS = 0.35
    SINGLE_CLICK_DELAY = 0.35

    def __init__(self) -> None:
        self.left_down = False
        self.right_down = False
        self.press_button = ""
        self.press_at = 0.0
        self.press_pos = (0, 0)
        self.press_on_char = False
        self.press_on_head = False
        self.moved = 0.0
        self.dragging = False
        self.last_left_click = 0.0
        self.pending_single = 0.0
        self.pending_head = False

    def feed(self, x: float, y: float, left: bool, right: bool, on_char: bool, on_head: bool, now: float) -> list[str]:
        events: list[str] = []

        if left and not self.left_down:
            self._press("left", x, y, on_char, on_head, now)
        elif not left and self.left_down:
            self._release(events, now)

        if right and not self.right_down:
            self._press("right", x, y, on_char, on_head, now)
        elif not right and self.right_down:
            self._release(events, now)

        if self.press_button:
            self.moved = max(self.moved, abs(x - self.press_pos[0]) + abs(y - self.press_pos[1]))
            if self.press_on_char and not self.dragging and self.moved > self.DRAG_THRESHOLD:
                self.dragging = True
                events.append("drag-start")

        events += self.tick(now)
        return events

    def tick(self, now: float) -> list[str]:
        """定时器每帧调用：把"等第二下"的单击判决定下来（否则单击永远不会触发）。"""
        events: list[str] = []
        if self.pending_single and now - self.pending_single > self.SINGLE_CLICK_DELAY:
            self.pending_single = 0.0
            events.append("pat" if self.pending_head else "poke")
        return events

    def _press(self, button: str, x: float, y: float, on_char: bool, on_head: bool, now: float) -> None:
        if button == "left":
            self.left_down = True
        else:
            self.right_down = True
        self.press_button = button
        self.press_at = now
        self.press_pos = (x, y)
        self.press_on_char = on_char
        self.press_on_head = on_head
        self.moved = 0.0
        self.dragging = False

    def _release(self, events: list[str], now: float) -> None:
        button = self.press_button
        dragged = self.dragging
        quick = (now - self.press_at) < self.CLICK_MAX_SECONDS
        on_char = self.press_on_char
        on_head = self.press_on_head

        self.dragging = False
        self.press_button = ""
        if button == "left":
            self.left_down = False
        else:
            self.right_down = False

        if dragged:
            events.append("drag-end")
            return
        if not on_char or not quick:
            return

        if button == "right":
            events.append("menu")
        elif self.pending_single and (now - self.last_left_click) < self.DOUBLE_CLICK_SECONDS:
            self.pending_single = 0.0
            self.last_left_click = 0.0
            events.append("cycle")
        else:
            self.last_left_click = now
            self.pending_single = now
            self.pending_head = on_head


def set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def init_sta() -> bool:
    """WebView2 needs a single-threaded apartment; threads start out as MTA."""
    hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    if hr in (0, 1):  # S_OK / S_FALSE
        return True
    log("COM 初始化失败 0x%08X" % (hr & 0xFFFFFFFF))
    return False


_singleton_handle = None


def single_instance() -> bool:
    """二开一次就够：桌宠已经在跑时，再启动直接退出。"""
    global _singleton_handle
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _singleton_handle = kernel32.CreateMutexW(None, False, "CodexPet.SingleInstance")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def desktop_name(handle) -> str:
    buf = ctypes.create_unicode_buffer(256)
    needed = ctypes.c_uint(0)
    if user32.GetUserObjectInformationW(ctypes.c_void_p(handle), 2, buf, 512, ctypes.byref(needed)):
        return buf.value
    return "<err %d>" % ctypes.get_last_error()


def hop_to_interactive_desktop() -> bool:
    """If we were launched inside a sandbox/other desktop, move this thread to the real one.

    A thread's desktop decides where its windows appear; threads created afterwards inherit
    the *process* desktop, so this has to run on every thread that creates windows.
    """
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    user32.GetThreadDesktop.restype = ctypes.c_void_p
    current = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    if desktop_name(current).lower() == "default":
        return True
    for access in (0x000F01FF, 0x00000100 | 0x00000002 | 0x00000001):
        handle = user32.OpenInputDesktop(0, False, access)
        if handle and user32.SetThreadDesktop(ctypes.c_void_p(handle)):
            log("窗口线程已切换到桌面 %s" % desktop_name(handle))
            return True
    log("无法切换到交互桌面，窗口可能不可见（err=%d）" % ctypes.get_last_error())
    return False


# ---------------------------------------------------------------- virtual desktops


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __eq__(self, other):
        return bytes(self) == bytes(other)

    def is_null(self) -> bool:
        return bytes(self) == b"\x00" * 16


def guid_from_string(text: str) -> GUID:
    guid = GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
    return guid


class VirtualDesktops:
    """Documented IVirtualDesktopManager, plus the foreground-window trick for "current"."""

    CLSID = "{AA509086-5CA9-4C25-8F95-589D3C07B48A}"
    IID = "{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}"

    def __init__(self) -> None:
        self.ok = False
        self.ptr = ctypes.c_void_p()
        try:
            clsid = guid_from_string(self.CLSID)
            iid = guid_from_string(self.IID)
            hr = ctypes.windll.ole32.CoCreateInstance(
                ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(self.ptr)
            )
            if hr != 0 or not self.ptr:
                raise OSError("CoCreateInstance failed: 0x%08X" % (hr & 0xFFFFFFFF))
            vtable = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            self._is_on_current = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, wt.HWND, ctypes.POINTER(wt.BOOL)
            )(vtable[3])
            self._desktop_id = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, wt.HWND, ctypes.POINTER(GUID)
            )(vtable[4])
            self._move = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, wt.HWND, ctypes.POINTER(GUID)
            )(vtable[5])
            self.ok = True
        except Exception as exc:  # noqa: BLE001
            log("虚拟桌面接口不可用：%r" % (exc,))

    def desktop_of(self, hwnd) -> GUID | None:
        if not self.ok or not hwnd:
            return None
        guid = GUID()
        if self._desktop_id(self.ptr, wt.HWND(hwnd), ctypes.byref(guid)) == 0 and not guid.is_null():
            return guid
        return None

    def move(self, hwnd, guid: GUID) -> bool:
        if not self.ok or not hwnd:
            return False
        return self._move(self.ptr, wt.HWND(hwnd), ctypes.byref(guid)) == 0

    def guid_text(self, guid: GUID | None) -> str:
        if not guid:
            return "?"
        raw = bytes(guid)
        return (
            "%08x-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x"
            % (
                int.from_bytes(raw[0:4], "little"),
                int.from_bytes(raw[4:6], "little"),
                int.from_bytes(raw[6:8], "little"),
                raw[8], raw[9], raw[10], raw[11], raw[12], raw[13], raw[14], raw[15],
            )
        )

    def follow(self, hwnd) -> None:
        """Keep the pet on whatever desktop the user is looking at right now."""
        if not self.ok or not hwnd:
            return
        foreground = user32.GetForegroundWindow()
        if not foreground or int(foreground) == int(hwnd):
            return
        here = self.desktop_of(hwnd)
        there = self.desktop_of(foreground)
        if here and there and here != there:
            if self.move(hwnd, there):
                log("跟随虚拟桌面，移动到当前桌面")
            elif not getattr(self, "_move_warned", False):
                self._move_warned = True
                log("跟随虚拟桌面失败（可能被系统策略挡住），桌宠会留在原桌面")

    def align_to(self, target_hwnd, source_hwnd) -> bool:
        """把 target 窗口搬到 source 窗口所在的虚拟桌面。

        用于"新开的设置窗口/卡片窗口"：它们默认落在进程启动时那个桌面，
        用户切过桌面后就看不见了。桌宠本身一直跟着用户，所以以它为准最可靠。
        """
        if not self.ok or not target_hwnd or not source_hwnd:
            return False
        there = self.desktop_of(source_hwnd)
        here = self.desktop_of(target_hwnd)
        if there and here and there != here:
            if self.move(target_hwnd, there):
                log("已把新窗口搬到桌宠所在的虚拟桌面")
                return True
        return False


# ------------------------------------------------------------------------ server


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: D102
        if os.environ.get("CODEXPET_HTTP_LOG"):
            log("HTTP %s" % (fmt % args))

    def log_error(self, fmt, *args):
        if os.environ.get("CODEXPET_HTTP_LOG"):
            log("HTTP-ERR %s" % (fmt % args))


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_server() -> int:
    handler = functools.partial(QuietHandler, directory=WEB)
    httpd = Server(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log("模型与界面服务已启动：http://127.0.0.1:%d/" % port)
    return port


# ---------------------------------------------------------------------- host app


class PetHost:
    def __init__(self, port: int, selftest: float, shot: str = "") -> None:
        import clr

        clr.AddReference(os.path.join(VENDOR, "webview", "lib", "Microsoft.Web.WebView2.Core.dll"))
        clr.AddReference(os.path.join(VENDOR, "webview", "lib", "Microsoft.Web.WebView2.WinForms.dll"))
        clr.AddReference("System.Drawing")
        clr.AddReference("System.Windows.Forms")

        loader_dir = os.path.join(VENDOR, "webview", "lib", "runtimes", "win-x64", "native")
        if os.path.isdir(loader_dir):
            os.environ["Path"] = os.environ.get("Path", "") + ";" + loader_dir

        self.clr = clr
        self.port = port
        self.selftest = selftest
        self.shot = shot
        self.queue: list[dict] = []
        self.lock = threading.Lock()
        self.panel_open = False
        self.settings = settings_store.load()
        self.webview = None
        self.settings_window = None
        self.settings_webview = None
        self._expressions = None
        self._motions = None
        self._props = None
        self.ready = threading.Event()
        self.status_ready = threading.Event()
        self.status = {}
        self.snapshot_ready = threading.Event()
        self.snapshot = ""
        self.desktops = None
        self.last_follow = 0.0
        self.reload_attempts = 0
        self.last_cursor = None
        self.last_cursor_send = 0.0
        self.last_stop_check = 0.0
        self.char_rect = None
        self.head_rect = None
        self.over_char = None
        self.alpha_probe_ok = False
        self.zoom_lock_until = 0.0
        self.resize_anchor = "bottom"
        self.pending_wheel = 0
        self.wheel_at = 0.0
        self._wheel_hook = None
        self.form_input_works = False
        self.card = None
        self.card_webview = None
        self.inline_card_open = False
        self.card_want_visible = False
        self.last_payload = None
        self.last_card_pos = None
        self.manual_drag = None
        self.manual_drag_started = 0.0
        self.manual_drag_moves = 0
        self.drag_anchor = None
        self.drag_button = "left"
        self.drag_started_at = 0.0
        self.drag_moves = 0
        self.drag_timer = None
        self.cursor_timer = None
        self.repair_timer = None
        self.repair_left = []
        self.repair_total = 0
        self.repair_nudge = True
        self.repair_rect = None       # 最近这次缩放涉及的屏幕区域（旧+新），用来让桌面重画
        self.repair_rect_at = 0.0
        self.mouse = MouseTracker()
        self.drag_stop = threading.Event()
        self.drag_monitor_stop = threading.Event()
        self.drag_samples = 0
        self.drag_changes = 0
        self.drag_max_gap_ms = 0.0
        self.drag_stop.set()
        self.drag_thread = None
        self.drag_started_at = 0.0
        self.drag_distance = 0.0
        self.render_fps = 0.0
        self.fps_log_at = 0.0
        self.last_drag_log = 0.0
        self.menu = None
        self.tray_icon = None
        self.edit_mode = False
        self.position_locked = False
        self.last_bottom_action = None

    # -- window construction -------------------------------------------------

    def build(self):
        from System.Drawing import Color, Size, Point
        from System.Windows.Forms import (
            Application,
            DockStyle,
            Form,
            FormBorderStyle,
            FormStartPosition,
            Screen,
            Timer,
        )
        from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties, WebView2

        self.Color = Color
        self.Size = Size
        self.Point = Point

        form = Form()
        form.Text = "Codex 桌宠"
        form.FormBorderStyle = getattr(FormBorderStyle, "None")
        form.StartPosition = FormStartPosition.Manual
        form.ShowInTaskbar = False
        form.TopMost = bool(self.settings.get("alwaysOnTop", True))
        form.BackColor = Color.Magenta
        form.TransparencyKey = Color.Magenta
        form.MinimizeBox = False
        form.MaximizeBox = False
        form.AllowTransparency = True

        area = Screen.PrimaryScreen.WorkingArea
        dpi = user32.GetDpiForWindow(ctypes.c_void_p(int(form.Handle.ToInt64())))
        self.dpi_scale = (dpi or 96) / 96.0
        self.pet_scale = float(self.settings.get("scale", 1.0))
        physical_w = int(round(WINDOW_W * self.dpi_scale * self.pet_scale))
        physical_h = int(round(WINDOW_H * self.dpi_scale * self.pet_scale))
        form.ClientSize = Size(physical_w, physical_h)
        form.Location = Point(area.Right - physical_w - 24, area.Bottom - physical_h - 8)
        form.Opacity = float(self.settings.get("opacity", 1.0))
        saved = load_saved_position()
        if saved:
            virtual = Screen.GetWorkingArea(form)
            if virtual.Left - 40 <= saved[0] <= virtual.Right - 40 and virtual.Top <= saved[1] <= virtual.Bottom - 40:
                form.Location = Point(saved[0], saved[1])
                log("窗口位置已恢复：%s" % (saved,))
        log("窗口 %dx%d（DPI %d，桌宠缩放 %.2f）" % (physical_w, physical_h, dpi, self.pet_scale))
        self.form = form

        webview = WebView2()
        props = self.create_props()
        webview.CreationProperties = props
        webview.Dock = DockStyle.Fill
        try:
            webview.DefaultBackgroundColor = Color.Transparent
        except Exception as exc:  # noqa: BLE001
            log("设置透明背景失败：%r" % (exc,))
        form.Controls.Add(webview)
        self.webview = webview

        webview.CoreWebView2InitializationCompleted += self.on_webview_ready
        webview.WebMessageReceived += self.on_web_message
        form.Shown += self.on_shown
        form.MouseWheel += self.on_mouse_wheel
        self.attach_mouse_events(form, webview)
        webview.EnsureCoreWebView2Async(None)

        self.timer = Timer()
        self.timer.Interval = 110
        self.timer.Tick += self.on_tick
        self.timer.Start()

        # 视线专用定时器：33ms（~30Hz）。
        # 眼睛的输入就是鼠标位置，110ms 才送一次的话，眼珠只能"一格一格"地追，
        # 再怎么平滑也还是会顿；这里单独跑快一点，送完立刻发出去。
        self.cursor_timer = Timer()
        self.cursor_timer.Interval = 33
        self.cursor_timer.Tick += self.on_cursor_tick
        self.cursor_timer.Start()

        # 拖动专用定时器：15ms 一次，全程在 UI 线程
        self.drag_timer = Timer()
        self.drag_timer.Interval = 15
        self.drag_timer.Tick += self.on_drag_timer

        # 缩放补救定时器：改完窗口尺寸后连着重画几次（见 force_repaint）
        self.repair_timer = Timer()
        self.repair_timer.Interval = REPAIR_SCHEDULE_MS[0]   # 真正的间隔每轮开刷前会按表重设
        self.repair_timer.Tick += self.on_repair_timer

        return form

    def attach_mouse_events(self, form, webview) -> None:
        """分层窗口里，不透明像素上的点击会送到子控件 —— 用系统自己的命中判断最准。

        同时挂到窗体和 WebView 控件上（不同像素可能落在不同 HWND），
        这样右键点在角色身上不会被桌面抢走，点在透明处才会穿透。
        """
        for control in (form, webview):
            control.MouseDown += self.on_ui_mouse_down
            control.MouseMove += self.on_ui_mouse_move
            control.MouseUp += self.on_ui_mouse_up
            control.MouseWheel += self.on_mouse_wheel

    def cursor_screen(self):
        cursor = wt.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        return cursor.x, cursor.y

    def feed_tracker(self, x: float, y: float, left: bool, right: bool, on_char: bool) -> None:
        local_x = x - self.form.Left
        local_y = y - self.form.Top
        on_head = on_char and self.on_head(local_x, local_y)
        for event in self.mouse.feed(x, y, left, right, on_char, on_head, time.time()):
            self.on_mouse_event(event, wt.POINT(int(x), int(y)), time.time())

    def on_ui_mouse_down(self, sender, args) -> None:
        from System.Windows.Forms import MouseButtons

        self.form_input_works = True
        x, y = self.cursor_screen()
        left = args.Button == MouseButtons.Left
        right = args.Button == MouseButtons.Right
        try:
            sender.Capture = True     # 捕获鼠标：拖出窗口也还能收到移动，拖动更顺
        except Exception:
            pass
        self.feed_tracker(x, y, left or self.mouse.left_down, right or self.mouse.right_down, True)

    def on_ui_mouse_move(self, sender, args) -> None:
        if not (self.mouse.left_down or self.mouse.right_down):
            return
        x, y = self.cursor_screen()
        self.feed_tracker(x, y, self.mouse.left_down, self.mouse.right_down, True)

    def on_ui_mouse_up(self, sender, args) -> None:
        from System.Windows.Forms import MouseButtons

        x, y = self.cursor_screen()
        left = args.Button == MouseButtons.Left
        right = args.Button == MouseButtons.Right
        try:
            sender.Capture = False
        except Exception:
            pass
        self.feed_tracker(
            x,
            y,
            self.mouse.left_down and not left,
            self.mouse.right_down and not right,
            True,
        )

    def create_props(self):
        """两个 WebView2 必须用完全相同的用户数据文件夹和启动参数，否则第二个初始化会失败。"""
        from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties

        props = CoreWebView2CreationProperties()
        props.UserDataFolder = os.path.join(HERE, ".webview2")
        props.AdditionalBrowserArguments = (
            "--disable-features=ElasticOverscroll,CalculateNativeWinOcclusion"
            " --disable-background-timer-throttling"
            " --disable-backgrounding-occluded-windows"
            " --disable-renderer-backgrounding"
            " --autoplay-policy=no-user-gesture-required"
        )
        return props

    def on_shown(self, sender, args) -> None:
        self.install_wheel_hook()
        self.desktops = VirtualDesktops()
        self.desktops.follow(self.form.Handle.ToInt64())

    # -- webview plumbing ----------------------------------------------------

    def on_webview_ready(self, sender, args) -> None:
        core = sender.CoreWebView2
        if core is None:
            log("WebView2 初始化失败：%s" % args.InitializationException)
            self.report_fatal("WebView2 初始化失败", args.InitializationException)
            return
        core.Settings.AreDefaultContextMenusEnabled = False
        core.Settings.IsZoomControlEnabled = False
        core.Settings.AreDevToolsEnabled = bool(os.environ.get("CODEXPET_DEVTOOLS"))
        if os.environ.get("CODEXPET_PINGTEST"):
            core.NavigateToString(
                """<!doctype html><meta charset="utf-8"><body style="background:#123">
                <canvas id="c" width="64" height="64"></canvas>
                <script>
                const gl = document.getElementById('c').getContext('webgl');
                gl.clearColor(0.2, 0.9, 0.4, 1.0);
                gl.clear(gl.COLOR_BUFFER_BIT);
                const px = new Uint8Array(4);
                gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
                window.chrome.webview.postMessage(JSON.stringify({
                  t: 'ready',
                  expressions: ['ping'],
                  webgl: !!gl,
                  pixel: Array.from(px),
                  ua: navigator.userAgent.slice(0, 60),
                  inline: true,
                }));
                </script></body>"""
            )
            log("WebView2 就绪（内嵌页面通道自检）")
        else:
            core.Navigate("http://127.0.0.1:%d/index.html" % self.port)
            log("WebView2 就绪，正在加载界面")
        core.NavigationCompleted += self.on_navigation_done

    def report_fatal(self, title: str, detail) -> None:
        """出错时弹一个提示框，但绝不卡住：① 可以用环境变量抑制；② 25 秒后自动关掉。"""
        if os.environ.get("CODEXPET_NO_DIALOG"):
            log("（已抑制错误弹窗）%s：%s" % (title, detail))
            return

        def show() -> None:
            try:
                from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon

                MessageBox.Show(
                    "%s\n\n%s\n\n详细信息见日志：%s" % (title, detail, log_path),
                    "Codex 桌宠",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning,
                )
            except Exception as exc:  # noqa: BLE001
                log("弹出提示失败：%r" % (exc,))

        def auto_close() -> None:
            time.sleep(25)
            try:
                hwnd = user32.FindWindowW(None, "Codex 桌宠")
                if hwnd:
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
            except Exception:
                pass

        threading.Thread(target=show, daemon=True).start()
        threading.Thread(target=auto_close, daemon=True).start()

    def on_navigation_done(self, sender, args) -> None:
        log("页面加载完成：成功=%s 状态=%s" % (args.IsSuccess, args.WebErrorStatus))
        if not args.IsSuccess and not self.ready.is_set():
            self.reload_attempts = getattr(self, "reload_attempts", 0) + 1
            if self.reload_attempts <= 2:
                log("界面没加载起来，2 秒后重试第 %d 次" % self.reload_attempts)

                def retry():
                    time.sleep(2)
                    self.reload_page()

                threading.Thread(target=retry, daemon=True).start()
            else:
                log("界面多次加载失败，请双击 诊断.cmd 查看日志")

    def reload_page(self) -> None:
        """Reload the pet page from any thread (WebView2 objects must be touched on the UI thread)."""
        try:
            from System import Action

            def do():
                core = self.webview.CoreWebView2 if self.webview is not None else None
                if core is None:
                    log("WebView2 还没准备好，稍后再试")
                    return
                core.Reload()

            self.form.BeginInvoke(Action(do))
        except Exception as exc:  # noqa: BLE001
            log("重新加载失败：%r" % (exc,))

    def on_web_message(self, sender, args) -> None:
        try:
            msg = json.loads(args.WebMessageAsJson)
        except Exception:
            return
        kind = msg.get("t")
        if kind == "size":
            rect = msg.get("rect") or [0, 0, 0, 0]
            head = msg.get("headRect") or None
            dpr = float(msg.get("dpr") or 1.0)
            self.char_rect = (rect[0] * dpr, rect[1] * dpr, rect[2] * dpr, rect[3] * dpr)
            self.head_rect = (
                (head[0] * dpr, head[1] * dpr, head[2] * dpr, head[3] * dpr) if head else None
            )
            self.panel_open = bool(msg.get("panel"))
        elif kind == "fps":
            self.render_fps = float(msg.get("value") or 0.0)
            # 顺手转发给设置窗口：让"渲染帧率上限"旁边能显示当前实际帧率
            self.send_to_settings_window({"t": "fps", "value": round(self.render_fps, 1)})
            now = time.time()
            if now - self.fps_log_at > 60:
                self.fps_log_at = now
                log(
                    "渲染 FPS：%.0f（窗口 %dx%d，缩放 %.2f）"
                    % (self.render_fps, self.form.ClientSize.Width, self.form.ClientSize.Height, self.pet_scale)
                )
        elif kind == "over":
            self.over_char = bool(msg.get("value"))
        elif kind == "over-probe":
            self.alpha_probe_ok = bool(msg.get("value"))
            log("像素命中检测：%s" % ("可用" if self.alpha_probe_ok else "不可用，退回角色外框判断"))
        elif kind == "panel":
            self.panel_open = bool(msg.get("open"))
        elif kind == "resize":
            self.resize_anchor = "bottom"      # 展开进度卡片时保持底部不动
            self.resize_window(float(msg.get("w") or WINDOW_W), float(msg.get("h") or WINDOW_H))
        elif kind == "open-settings":
            self.show_settings()
        elif kind == "quit":
            log("收到退出指令")
            from System import Action

            self.form.BeginInvoke(Action(self.form.Close))
        elif kind == "reload":
            self.reload_page()
        elif kind == "open-codex":
            self.focus_codex()
        elif kind == "ready":
            self.ready.set()
            extra = ""
            if msg.get("inline"):
                extra = " webgl=%s 像素=%s" % (msg.get("webgl"), msg.get("pixel"))
            log("界面就绪，形象表情数量：%d%s" % (len(msg.get("expressions") or []), extra))
            self.push_settings()
        elif kind == "status":
            self.status = msg.get("data") or {}
            self.status_ready.set()
        elif kind == "playback-debug":
            data = msg.get("data") or {}
            log(
                "动作硬闸门：%s，清理队列 %s 个（自动动作=%s）"
                % (data.get("reason"), data.get("queued"), data.get("autoMotion"))
            )
        elif kind == "error":
            log("页面报错：%s" % msg.get("message"))
        elif kind == "snapshot":
            self.snapshot = msg.get("data") or ""
            self.snapshot_ready.set()

    def post(self, message: dict) -> None:
        with self.lock:
            self.queue.append(message)

    def flush(self) -> None:
        with self.lock:
            pending, self.queue = self.queue, []
        if not pending or self.webview is None or self.webview.CoreWebView2 is None:
            return
        for message in pending:
            try:
                self.webview.CoreWebView2.PostWebMessageAsJson(json.dumps(message, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001
                log("发送状态失败：%r" % (exc,))

    # -- per-frame work ------------------------------------------------------

    def on_tick(self, sender, args) -> None:
        self.flush()
        self.handle_mouse()
        self.drain_wheel()

        hwnd = self.form.Handle.ToInt64()
        now = time.time()
        for event in self.mouse.tick(now):        # 单击 / 双击的判定要靠定时器推动
            cursor = wt.POINT()
            user32.GetCursorPos(ctypes.byref(cursor))
            self.on_mouse_event(event, cursor, now)
        self.stream_cursor(now)
        if now - self.last_stop_check > 0.5:
            self.last_stop_check = now
            if os.path.exists(STOP_FILE):
                try:
                    os.remove(STOP_FILE)
                except OSError:
                    pass
                log("收到停止请求，退出桌宠")
                self.form.Close()
                return
        if self.desktops and self.settings.get("followDesktop", True) and now - self.last_follow > 0.6:
            self.last_follow = now
            self.desktops.follow(hwnd)

        if self.card is not None and not self.card.IsDisposed and self.card.Visible:
            # 位置**或尺寸**变了都要重新摆卡片（缩放桌宠时最容易挡住它）
            here = (self.form.Left, self.form.Top, self.form.ClientSize.Width, self.form.ClientSize.Height)
            if here != self.last_card_pos:
                self.apply_card_mode()
                self.position_card()

    # 鼠标事件不走网页（透明窗口是分层的，子窗口收不到点击），这里直接读键鼠状态来驱动互动
    def handle_mouse(self) -> None:
        if self.form_input_works:
            return   # 已经能收到真实的窗口鼠标事件，就不再用轮询凑
        cursor = wt.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        left = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
        right = bool(user32.GetAsyncKeyState(0x02) & 0x8000)
        now = time.time()

        width = self.form.ClientSize.Width
        height = self.form.ClientSize.Height
        local_x = cursor.x - self.form.Left
        local_y = cursor.y - self.form.Top
        inside = 0 <= local_x < width and 0 <= local_y < height
        locked = now < self.zoom_lock_until      # 缩放的瞬间不接受拖动，避免互相打架
        on_char = inside and not locked and self.on_character(local_x, local_y)
        on_head = on_char and self.on_head(local_x, local_y)

        for event in self.mouse.feed(cursor.x, cursor.y, left, right, on_char, on_head, now):
            self.on_mouse_event(event, cursor, now)

    def on_head(self, local_x: float, local_y: float) -> bool:
        """头部区域：网页报了头框就用它，否则退回"角色上方 40%"。"""
        rect = self.head_rect or (
            (self.char_rect[0] + self.char_rect[2] * 0.2, self.char_rect[1], self.char_rect[2] * 0.6, self.char_rect[3] * 0.4)
            if self.char_rect
            else None
        )
        if rect is None:
            return False
        rx, ry, rw, rh = rect
        return rx <= local_x <= rx + rw and ry <= local_y <= ry + rh

    def tool_zone(self, local_x: float, local_y: float) -> str | None:
        """底部三个图标（撤回 / 铅笔 / 橡皮擦）的命中区。

        图标是画死在模型图里的，位置固定，所以按实测比例定位（TOOL_MARK_*）。
        千万别按"角色宽度三等分"来分 —— 那三个图标挤在中间偏左，三等分必然错位。
        """
        if not self.char_rect:
            return None
        rx, ry, rw, rh = self.char_rect
        if rw <= 0 or rh <= 0:
            return None
        fx = (local_x - rx) / rw
        fy = (local_y - ry) / rh
        if abs(fy - TOOL_MARK_Y) > TOOL_MARK_HALF_Y:
            return None
        best = None
        best_dx = TOOL_MARK_HALF_X
        for name, mark_x in TOOL_MARK_X:
            dx = abs(fx - mark_x)
            if dx <= best_dx:
                best, best_dx = name, dx
        return best

    def on_mouse_event(self, event: str, cursor, now: float) -> None:
        log("鼠标事件：%s (%d,%d)" % (event, cursor.x, cursor.y))
        local_x = cursor.x - self.form.Left
        local_y = cursor.y - self.form.Top
        tool = self.tool_zone(local_x, local_y)
        if event in ("pat", "poke"):
            if tool:
                self.trigger_bottom_action(tool, "single", cursor)
            else:
                self.post({"t": "react", "kind": "pat" if event == "pat" else "poke"})
        elif event == "cycle":
            if tool:
                # 图标上双击也按单击处理：手感上"点一下就触发"最直接，
                # 万一习惯了双击也不会变成没反应
                self.trigger_bottom_action(tool, "single", cursor)
            else:
                self.post({"t": "cycle-expression"})
        elif event == "menu":
            if tool:
                self.trigger_bottom_action(tool, "right", cursor)
            else:
                self.show_menu(cursor.x, cursor.y)
        elif event == "drag-start":
            if self.position_locked:
                self.post({"t": "say", "text": "位置已锁定"})
                log("位置已锁定，忽略拖动")
                return
            self.start_drag_loop()
        elif event == "drag-end":
            self.finish_drag()   # 兜底：拖动定时器已经在盯着按键状态了

    def on_character(self, local_x: float, local_y: float, margin: float = 10) -> bool:
        """只有点在角色身上才算互动，透明边角就让点击穿过去。"""
        if self.alpha_probe_ok and self.over_char is not None:
            # 有像素级判断就用它：这样右键点空白处不会既弹我们的菜单、又弹桌面菜单
            return bool(self.over_char)
        if self.char_rect is None:
            return True
        rx, ry, rw, rh = self.char_rect
        return (rx - margin) <= local_x <= (rx + rw + margin) and (ry - margin) <= local_y <= (ry + rh + margin)

    # -- 滚轮缩放 ------------------------------------------------------------

    def on_mouse_wheel(self, sender, args) -> None:
        if self._wheel_hook:
            return   # 有全局滚轮钩子时以它为准，避免一次滚动被处理两遍
        steps = args.Delta / 120.0
        if not steps:
            return
        self.zoom_by(steps * 0.06)

    def install_wheel_hook(self) -> None:
        """底层鼠标钩子：窗口没焦点时也能收到滚轮，用来缩放桌宠。

        只在光标位于桌宠上时消费滚轮，避免影响别的程序。
        """
        WH_MOUSE_LL = 14
        WM_MOUSEWHEEL = 0x020A
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)

        def hook(code, wparam, lparam):
            if code >= 0 and wparam == WM_MOUSEWHEEL and self.settings.get("wheelZoom", True):
                try:
                    data = ctypes.cast(lparam, ctypes.POINTER(ctypes.c_ulong))
                    mouse_data = data[2]  # MSLLHOOKSTRUCT.mouseData
                    delta = ctypes.c_short((mouse_data >> 16) & 0xFFFF).value
                    self.pending_wheel += delta
                    self.wheel_at = time.time()
                except Exception:
                    pass
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._hook_proc = HOOKPROC(hook)   # 必须留着引用，否则会被回收
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        self._wheel_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._hook_proc, None, 0)
        log("滚轮钩子：%s" % ("已安装" if self._wheel_hook else "安装失败（改用窗口滚轮事件）"))

    def drain_wheel(self) -> None:
        if not self.pending_wheel:
            return
        delta, self.pending_wheel = self.pending_wheel, 0
        # 只有光标在桌宠（角色像素）上才缩放
        cursor = wt.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        local_x = cursor.x - self.form.Left
        local_y = cursor.y - self.form.Top
        if not self.on_character(local_x, local_y):
            return
        self.zoom_by((delta / 120.0) * 0.06)

    def zoom_by(self, delta: float) -> None:
        low, high = self.scale_limits()
        target = max(low, min(high, self.pet_scale + delta))
        if abs(target - self.pet_scale) < 0.001:
            return
        self.pet_scale = target
        self.settings["scale"] = target
        self.zoom_lock_until = time.time() + 0.45   # 缩放过程中先别拖动，避免互相打架
        self.resize_anchor = "center"               # 以窗口中心为锚点，缩放时不会飞走
        base = (PANEL_W, PANEL_H) if self.panel_open else (WINDOW_W, WINDOW_H)
        self.resize_window(*base)
        settings_store.save(self.settings)
        self.push_settings()                        # 通知渲染层和设置窗口
        log("滚轮缩放：%.0f%%（窗口 %dx%d）" % (target * 100, self.form.ClientSize.Width, self.form.ClientSize.Height))
        self.apply_card_mode()
        if self.card is not None and not self.card.IsDisposed and self.card.Visible:
            self.position_card()

    # -- 拖动：独立线程 + SetWindowPos，不受 110ms 界面定时器限制 -----------------

    def start_drag_loop(self) -> None:
        """拖动窗口：用一个 15ms 的 UI 定时器按光标位移搬运窗口。

        为什么不用另外几种写法：
        * `WM_NCLBUTTONDOWN/HTCAPTION` 的系统移动循环：在这个窗口上会立刻返回（日志实测 0.00s）。
        * 靠 MouseMove 事件搬窗口：一旦某个事件没送到（WebView2 吃掉 / 异常被吞），就完全不动。
        * 独立线程 144Hz：分层窗口反复合成，把系统鼠标都拖死。

        现在这个做法不依赖鼠标事件，只看"光标现在在哪 + 按键还按着吗"，每 15ms 更新一次位置，
        全程在 UI 线程、单线程、无重绘风暴，松手立刻停。
        """
        self.post({"t": "react", "kind": "drag"})
        if self.drag_timer is None:
            log("拖动定时器未初始化，无法拖动")
            return
        anchor = (self.mouse.press_pos[0], self.mouse.press_pos[1], self.form.Left, self.form.Top)
        self.drag_anchor = anchor
        self.drag_button = "left" if self.mouse.press_button == "left" else "right"
        self.drag_started_at = time.time()
        self.drag_moves = 0
        log("开始拖动：锚点 %s（%s 键）" % (anchor, self.drag_button))
        self.drag_timer.Start()

    def on_drag_timer(self, sender, args) -> None:
        anchor = self.drag_anchor
        if not anchor:
            self.drag_timer.Stop()
            return
        vk = 0x01 if self.drag_button == "left" else 0x02
        if not (user32.GetAsyncKeyState(vk) & 0x8000):
            self.finish_drag()
            return
        cursor = wt.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        ax, ay, win_x, win_y = anchor
        try:
            self.form.Location = self.Point(int(win_x + cursor.x - ax), int(win_y + cursor.y - ay))
            self.drag_moves += 1
        except Exception as exc:  # noqa: BLE001
            log("拖动失败：%r" % (exc,))
            self.finish_drag()

    def finish_drag(self) -> None:
        self.drag_timer.Stop()
        anchor = self.drag_anchor
        self.drag_anchor = None
        if not anchor:
            return
        used = max(0.001, time.time() - self.drag_started_at)
        save_position(self.form.Left, self.form.Top)
        self.post({"t": "react", "kind": "drop"})
        log(
            "拖动结束：%.2fs，搬动 %d 次（%.0f 次/秒），位置 %s，渲染 %.0f fps"
            % (used, self.drag_moves, self.drag_moves / used, (self.form.Left, self.form.Top), self.render_fps)
        )

    def apply_manual_drag(self, x: float, y: float) -> None:
        """保留空实现：早期版本靠 MouseMove 搬窗口，现在统一由拖动定时器负责。"""

    def end_manual_drag(self) -> None:
        self.finish_drag()

    def stop_drag_loop(self) -> None:
        self.drag_stop.set()

    WM_NCLBUTTONDOWN = 0x00A1
    HTCAPTION = 2

    def native_drag(self) -> None:
        hwnd = wt.HWND(self.form.Handle.ToInt64())
        started = time.time()
        before = (self.form.Left, self.form.Top)
        monitor = threading.Thread(target=self.monitor_cursor, args=(started,), daemon=True)
        monitor.start()
        try:
            user32.ReleaseCapture()
            user32.SendMessageW(hwnd, self.WM_NCLBUTTONDOWN, self.HTCAPTION, 0)
        except Exception as exc:  # noqa: BLE001
            log("原生拖动失败：%r" % (exc,))
        self.drag_monitor_stop.set()
        seconds = max(0.001, time.time() - started)
        samples = self.drag_samples
        self.drag_samples = 0
        save_position(self.form.Left, self.form.Top)
        self.post({"t": "react", "kind": "drop"})
        log(
            "拖动结束：%.2fs，位置 %s -> %s；期间采样 %d 次、指针位置变化 %d 次"
            "（最大间隔 %.0f ms），渲染 %.0f fps"
            % (seconds, before, (self.form.Left, self.form.Top), samples, self.drag_changes, self.drag_max_gap_ms, self.render_fps)
        )
        self.drag_changes = 0
        self.drag_max_gap_ms = 0.0

    def monitor_cursor(self, started: float) -> None:
        """拖动期间按 ~120Hz 采样指针位置：用来判断系统有没有丢鼠标事件。

        只在独立线程里读 GetCursorPos，不做任何耗时操作。
        """
        hop_to_interactive_desktop()   # 新线程默认在沙箱桌面，不切过去读到的是"假鼠标"
        self.drag_monitor_stop.clear()
        last = None
        last_change = started
        self.drag_changes = 0
        self.drag_max_gap_ms = 0.0
        while not self.drag_monitor_stop.is_set():
            cursor = wt.POINT()
            user32.GetCursorPos(ctypes.byref(cursor))
            now = time.time()
            self.drag_samples += 1
            if last is not None and (cursor.x != last[0] or cursor.y != last[1]):
                self.drag_changes += 1
                gap = (now - last_change) * 1000.0
                if gap > self.drag_max_gap_ms:
                    self.drag_max_gap_ms = gap
                last_change = now
            last = (cursor.x, cursor.y)
            time.sleep(0.008)

    # -- 右键菜单（原生菜单，不受透明窗口影响） --------------------------------

    def toggle_card(self, show=None) -> None:
        """任务进度卡片。

        固定画在桌宠自己的网页里：宽度 CSS 固定、高度由内容自适应，
        窗口尺寸完全不变 —— 既没有改窗口尺寸的卡顿，也不会影响穿透。
        """
        mode = "inline"
        if mode != "window":
            self.inline_card_open = (not self.inline_card_open) if show is None else bool(show)
            self.post({"t": "card", "open": self.inline_card_open})
            log("进度卡片（窗口内渲染）：%s" % ("显示" if self.inline_card_open else "隐藏"))
            return
        if self.card is None or self.card.IsDisposed:
            self.card_want_visible = (not self.card_want_visible) if show is None else bool(show)
            self.spawn_window_thread("card-window", self.build_card)   # 同样跑在自己的 STA 线程上
            return
        target = (not self.card.Visible) if show is None else bool(show)
        self.invoke_on_card(lambda: self.show_card_window(target))

    def invoke_on_card(self, fn) -> None:
        """卡片窗口在它自己的线程上，跨线程操作必须回到那个线程。"""
        form = self.card
        if form is None or form.IsDisposed:
            return
        try:
            from System import Action

            form.BeginInvoke(Action(fn))
        except Exception as exc:  # noqa: BLE001
            log("操作进度卡片窗口失败：%r" % (exc,))

    def show_card_window(self, visible: bool) -> None:
        if self.card is None or self.card.IsDisposed:
            return
        self.apply_card_mode()
        if visible:
            self.update_card(self.last_payload)
            self.position_card()
            self.card.Show()
            self.card.BringToFront()
        else:
            self.card.Hide()
        log("进度卡片（独立窗口）：%s" % ("显示" if visible else "隐藏"))

    def build_card(self) -> None:
        """进度卡片 = 独立窗口，里面还是原来那张 HTML 卡片（好看，且不会改变桌宠尺寸）。"""
        from System.Drawing import Color, Size
        from System.Windows.Forms import DockStyle, Form, FormBorderStyle, FormStartPosition
        from Microsoft.Web.WebView2.WinForms import WebView2

        form = Form()
        form.Text = "任务进度"
        form.FormBorderStyle = getattr(FormBorderStyle, "None")
        form.StartPosition = FormStartPosition.Manual
        form.ShowInTaskbar = False
        form.TopMost = True
        # 关键：窗口背景必须"挖空"，否则毛玻璃/桌面都被它盖住。
        # 键色故意选一个很暗、内容里不会精确出现的颜色：这样即使某些驱动不认
        # 子窗口的 alpha，最差也只是"更暗的卡片"，不会出现洋红边或者镂空。
        key_color = Color.FromArgb(8, 8, 8)
        form.BackColor = key_color
        form.TransparencyKey = key_color
        scale = self.dpi_scale or 1.0
        compact = self.pet_scale < CARD_COMPACT_BELOW
        self.card_compact = compact
        base_w = CARD_COMPACT_W if compact else CARD_W
        base_h = CARD_COMPACT_H if compact else CARD_H
        form.ClientSize = Size(int(round(base_w * scale)), int(round(base_h * scale)))
        self.apply_card_region(form.ClientSize.Width, form.ClientSize.Height)

        webview = WebView2()
        webview.CreationProperties = self.create_props()
        webview.Dock = DockStyle.Fill
        try:
            webview.DefaultBackgroundColor = Color.Transparent   # 让网页的半透明背景透出桌面/毛玻璃
        except Exception:  # noqa: BLE001
            pass
        form.Controls.Add(webview)
        webview.CoreWebView2InitializationCompleted += self.on_card_ready
        webview.WebMessageReceived += self.on_card_message
        webview.EnsureCoreWebView2Async(None)

        self.card = form
        self.card_webview = webview
        form.FormClosed += lambda s, e: setattr(self, "card", None)
        form.Shown += self.on_card_shown
        if getattr(self, "card_want_visible", True):
            self.show_card_window(True)
        return form

    def on_card_shown(self, sender, args) -> None:
        """卡片窗口刚显示时：搬到你当前的虚拟桌面 + 开毛玻璃。"""
        self.enable_acrylic(self.card)
        if self.desktops is not None and self.card is not None and self.form is not None:
            self.desktops.align_to(int(self.card.Handle.ToInt64()), int(self.form.Handle.ToInt64()))

    def enable_acrylic(self, form) -> None:
        """给卡片窗口开毛玻璃，按"成功率从高到低"依次尝试，并把结果写进日志。

        注意：系统设置里如果关掉了"透明效果"，下面几种方式都会**静默失效**，
        所以先读注册表判断，避免让你以为是代码没生效。
        """
        if not self.settings.get("cardBlur", True):
            try:   # 关掉时要主动清掉已经生效的模糊，否则只是"新开的不加"
                accent = ACCENT_POLICY(0, 0, 0, 0)   # ACCENT_DISABLED
                data = WINDOWCOMPOSITIONATTRIBDATA(
                    19, ctypes.cast(ctypes.byref(accent), ctypes.c_void_p), ctypes.sizeof(accent)
                )
                user32.SetWindowCompositionAttribute(ctypes.c_void_p(int(form.Handle.ToInt64())), ctypes.byref(data))
            except Exception:
                pass
            log("卡片毛玻璃：设置里已关闭（只保留半透明，圆角绝对干净）")
            return
        if not self.system_transparency_enabled():
            log(
                "卡片毛玻璃：系统关闭了透明效果，所以看不到模糊 —— "
                "到「设置 → 个性化 → 颜色 → 透明效果」打开即可（卡片本身不受影响）"
            )
        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t),
            ]

        hwnd = ctypes.c_void_p(int(form.Handle.ToInt64()))
        ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
        ACCENT_ENABLE_BLURBEHIND = 3
        WCA_ACCENT_POLICY = 19
        # 色调由网页那层 rgba(25,28,36,0.62) 决定，这里只给一点点底色（alpha 0x30），
        # 免得两层叠起来变成一块死黑的板子。
        tint = (0x30 << 24) | (36 << 16) | (28 << 8) | 25

        # 注意：这里**故意不用** Win11 的 DWMSBT 背景材质（DwmSetWindowAttribute 38 / DwmExtendFrameIntoClientArea）。
        # 它是按"整块窗口矩形"画的，不认窗口的圆角 Region —— 圆角外面会留一圈方形底色，
        # 也就是之前四个角上的那种残留。改用 SetWindowCompositionAttribute：
        # 这类模糊是跟着窗口形状（Region）裁的，圆角外面的模糊会被切掉。
        for state, label in ((ACCENT_ENABLE_ACRYLICBLURBEHIND, "亚克力"), (ACCENT_ENABLE_BLURBEHIND, "模糊")):
            try:
                accent = ACCENT_POLICY(state, 2, tint, 0)
                data = WINDOWCOMPOSITIONATTRIBDATA(
                    WCA_ACCENT_POLICY,
                    ctypes.cast(ctypes.byref(accent), ctypes.c_void_p),
                    ctypes.sizeof(accent),
                )
                ok = user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
                if ok:
                    log("卡片毛玻璃：已开启（%s，旧接口）" % label)
                    return
                log("卡片毛玻璃：%s 接口返回失败" % label)
            except Exception as exc:  # noqa: BLE001
                log("卡片毛玻璃：%s 接口异常 %r" % (label, exc))
        log("卡片毛玻璃：所有方式都没生效，卡片维持半透明实色（不影响使用）")

    def system_transparency_enabled(self) -> bool:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "EnableTransparency")
            return bool(value)
        except Exception:
            return True

    def apply_card_region(self, width: int, height: int) -> None:
        """给窗口本身做圆角：先试 Win11 原生的 DWM 圆角，再用 Region 兜底。

        两条一起用也没问题（Region 是更紧的形状），但系统圆角对 DWM 画的毛玻璃
        裁剪更可靠，所以优先启用。
        """
        try:
            dwm = ctypes.WinDLL("dwmapi")
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            value = ctypes.c_int(DWMWCP_ROUND)
            hr = dwm.DwmSetWindowAttribute(
                ctypes.c_void_p(int(self.card.Handle.ToInt64())),
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            if hr == 0:
                log("卡片圆角：已启用 Win11 系统圆角")
            else:
                log("卡片圆角：系统圆角接口返回 0x%08X，改用 Region 裁剪" % (hr & 0xFFFFFFFF))
        except Exception as exc:  # noqa: BLE001
            log("卡片圆角：系统圆角不可用（%r），改用 Region 裁剪" % (exc,))
        try:
            # 直接用 .NET 画一个圆角路径当窗口区域（比 P/Invoke 的 FromHrgn 稳）
            from System.Drawing import Region
            from System.Drawing.Drawing2D import GraphicsPath

            radius = int(round(CARD_RADIUS * (self.dpi_scale or 1.0)))
            radius = max(2, min(radius, min(width, height) // 2))
            path = GraphicsPath()
            path.AddArc(0, 0, radius, radius, 180, 90)
            path.AddArc(width - radius, 0, radius, radius, 270, 90)
            path.AddArc(width - radius, height - radius, radius, radius, 0, 90)
            path.AddArc(0, height - radius, radius, radius, 90, 90)
            path.CloseFigure()
            self.card.Region = Region(path)
        except Exception as exc:  # noqa: BLE001
            log("设置卡片圆角失败：%r" % (exc,))

    def apply_card_mode(self) -> None:
        """桌宠太小就切紧凑卡片；尺寸变化时重算窗口大小与圆角。"""
        if self.card is None or self.card.IsDisposed:
            return
        compact = self.pet_scale < CARD_COMPACT_BELOW
        scale = self.dpi_scale or 1.0
        base_w = CARD_COMPACT_W if compact else CARD_W
        base_h = CARD_COMPACT_H if compact else CARD_H
        width = int(round(base_w * scale))
        height = int(round(base_h * scale))
        if self.card.ClientSize.Width != width or self.card.ClientSize.Height != height:
            self.card.ClientSize = self.Size(width, height)
            self.apply_card_region(width, height)
        if compact != getattr(self, "card_compact", None):
            self.card_compact = compact
            webview = getattr(self, "card_webview", None)
            if webview is not None and webview.CoreWebView2 is not None:
                try:
                    webview.CoreWebView2.PostWebMessageAsJson(
                        json.dumps({"t": "compact", "value": compact})
                    )
                except Exception as exc:  # noqa: BLE001
                    log("切换卡片紧凑模式失败：%r" % (exc,))

    def on_card_ready(self, sender, args) -> None:
        core = sender.CoreWebView2
        if core is None:
            log("进度卡片窗口初始化失败：%s" % args.InitializationException)
            return
        core.Settings.AreDefaultContextMenusEnabled = False
        core.Navigate("http://127.0.0.1:%d/card.html" % self.port)
        log("进度卡片窗口就绪")
        if getattr(self, "card_compact", False):
            try:
                core.PostWebMessageAsJson(json.dumps({"t": "compact", "value": True}))
            except Exception:
                pass

    def on_card_message(self, sender, args) -> None:
        try:
            msg = json.loads(args.WebMessageAsJson)
        except Exception:
            return
        if msg.get("t") == "close-card":
            self.toggle_card(False)
        elif msg.get("t") == "hello":
            self.schedule_card_update(self.last_payload or {})

    def update_card(self, payload: dict | None) -> None:
        if self.card is None or self.card.IsDisposed:
            return
        webview = getattr(self, "card_webview", None)
        if webview is None or webview.CoreWebView2 is None:
            return
        try:
            from System import Action

            message = json.dumps({"t": "state", "data": payload or {}}, ensure_ascii=False)
            core = webview.CoreWebView2
            if core is not None:
                core.PostWebMessageAsJson(message)
        except Exception as exc:  # noqa: BLE001
            log("更新进度卡片失败：%r" % (exc,))

    def position_card(self) -> None:
        if self.card is None or self.card.IsDisposed:
            return
        from System.Windows.Forms import Screen

        area = Screen.FromControl(self.form).WorkingArea
        width = self.card.ClientSize.Width
        height = self.card.ClientSize.Height
        pet = (self.form.Left, self.form.Top, self.form.ClientSize.Width, self.form.ClientSize.Height)
        area_box = (area.Left, area.Top, area.Right, area.Bottom)
        # 模型在窗口里是底部对齐的，头顶上方还有一段空白。这里不光用"模型外框顶部"，
        # 还往下压 18% 模型高度 —— 也就是允许卡片压住头顶那圈发丝/特效，只要不挡脸。
        if self.char_rect:
            head_offset = card_head_offset(self.char_rect)
        else:
            head_offset = 0
        x, y = compute_card_position(pet, (width, height), area_box, CARD_GAP, head_offset)
        self.card.Location = self.Point(int(x), int(y))
        self.last_card_pos = pet

    def schedule_card_update(self, payload: dict) -> None:
        if self.card is None or self.card.IsDisposed or not self.card.Visible:
            return
        self.invoke_on_card(lambda: self.update_card(payload))

    def show_menu(self, screen_x: int, screen_y: int) -> None:
        from System.Windows.Forms import ContextMenuStrip, ToolStripSeparator, ToolStripMenuItem

        if self.menu is None or self.menu.IsDisposed:
            menu = ContextMenuStrip()
            items = [
                ("显示 / 隐藏任务进度", self.toggle_card),
                ("摸头", lambda: self.post({"t": "react", "kind": "pat"})),
                ("换个表情", lambda: self.post({"t": "cycle-expression"})),
                ("随机动作", lambda: self.post({"t": "react", "kind": "motion"})),
                ("-", None),
                ("设置…", self.show_settings),
                ("切到 Codex 窗口", self.focus_codex),
                ("重新载入形象", self.reload_page),
                ("重画窗口（缩放后出现黑边时点一下）", self.repaint_now),
                ("退出桌宠", lambda: self.form.Close()),
            ]
            for label, handler in items:
                if label == "-":
                    menu.Items.Add(ToolStripSeparator())
                    continue
                item = ToolStripMenuItem(label)
                item.Click += (lambda sender, args, h=handler: h())
                menu.Items.Add(item)
            self.menu = menu
        self.menu.Show(self.Point(screen_x, screen_y))
        log("弹出右键菜单")

    def on_cursor_tick(self, sender, args) -> None:
        """视线的输入就是鼠标位置：~30Hz 送一次，而且当场 flush 出去，不再等下一个 110ms 的 tick。

        （on_tick 也在调 stream_cursor，里面的最小间隔判断会挡住重复的那一次。）
        """
        self.stream_cursor(time.time())
        self.flush()

    def stream_cursor(self, now: float) -> None:
        """Feed the page the real mouse position so the eyes can follow it everywhere on screen."""
        if now - self.last_cursor_send < 0.03:
            return
        cursor = wt.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        scale = self.dpi_scale or 1.0
        local_x = (cursor.x - self.form.Left) / scale
        local_y = (cursor.y - self.form.Top) / scale
        inside = 0 <= local_x <= self.form.ClientSize.Width / scale and 0 <= local_y <= self.form.ClientSize.Height / scale
        moved = self.last_cursor is None or abs(cursor.x - self.last_cursor[0]) + abs(cursor.y - self.last_cursor[1]) > 1
        if not moved and inside == self.last_cursor[2]:
            return
        self.last_cursor = (cursor.x, cursor.y, inside)
        self.last_cursor_send = now
        self.post({"t": "cursor", "data": {"x": round(local_x, 1), "y": round(local_y, 1), "inside": inside}})

    def resize_window(self, w: float, h: float) -> None:
        """窗口贴着角色走。默认右下角固定；缩放桌宠时以窗口中心为锚点，避免"飞走"。"""
        scale = self.dpi_scale * self.pet_scale
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))
        if getattr(self, "Size", None) is None:
            return
        old = self.form.ClientSize
        if abs(old.Width - new_w) < 3 and abs(old.Height - new_h) < 3:
            return
        before = (self.form.Left, self.form.Top, old.Width, old.Height)
        center_x = self.form.Left + old.Width / 2.0
        center_y = self.form.Top + old.Height / 2.0
        anchor = getattr(self, "resize_anchor", "center")
        self.form.ClientSize = self.Size(new_w, new_h)
        if anchor == "bottom":
            right = self.form.Left + old.Width
            bottom = self.form.Top + old.Height
            self.form.Location = self.Point(right - new_w, bottom - new_h)
        else:
            self.form.Location = self.Point(int(center_x - new_w / 2.0), int(center_y - new_h / 2.0))
        log("窗口调整为 %dx%d" % (new_w, new_h))
        after = (self.form.Left, self.form.Top, self.form.ClientSize.Width, self.form.ClientSize.Height)
        self.schedule_repair_repaint(before, after)

    # -- 缩放后的重绘补救（Win11 黑边） ---------------------------------------
    #
    # 现象：滚轮/滑条把桌宠放大缩小时，偶尔会在窗口原来占的地方留一块黑色，
    # 一直挂着不消失（拖一下窗口、或者切一下别的窗口才会掉）。
    #
    # 原因：这个窗口是"整块挖空背景"的透明分层窗口（TransparencyKey + WebView2 透明
    # 背景），Win11 在它改尺寸之后不保证把"旧的那块"和"新露出来的那条边"重画一遍，
    # 于是旧像素就留在屏幕上了。
    #
    # 处理：尺寸改完之后，短时间内连着重画几次 ——
    #   1) 让窗口和里面的网页自己重画一遍；
    #   2) 用 SetWindowPos 催一次 DWM 重新合成；
    #   3) 把"这次缩放涉及的屏幕区域"（旧窗口 ∪ 新窗口）标脏，让桌面也重画那一块。
    # 全都是重绘动作，不改任何功能，也不动鼠标/拖拽/视线的逻辑。

    def schedule_repair_repaint(self, before, after) -> None:
        """记下这次缩放涉及的区域，并安排一串重画。"""
        try:
            now = time.time()
            rect = (
                min(before[0], after[0]),
                min(before[1], after[1]),
                max(before[0] + before[2], after[0] + after[2]),
                max(before[1] + before[3], after[1] + after[3]),
            )
            if self.repair_rect is None or now - self.repair_rect_at > REPAIR_KEEP_SECONDS:
                self.repair_rect = rect
            else:
                prev = self.repair_rect
                self.repair_rect = (
                    min(prev[0], rect[0]), min(prev[1], rect[1]),
                    max(prev[2], rect[2]), max(prev[3], rect[3]),
                )
            self.repair_rect_at = now
            schedule, nudge_ok = repair_schedule()
            self.repair_left = list(schedule)
            self.repair_total = len(schedule)
            self.repair_nudge = nudge_ok
            self.start_repair_timer()
        except Exception as exc:  # noqa: BLE001
            log("安排重画失败（不影响使用）：%r" % (exc,))

    def start_repair_timer(self) -> None:
        if self.repair_timer is None or not self.repair_left:
            return
        self.repair_timer.Stop()
        self.repair_timer.Interval = self.repair_left[0]
        self.repair_timer.Start()

    def on_repair_timer(self, sender, args) -> None:
        if self.repair_timer is not None:
            self.repair_timer.Stop()
        if not self.repair_left:
            return
        self.repair_left.pop(0)
        first = len(self.repair_left) == self.repair_total - 1
        last = not self.repair_left
        # 第一轮先把"窗口原来占的那块桌面"也叫醒；最后两轮再各叫一次（黑边有时出现得晚），
        # 最后一轮顺便把窗口抖一下。
        self.force_repaint(desktop=first or last or len(self.repair_left) == 1, nudge=last and self.repair_nudge)
        self.start_repair_timer()

    def repaint_now(self) -> None:
        """手动重画：万一缩放后还留着黑边，右键菜单点这一条。"""
        form = getattr(self, "form", None)
        if form is None or form.IsDisposed:
            return
        rect = (form.Left, form.Top, form.ClientSize.Width, form.ClientSize.Height)
        self.schedule_repair_repaint(rect, rect)
        log("手动重画窗口（清理黑边）")

    def force_repaint(self, desktop: bool = True, nudge: bool = False) -> None:
        """把桌宠窗口重画一遍；desktop=True 时连"窗口原来占的那块桌面"一起叫醒。

        nudge=True 时还会把窗口位置左右抖 1px 再抖回来 —— 这一下在 DWM 看来是
        一次真实的移动，会强制它把这块彻底重新合成，专门治那种"重画了也不掉"的黑边。
        """
        form = getattr(self, "form", None)
        if form is None or form.IsDisposed:
            return
        try:
            form.Invalidate(True)
            form.Update()
        except Exception:
            pass
        webview = getattr(self, "webview", None)
        if webview is not None:
            try:
                webview.Invalidate()
                webview.Update()
            except Exception:
                pass
        try:
            user32.SetWindowPos(
                ctypes.c_void_p(int(form.Handle.ToInt64())),
                None, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE,
            )
        except Exception:
            pass
        rect = self.repair_rect if desktop else None
        if rect is not None:
            try:
                r = wt.RECT(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
                user32.InvalidateRect(None, ctypes.byref(r), False)
            except Exception:
                pass
        if nudge:
            try:
                hwnd = ctypes.c_void_p(int(form.Handle.ToInt64()))
                x, y = form.Left, form.Top
                flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                user32.SetWindowPos(hwnd, None, x + 1, y + 1, 0, 0, flags)
                user32.SetWindowPos(hwnd, None, x, y, 0, 0, flags)
            except Exception:
                pass

    # -- bottom tool buttons -------------------------------------------------

    def trigger_bottom_action(self, button: str, event: str, cursor) -> None:
        self.release_mouse_capture()
        mode = self.settings.get("bottomButtonsMode", "assistant")
        actions = self.settings.get("bottomButtonActions") or {}
        action = ((actions.get(button) or {}).get(event) or "none")
        if action == "none":
            self.post({"t": "say", "text": "%s：未配置功能" % TOOL_LABELS.get(button, button)})
            return
        label = TOOL_ACTION_LABELS.get(action, action)
        if not self.confirm_action(label, TOOL_LABELS.get(button, button)):
            log("取消底部按钮操作：%s / %s" % (button, label))
            return
        self.last_bottom_action = {"button": button, "event": event, "action": action, "at": time.time()}
        log("底部按钮确认执行：%s / %s / %s" % (button, event, label))
        self.execute_bottom_action(action, cursor)

    def execute_bottom_action(self, action: str, cursor) -> None:
        if action == "open-settings":
            self.show_settings()
        elif action == "open-menu":
            self.show_menu(cursor.x, cursor.y)
        elif action == "toggle-edit-mode":
            self.edit_mode = not self.edit_mode
            self.post({"t": "say", "text": "编辑模式：" + ("开启" if self.edit_mode else "关闭")})
        elif action == "toggle-position-lock":
            self.position_locked = not self.position_locked
            self.post({"t": "say", "text": "位置：" + ("已锁定" if self.position_locked else "已解锁")})
        elif action == "reset-default":
            self.settings["scale"] = 1.0
            self.settings["opacity"] = 1.0
            settings_store.save(self.settings)
            self.invoke_on_pet(self.apply_settings_ui)
            self.reset_position()
            self.post({"t": "say", "text": "已恢复默认状态"})
        elif action == "undo-expression":
            self.post({"t": "button-page-action", "action": "undo-expression"})
        elif action == "clear-card":
            self.post({"t": "button-page-action", "action": "clear-card"})
        elif action == "hide-pet":
            self.hide_to_tray()
        elif action == "mute-animations":
            self.post({"t": "mute", "seconds": 300})
            self.post({"t": "say", "text": "动画已静音 5 分钟"})
        elif action == "codex-focus":
            self.focus_codex()
        elif action == "codex-new-session":
            if self.focus_codex():
                self.send_key_chord("CTRL", "N")
        elif action == "codex-rename-task":
            value = self.prompt_text("重命名当前任务（复制新名称）", "输入新的任务名称（会复制到剪贴板）")
            if value:
                self.set_clipboard(value)
                self.focus_codex()
                self.post({"t": "say", "text": "新任务名已复制"})
        elif action == "codex-add-note":
            value = self.prompt_text("添加备注", "输入要放进 Codex 输入框的备注")
            if value:
                self.focus_codex()
                self.set_clipboard("备注：" + value)
                self.send_key_chord("CTRL", "V")
        elif action == "codex-interrupt":
            if self.focus_codex():
                self.send_key_chord("ESC")
        elif action == "codex-undo-question":
            if self.focus_codex():
                self.send_key_chord("CTRL", "Z")
        elif action == "codex-clear-context":
            if self.focus_codex():
                self.send_key_chord("CTRL", "N")
        elif action == "codex-end-session":
            hwnd = self.focus_codex()
            if hwnd:
                user32.PostMessageW(wt.HWND(hwnd), 0x0010, 0, 0)

    def confirm_action(self, action_label: str, button_label: str) -> bool:
        from System.Drawing import Size
        from System.Windows.Forms import (
            Button,
            DialogResult,
            Form,
            FormBorderStyle,
            FormStartPosition,
            Label,
        )

        form = Form()
        form.Text = "确认底部按钮操作"
        form.ClientSize = Size(430, 155)
        form.StartPosition = FormStartPosition.CenterScreen
        form.TopMost = True
        form.FormBorderStyle = FormBorderStyle.FixedDialog
        form.MaximizeBox = False
        form.MinimizeBox = False
        form.ControlBox = False
        form.ShowInTaskbar = False

        label = Label()
        label.Text = "确定要执行“%s”吗？\n\n按钮：%s" % (action_label, button_label)
        label.SetBounds(16, 14, 398, 58)
        confirm = Button()
        confirm.Text = "确认"
        confirm.DialogResult = DialogResult.OK
        confirm.SetBounds(226, 96, 88, 32)
        cancel = Button()
        cancel.Text = "取消"
        cancel.DialogResult = DialogResult.Cancel
        cancel.SetBounds(326, 96, 88, 32)
        form.Controls.AddRange([label, confirm, cancel])
        form.AcceptButton = confirm
        form.CancelButton = cancel

        def on_closing(sender, args):
            if form.DialogResult.ToString() == "None":
                form.DialogResult = DialogResult.Cancel

        form.FormClosing += on_closing
        try:
            return form.ShowDialog() == DialogResult.OK
        finally:
            form.Dispose()

    def prompt_text(self, title: str, prompt: str) -> str:
        from System.Drawing import Size
        from System.Windows.Forms import (
            Button,
            DialogResult,
            Form,
            FormBorderStyle,
            FormStartPosition,
            Label,
            TextBox,
        )

        form = Form()
        form.Text = title
        form.ClientSize = Size(420, 150)
        form.StartPosition = FormStartPosition.CenterScreen
        form.TopMost = True
        form.FormBorderStyle = FormBorderStyle.FixedDialog
        form.MaximizeBox = False
        form.MinimizeBox = False
        form.ControlBox = False
        form.ShowInTaskbar = False

        label = Label()
        label.Text = prompt
        label.SetBounds(14, 12, 390, 24)
        box = TextBox()
        box.SetBounds(14, 42, 390, 28)
        ok = Button()
        ok.Text = "确定"
        ok.DialogResult = DialogResult.OK
        ok.SetBounds(228, 92, 82, 30)
        cancel = Button()
        cancel.Text = "取消"
        cancel.DialogResult = DialogResult.Cancel
        cancel.SetBounds(322, 92, 82, 30)
        form.Controls.AddRange([label, box, ok, cancel])
        form.AcceptButton = ok
        form.CancelButton = cancel

        def on_closing(sender, args):
            if form.DialogResult.ToString() == "None":
                form.DialogResult = DialogResult.Cancel

        form.FormClosing += on_closing
        try:
            if form.ShowDialog() == DialogResult.OK:
                return box.Text.strip()
        finally:
            form.Dispose()
        return ""

    def set_clipboard(self, text: str) -> None:
        try:
            from System.Windows.Forms import Clipboard

            Clipboard.SetText(text)
        except Exception as exc:  # noqa: BLE001
            log("写入剪贴板失败：%r" % (exc,))

    def send_key_chord(self, *keys: str) -> None:
        vk = {
            "CTRL": 0x11, "SHIFT": 0x10, "ALT": 0x12, "ESC": 0x1B,
            "N": 0x4E, "V": 0x56, "Z": 0x5A,
        }
        codes = [vk[key.upper()] for key in keys if key.upper() in vk]
        for code in codes:
            user32.keybd_event(code, 0, 0, 0)
        for code in reversed(codes):
            user32.keybd_event(code, 0, 2, 0)  # KEYEVENTF_KEYUP

    def release_mouse_capture(self) -> None:
        try:
            self.form.Capture = False
            for control in self.form.Controls:
                control.Capture = False
        except Exception:
            pass

    def hide_to_tray(self) -> None:
        if self.tray_icon is None:
            try:
                from System.Drawing import Icon
                from System.Windows.Forms import ContextMenuStrip, NotifyIcon, ToolStripMenuItem

                tray = NotifyIcon()
                tray.Icon = Icon(os.path.join(WEB, "icon.ico"))
                tray.Text = "Codex 桌宠"
                menu = ContextMenuStrip()
                show_item = ToolStripMenuItem("显示桌宠")
                quit_item = ToolStripMenuItem("退出桌宠")
                show_item.Click += lambda sender, args: self.show_from_tray()
                quit_item.Click += lambda sender, args: self.form.Close()
                menu.Items.Add(show_item)
                menu.Items.Add(quit_item)
                tray.ContextMenuStrip = menu
                tray.Click += lambda sender, args: self.show_from_tray()
                tray.DoubleClick += lambda sender, args: self.show_from_tray()
                self.tray_icon = tray
            except Exception as exc:  # noqa: BLE001
                log("创建系统托盘图标失败：%r" % (exc,))
                return
        self.form.Hide()
        self.tray_icon.Visible = True
        log("桌宠已隐藏到系统托盘")

    def show_from_tray(self) -> None:
        if self.tray_icon is not None:
            self.tray_icon.Visible = False
        self.form.Show()
        self.form.Activate()
        log("桌宠已从系统托盘恢复")

    def reset_position(self) -> None:
        rect = self.screen_area()
        self.form.Location = self.Point(rect[2] - self.form.ClientSize.Width - 24, rect[3] - self.form.ClientSize.Height - 8)
        save_position(self.form.Left, self.form.Top)

    # -- misc ----------------------------------------------------------------

    def focus_codex(self) -> int:
        """Bring the Codex window forward.

        The desktop app ships as the packaged "ChatGPT.exe" under OpenAI.Codex, and its
        window title is just "ChatGPT" — so match on the process image, not the title.
        """
        me = int(self.form.Handle.ToInt64())
        found = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def visit(hwnd, _):
            if int(hwnd) == me or not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            image = self.process_image(hwnd).lower()
            title_l = title.lower()
            score = 0
            if "openai.codex" in image or "\\codex.exe" in image:
                score += 5
            if "chatgpt" in image:
                score += 3
            if "codex" in title_l or "chatgpt" in title_l:
                score += 2
            if score:
                found.append((score, hwnd, title, image))
            return True

        user32.EnumWindows(visit, 0)
        if not found:
            log("没找到 Codex 窗口，可以自己切过去")
            return 0
        found.sort(key=lambda item: -item[0])
        _, hwnd, title, image = found[0]
        user32.ShowWindow(wt.HWND(hwnd), 9)  # SW_RESTORE
        user32.SetForegroundWindow(wt.HWND(hwnd))
        if int(user32.GetForegroundWindow() or 0) != int(hwnd):
            foreground = user32.GetForegroundWindow()
            target_thread = user32.GetWindowThreadProcessId(wt.HWND(hwnd), None)
            current_thread = kernel32.GetCurrentThreadId()
            if foreground:
                user32.AttachThreadInput(user32.GetWindowThreadProcessId(foreground, None), current_thread, True)
            user32.AttachThreadInput(target_thread, current_thread, True)
            user32.SetForegroundWindow(wt.HWND(hwnd))
            if foreground:
                user32.AttachThreadInput(user32.GetWindowThreadProcessId(foreground, None), current_thread, False)
            user32.AttachThreadInput(target_thread, current_thread, False)
        log("已切到 Codex 窗口：%s (%s)" % (title, os.path.basename(image)))
        return int(hwnd)

    def process_image(self, hwnd) -> str:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
        if not pid.value:
            return ""
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(600)
            size = ctypes.c_ulong(600)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(handle)

    # -- settings window -----------------------------------------------------

    def expression_names(self) -> list[str]:
        if self._expressions is None:
            names = []
            try:
                with open(os.path.join(WEB, "model", "settings.json"), encoding="utf-8") as fh:
                    data = json.load(fh)
                # 表情声明在 FileReferences 里面（渲染库要求的位置）；
                # 早期版本写在最外层，这里两种都读，免得又出现"0 个表情"。
                items = (data.get("FileReferences") or {}).get("Expressions") or data.get("Expressions") or []
                for item in items:
                    if isinstance(item, dict):
                        names.append(item.get("Name") or item.get("File") or "")
            except Exception as exc:  # noqa: BLE001
                log("读取表情列表失败：%r" % (exc,))
            self._expressions = [n for n in names if n]
        return self._expressions

    def motion_inventory(self) -> list[dict]:
        """列出模型里所有动作（组 / 序号 / 文件 / 时长），用来在设置里逐条勾选。"""
        if self._motions is not None:
            return self._motions
        items: list[dict] = []
        try:
            with open(os.path.join(WEB, "model", "settings.json"), encoding="utf-8") as fh:
                groups = (json.load(fh).get("FileReferences") or {}).get("Motions") or {}
            for group, motions in groups.items():
                for index, motion in enumerate(motions or []):
                    rel = motion.get("File") or ""
                    duration = 0.0
                    try:
                        with open(os.path.join(WEB, "model", rel.replace("/", os.sep)), encoding="utf-8") as fh:
                            duration = float(((json.load(fh).get("Meta") or {}).get("Duration")) or 0.0)
                    except Exception:
                        pass
                    items.append(
                        {
                            "id": "%s:%d" % (group, index),
                            "group": group,
                            "index": index,
                            "file": os.path.basename(rel),
                            "duration": round(duration, 1),
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            log("读取动作列表失败：%r" % (exc,))
        self._motions = items
        return items

    def prop_parameters(self) -> list[dict]:
        """列出"道具参数"：被动作驱动的、非身体/表情的标准参数（锤子、猫手、手机、泡泡……）。

        这些道具藏在动作曲线里，关表情/关动作都不好使 —— 直接把参数按死为 0 最直接。
        名字取自 cdi3.json（模型作者写的显示名），所以能看出哪个是"锤子出现"。
        """
        if self._props is not None:
            return self._props
        standard_prefix = (
            "ParamAngle", "ParamBodyAngle", "ParamEye", "ParamBrow", "ParamMouth", "ParamBreath",
            "ParamHair", "ParamArm", "ParamHand", "ParamLeg", "ParamShoulder", "ParamNeck",
            "ParamBase", "ParamOpacity", "ParamRotation", "ParamPosition",
        )
        ids: set[str] = set()
        try:
            with open(os.path.join(WEB, "model", "settings.json"), encoding="utf-8") as fh:
                groups = (json.load(fh).get("FileReferences") or {}).get("Motions") or {}
            for motions in groups.values():
                for motion in motions or []:
                    rel = (motion.get("File") or "").replace("/", os.sep)
                    try:
                        with open(os.path.join(WEB, "model", rel), encoding="utf-8") as fh:
                            for curve in json.load(fh).get("Curves") or []:
                                cid = curve.get("Id") or ""
                                if cid and not cid.startswith(standard_prefix):
                                    ids.add(cid)
                    except Exception:
                        continue
            names = {}
            try:
                with open(os.path.join(WEB, "model", "c_0120.cdi3.json"), encoding="utf-8") as fh:
                    for item in json.load(fh).get("Parameters") or []:
                        names[item.get("Id")] = item.get("Name") or ""
            except Exception:
                pass
            ids.discard("chehui")  # 底部工具按钮，不属于要屏蔽的道具
            ids.discard("bi")
            ids.discard("pi")
            items = [
                {"id": pid, "name": names.get(pid) or pid, "hammer": "锤" in (names.get(pid) or "")}
                for pid in sorted(ids, key=lambda p: (0 if "锤" in (names.get(p) or "") else 1, p))
            ]
        except Exception as exc:  # noqa: BLE001
            log("读取道具参数失败：%r" % (exc,))
            items = []
        self._props = items
        return items

    def show_settings(self) -> None:
        """设置窗口跑在独立 STA 线程上。

        之前是在右键菜单的回调里直接建窗口，那条路径上线程公寓被当成 MTA，
        WebView2 会报 `RPC_E_CHANGED_MODE：无法在设置线程模式后对其加以更改`。
        每个窗口起一个自己的 STA 线程（并在里面自己 CoInitializeEx）就彻底绕开了。
        """
        if self.settings_window is not None and not self.settings_window.IsDisposed:
            try:
                self.settings_window.Activate()
                self.settings_window.BringToFront()
            except Exception:
                pass
            return
        self.spawn_window_thread("settings-window", self.build_settings_window)

    def spawn_window_thread(self, name: str, build) -> None:
        def run() -> None:
            init_sta()
            import clr

            clr.AddReference("System.Windows.Forms")
            from System.Windows.Forms import Application

            try:
                form = build()
            except Exception as exc:  # noqa: BLE001
                log("%s 创建失败：%r" % (name, exc))
                return
            if form is None:
                return
            Application.Run(form)

        threading.Thread(target=run, name=name, daemon=True).start()

    def build_settings_window(self):
        from System.Drawing import Size
        from System.Windows.Forms import DockStyle, Form, FormStartPosition
        from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties, WebView2

        form = Form()
        form.Text = "Codex 桌宠 · 设置"
        form.ClientSize = Size(580, 680)
        form.StartPosition = FormStartPosition.CenterScreen
        form.MinimumSize = Size(520, 520)
        form.TopMost = True          # 先置顶，避免被 Codex/浏览器窗口压在下面
        form.ShowInTaskbar = True    # 也在任务栏留一个，找不到时能点出来
        try:
            form.Icon = self.form.Icon
        except Exception:  # noqa: BLE001
            pass

        webview = WebView2()
        webview.CreationProperties = self.create_props()
        webview.Dock = DockStyle.Fill
        form.Controls.Add(webview)
        webview.CoreWebView2InitializationCompleted += self.on_settings_ready
        webview.WebMessageReceived += self.on_settings_message
        webview.EnsureCoreWebView2Async(None)

        self.settings_window = form
        self.settings_webview = webview
        form.FormClosed += self.on_settings_closed
        form.Show()
        form.Activate()
        form.BringToFront()
        # 关键：新窗口默认开在"进程启动时那个桌面"上。你切过虚拟桌面的话，
        # 窗口会开在你看不见的那个桌面上 —— 这里把它搬到你现在看的桌面。
        desktops = self.desktops or VirtualDesktops()
        pet_form = getattr(self, "form", None)
        if pet_form is not None:
            desktops.align_to(int(form.Handle.ToInt64()), int(pet_form.Handle.ToInt64()))
        log("打开设置窗口（置顶已开启；虚拟桌面已同步）")
        return form

    def on_settings_closed(self, sender, args) -> None:
        self.settings_window = None

    def on_settings_ready(self, sender, args) -> None:
        core = sender.CoreWebView2
        if core is None:
            log("设置窗口初始化失败：%s" % args.InitializationException)
            self.report_fatal("设置窗口初始化失败", args.InitializationException)
            return
        core.Settings.AreDefaultContextMenusEnabled = False
        core.Navigate("http://127.0.0.1:%d/settings.html" % self.port)
        log("设置窗口 WebView2 就绪，正在加载界面")

    def on_settings_message(self, sender, args) -> None:
        try:
            msg = json.loads(args.WebMessageAsJson)
        except Exception:
            return
        kind = msg.get("t")
        if kind == "hello":
            self.push_settings()
        elif kind == "save":
            self.apply_settings(msg.get("data") or {})
        elif kind == "action":
            self.run_action(msg.get("name") or "")

    def push_settings(self) -> None:
        payload = dict(self.settings)
        payload["expressions"] = dict(self.settings.get("expressions") or {})
        low, high = self.scale_limits()
        payload["scaleMin"] = low
        payload["scaleMax"] = high
        payload["avatars"] = []
        payload["availableExpressions"] = self.expression_names()
        payload["motions"] = self.motion_inventory()
        payload["baseSize"] = [WINDOW_W, WINDOW_H]
        payload["petNow"] = dict(self.status or {})   # 桌宠此刻在演什么，设置里能看到
        payload["renderFps"] = round(self.render_fps, 1)   # 给"渲染帧率上限"旁边显示当前帧率
        payload["allowed"] = {
            "motions": self.allowed_motion_count(),
            "motionTotal": len(self.motion_inventory()),
            "expressions": self.allowed_expression_count(),
            "expressionTotal": len(self.expression_names()),
        }
        payload["logPath"] = log_path
        payload["home"] = HERE
        payload["autostart"] = os.path.exists(startup_link_path())
        payload["followCodex"] = codex_follow_enabled()
        message = {"t": "settings", "data": payload}
        self.post(message)
        self.send_to_settings_window(message)

    def send_to_settings_window(self, message: dict) -> None:
        """设置窗口在别的线程上，跨线程碰 WebView2 必须回到它自己的线程（BeginInvoke）。"""
        form = self.settings_window
        webview = self.settings_webview
        if form is None or webview is None or form.IsDisposed:
            return
        try:
            from System import Action

            payload = json.dumps(message, ensure_ascii=False)

            def do():
                core = webview.CoreWebView2
                if core is not None:
                    core.PostWebMessageAsJson(payload)

            form.BeginInvoke(Action(do))
        except Exception as exc:  # noqa: BLE001
            log("向设置窗口发送配置失败：%r" % (exc,))

    def scale_limits(self) -> tuple[float, float]:
        """最小约 48x48（桌面图标大小）；最大至少给到 SCALE_MAX_WISH（160%）。

        早先的算法是"角色不超过半屏"（面积约 1/4 屏幕），屏幕不高的机器上只能放大到
        120%~140%；现在改成：先按半屏算，但至少给到 160%，再用"整个窗口要能放进屏幕"
        兜底（不然窗口上方留给进度卡片的那块会跑到屏幕外面去）。
        """
        from System.Windows.Forms import Screen

        area = Screen.PrimaryScreen.WorkingArea
        dpi = self.dpi_scale or 1.0
        screen_w = area.Width / dpi
        screen_h = area.Height / dpi
        # 纵向按"角色本身的高度"算，而不是整个窗口（窗口上方那块是留给进度卡片的，
        # 按窗口算会把放大上限压到 100%，等于没法放大）
        half_screen = min(screen_w * 0.5 / WINDOW_W, screen_h * 0.5 / CHAR_BASE_H)
        # 兜底"别大得离谱"：允许整个窗口比屏幕高出 20% —— 窗口上方那块是空的（留给卡片），
        # 角色本身贴着底边画，所以顶出去一点不影响看角色，卡片也会自动贴回屏幕内。
        too_big = min(screen_w / WINDOW_W, screen_h / (WINDOW_H * 0.8))
        max_scale = min(max(half_screen, SCALE_MAX_WISH), max(1.0, too_big))
        min_scale = max(ICON_SIZE / float(WINDOW_W), ICON_SIZE / float(WINDOW_H))
        return round(min_scale, 3), round(max(1.0, min(max_scale, 4.0)), 2)

    def apply_settings(self, incoming: dict) -> None:
        """保存设置。注意：这个函数可能跑在设置窗口的线程上，
        所以所有"碰窗口"的动作都要回到桌宠自己的 UI 线程去做（跨线程会让界面卡死）。"""
        merged = dict(self.settings)
        merged.update({k: v for k, v in (incoming or {}).items() if k != "availableExpressions"})
        low, high = self.scale_limits()
        try:
            merged["scale"] = max(low, min(high, float(merged.get("scale", 1.0))))
        except (TypeError, ValueError):
            merged["scale"] = 1.0
        self.settings = settings_store.save(merged)
        # 开机自启这类"外部动作"跟着保存一起生效（设置界面现在改成点保存才应用）
        want_autostart = bool(merged.get("autostart", os.path.exists(startup_link_path())))
        if want_autostart != os.path.exists(startup_link_path()):
            self.toggle_autostart(want_autostart)
        want_follow = bool(merged.get("followCodex", codex_follow_enabled()))
        if want_follow != codex_follow_enabled():
            self.toggle_codex_follow(want_follow)
        self.invoke_on_pet(self.apply_settings_ui)
        log(
            "设置已保存：缩放 %.2f / 跟随桌面 %s / 置顶 %s / 自动表情 %s / 自动动作 %s / 跟随Codex启动 %s / 动作 %d/%d / 表情 %d/%d"
            % (
                self.settings.get("scale", 1.0),
                self.settings.get("followDesktop"),
                self.settings.get("alwaysOnTop"),
                self.settings.get("autoExpression"),
                self.settings.get("autoMotion"),
                codex_follow_enabled(),
                self.allowed_motion_count(),
                len(self.motion_inventory()),
                self.allowed_expression_count(),
                len(self.expression_names()),
            )
        )
        self.push_settings()

    def invoke_on_pet(self, fn) -> None:
        """把动作丢回桌宠的 UI 线程执行（跨线程碰 WinForms 会让界面无响应）。"""
        form = getattr(self, "form", None)
        if form is None:
            fn()
            return
        try:
            from System import Action

            form.BeginInvoke(Action(fn))
        except Exception:
            fn()

    def apply_settings_ui(self) -> None:
        try:
            if getattr(self, "form", None) is None:
                return
            global verbose_log
            verbose_log = bool(self.settings.get("verboseLog", False))
            self.form.TopMost = bool(self.settings.get("alwaysOnTop", True))
            self.form.Opacity = float(self.settings.get("opacity", 1.0))
            self.pet_scale = float(self.settings.get("scale", 1.0))
            base = (PANEL_W, PANEL_H) if self.panel_open else (WINDOW_W, WINDOW_H)
            self.resize_window(*base)
            if self.card is not None and not self.card.IsDisposed:
                self.invoke_on_card(lambda: self.enable_acrylic(self.card))
        except Exception as exc:  # noqa: BLE001
            log("应用窗口设置失败：%r" % (exc,))

    def allowed_motion_count(self) -> int:
        allowed = self.settings.get("motionAllowed")
        if not isinstance(allowed, list):
            allowed = ["*"]
        total = len(self.motion_inventory())
        if "*" in allowed:
            return total
        return len([m for m in self.motion_inventory() if m["id"] in allowed])

    def allowed_expression_count(self) -> int:
        allowed = self.settings.get("expressionAllowed")
        if not isinstance(allowed, list):
            allowed = ["*"]
        total = len(self.expression_names())
        if "*" in allowed:
            return total
        return len([n for n in self.expression_names() if n in allowed])

    def run_action(self, name: str) -> None:
        if name == "quit":
            self.form.Close()
        elif name == "reload":
            self.reload_page()
        elif name == "open-log":
            os.startfile(log_path)  # noqa: S606
        elif name == "open-folder":
            os.startfile(HERE)  # noqa: S606
        elif name == "open-codex":
            self.focus_codex()
        elif name == "toggle-panel":
            self.toggle_card()
        elif name == "reset-position":
            rect = self.screen_area()
            self.form.Location = self.Point(rect[2] - self.form.ClientSize.Width - 24, rect[3] - self.form.ClientSize.Height - 8)
            save_position(self.form.Left, self.form.Top)
            log("桌宠位置已重置到右下角")
        elif name in ("autostart-on", "autostart-off"):
            self.toggle_autostart(name.endswith("on"))
        elif name == "clear-log":
            cleared = []
            for path in (log_path, os.path.join(HERE, "watcher.log")):
                try:
                    if os.path.exists(path):
                        open(path, "w", encoding="utf-8").close()
                        cleared.append(os.path.basename(path))
                except OSError:
                    pass
            log("已清空日志：%s" % (", ".join(cleared) or "无"), force=True)
            self.push_settings()
        elif name == "motion":
            self.post({"t": "say", "text": "随机动作"})

    def screen_area(self):
        from System.Windows.Forms import Screen

        area = Screen.PrimaryScreen.WorkingArea
        return (area.Left, area.Top, area.Right, area.Bottom)

    def toggle_codex_follow(self, enable: bool) -> None:
        """调用原有的跟随 Codex 启动开/关脚本，把状态真正写进 Windows 启动项。"""
        script = os.path.join(HERE, "跟随Codex启动-开启.cmd" if enable else "跟随Codex启动-关闭.cmd")
        if not os.path.exists(script):
            log("找不到跟随 Codex 启动脚本：%s" % script)
            return
        try:
            import subprocess

            creation = 0x08000000  # CREATE_NO_WINDOW
            subprocess.run(
                ["cmd.exe", "/d", "/c", script],
                cwd=HERE,
                creationflags=creation,
                timeout=25,
            )
            done = codex_follow_enabled() == bool(enable)
            log("跟随 Codex 启动已%s%s" % ("开启" if enable else "关闭", "" if done else "（未能确认启动项状态）"))
        except Exception as exc:  # noqa: BLE001
            log("切换跟随 Codex 启动失败：%r" % (exc,))
        self.push_settings()

    def toggle_autostart(self, enable: bool) -> None:
        script = os.path.join(HERE, "pet-launcher.ps1")
        flag = "-Install" if enable else "-Uninstall"
        target = startup_link_path()
        if enable and not os.path.exists(script):
            log("找不到 pet-launcher.ps1，无法设置开机自启")
            return
        try:
            import subprocess

            creation = 0x08000000  # CREATE_NO_WINDOW
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, flag],
                cwd=HERE,
                creationflags=creation,
                timeout=30,
            )
            done = os.path.exists(target) if enable else not os.path.exists(target)
            log("开机自启已%s%s" % ("开启" if enable else "关闭", "" if done else "（未能确认，请查看 startup 文件夹）"))
        except Exception as exc:  # noqa: BLE001
            log("切换开机自启失败：%r" % (exc,))
        self.push_settings()

    def run_selftest(self) -> None:
        """Load the UI for real, then ask the page what it thinks it is."""
        deadline = time.time() + self.selftest
        while time.time() < deadline:
            if self.ready.is_set():
                break
            time.sleep(0.2)
        time.sleep(3.0)
        self.post({"t": "status-request"})
        self.status_ready.wait(6.0)
        result = dict(self.status or {"error": "界面没有回报状态"})
        if self.shot:
            self.post({"t": "snapshot-request"})
            if self.snapshot_ready.wait(10.0) and self.snapshot.startswith("data:image/png;base64,"):
                raw = base64.b64decode(self.snapshot.split(",", 1)[1])
                with open(self.shot, "wb") as fh:
                    fh.write(raw)
                result["snapshot"] = self.shot
                result["snapshotBytes"] = len(raw)
            else:
                result["snapshot"] = "failed"

        hwnd = self.form.Handle.ToInt64()
        result["hwnd"] = hwnd
        result["visible"] = bool(user32.IsWindowVisible(wt.HWND(hwnd)))
        result["desktop"] = desktop_name(user32.GetThreadDesktop(kernel32.GetCurrentThreadId()))
        result["exstyle"] = hex(user32.GetWindowLongW(wt.HWND(hwnd), GWL_EXSTYLE) & 0xFFFFFFFF)
        result["virtualDesktop"] = self.desktops.guid_text(self.desktops.desktop_of(hwnd)) if self.desktops else "?"
        result["client"] = [self.form.ClientSize.Width, self.form.ClientSize.Height]
        result["location"] = [self.form.Left, self.form.Top]
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        log("自检完成")


# -------------------------------------------------------------------- state feed


def state_loop(host: PetHost, interval: float = 1.0) -> None:
    import codex_state

    last_payload = None
    last_send = 0.0
    while True:
        try:
            payload = codex_state.snapshot()
        except Exception as exc:  # noqa: BLE001
            log("读取 Codex 状态失败：%r" % (exc,))
            payload = {"state": "idle", "label": "空闲", "title": "", "activity": ""}
        trimmed = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        host.last_payload = payload
        host.schedule_card_update(payload)
        now = time.time()
        if trimmed != last_payload or now - last_send > 15:
            host.post({"t": "state", "data": payload})
            last_payload = trimmed
            last_send = now
        else:
            host.post({"t": "tick", "data": {"elapsed": payload.get("elapsed"), "state": payload.get("state")}})
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex 桌宠")
    parser.add_argument("--selftest", type=float, default=0, help="加载界面 N 秒后自动截取状态并退出")
    parser.add_argument("--state-only", action="store_true", help="只打印状态，不开窗口")
    parser.add_argument("--shot", default="", help="把形象画布导出成 PNG（自检用）")
    args = parser.parse_args()

    if args.state_only:
        import codex_state

        print(json.dumps(codex_state.snapshot(), ensure_ascii=False, indent=2))
        return 0

    if not single_instance():
        print("Codex 桌宠已经在运行了", flush=True)
        return 0

    global verbose_log
    verbose_log = bool(settings_store.load().get("verboseLog", False))

    try:
        with open(PID_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        if os.path.exists(STOP_FILE):
            os.remove(STOP_FILE)
    except OSError:
        pass

    set_dpi_awareness()
    install_exception_logging()
    port = start_server()

    result = {}

    def gui() -> None:
        if not os.environ.get("CODEXPET_NO_HOP"):
            hop_to_interactive_desktop()
        init_sta()
        import clr

        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import Application

        host = PetHost(port, args.selftest, args.shot)
        form = host.build()
        threading.Thread(target=state_loop, args=(host,), daemon=True).start()
        if args.selftest:
            threading.Thread(target=lambda: (time.sleep(1.0), host.run_selftest(), Application.Exit()), daemon=True).start()
        Application.Run(form)
        result["done"] = True

    thread = threading.Thread(target=gui)
    thread.start()
    thread.join()
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())










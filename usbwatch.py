# Copyright 2026 Joseph Costarella (AxialForge)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
USBSentry - a simple USB peripheral monitor for Windows 11.

- Lists all currently connected USB peripherals and their details.
- Watches in the background and alerts when a NEW device is connected
  (and notes when one is removed).
- Can alert only for UNRECOGNIZED devices: trusts everything present at first
  run, learns models (VID:PID) you approve, and flags anything unfamiliar.
- Alerts three ways, each toggleable: Windows toast, in-app banner + row
  highlight, and a sound.
- Light / dark / follow-Windows theming, adjustable text size, search filter,
  and per-device hiding.
- Lives in a normal window that hides to the system tray when closed;
  quit from the tray menu or the Quit button.

Requires: pystray, Pillow (Tkinter ships with Python on Windows).
"""

import csv
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import winreg
import winsound
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import pystray
from PIL import Image, ImageDraw

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

APP_NAME = "USBSentry"
APP_VERSION = "1.2.0"

# Where to keep config / history / known-devices files.
# When frozen by PyInstaller, __file__ lives in a temp extraction dir that is
# deleted on exit, so we must use the folder that actually holds the .exe.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    # Bundled read-only assets live in the PyInstaller extraction dir.
    RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = BASE_DIR


def resource_path(*parts):
    """Absolute path to a bundled asset (works both frozen and from source)."""
    return os.path.join(RESOURCE_DIR, *parts)


def set_app_id():
    """Give Windows an explicit app identity so the taskbar shows OUR icon
    (instead of pythonw.exe's) and groups the app under its own name."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AxialForge.USBSentry")
    except Exception:
        pass


def set_dark_titlebar(win, dark):
    """Toggle the Windows immersive dark title bar for a Tk window/dialog."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # 20 = Win11 / late Win10, 19 = earlier builds
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


# -- Run-on-boot (registry Run key) ------------------------------------------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "USBSentry"


def _autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    return f'"{pyw}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY)
        try:
            winreg.QueryValueEx(k, _RUN_NAME)
            return True
        finally:
            winreg.CloseKey(k)
    except OSError:
        return False


def set_autostart(enable):
    try:
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY)
    except OSError:
        return
    try:
        if enable:
            winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(k, _RUN_NAME)
            except OSError:
                pass
    finally:
        winreg.CloseKey(k)

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
HISTORY_PATH = os.path.join(BASE_DIR, "history.csv")
KNOWN_PATH = os.path.join(BASE_DIR, "known_devices.json")
HISTORY_HEADER = ["timestamp", "action", "device", "type", "vid_pid", "instance_id"]

DEFAULT_CONFIG = {
    "poll_seconds": 3,          # how often to re-scan the USB bus
    "alert_toast": True,        # Windows notification
    "alert_banner": True,       # in-app banner + row highlight
    "alert_sound": True,        # play a chime
    "show_hubs": False,         # include root hubs / internal USB devices
    "unrecognized_only": True,  # alert only for devices not in the known list
    "theme": "system",          # "system" | "light" | "dark"
    "text_size": "Normal",      # Small | Normal | Large | Extra-large
    "hidden": [],               # device_keys hidden from the list
    "sound_name": "Chime",      # which alert sound to play
    "watched": [],              # device_keys to always announce on reconnect
}

TEXT_SIZES = {"Small": 8, "Normal": 10, "Large": 12, "Extra-large": 14}

# Alert sounds (Windows system beeps). "None" is silent.
SOUNDS = {
    "Chime": winsound.MB_ICONASTERISK,
    "Ding": winsound.MB_OK,
    "Exclamation": winsound.MB_ICONEXCLAMATION,
    "Critical": winsound.MB_ICONHAND,
    "None": None,
}


def play_sound(name):
    flag = SOUNDS.get(name, winsound.MB_ICONASTERISK)
    if flag is None:
        return
    try:
        winsound.MessageBeep(flag)
    except RuntimeError:
        pass


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# Theme palettes
# ----------------------------------------------------------------------------

PALETTES = {
    "light": {
        "bg": "#f0f0f0", "surface": "#ffffff", "fg": "#1a1a1a", "muted": "#555555",
        "tree_bg": "#ffffff", "tree_fg": "#1a1a1a",
        "sel_bg": "#2f6ec8", "sel_fg": "#ffffff",
        "heading_bg": "#e4e4e4", "heading_fg": "#1a1a1a",
        "banner_bg": "#2f6ec8", "banner_fg": "#ffffff",
        "alert_bg": "#d9822b", "unrec_bg": "#c0392b",
        "row_new_bg": "#fff2b2", "row_new_fg": "#1a1a1a",
        "row_untrusted_bg": "#f8c9c4", "row_untrusted_fg": "#1a1a1a",
        "entry_bg": "#ffffff", "entry_fg": "#1a1a1a",
        "accent": "#2f6ec8", "border": "#c4c4c4",
    },
    "dark": {
        "bg": "#1e1e1e", "surface": "#2a2a2b", "fg": "#e6e6e6", "muted": "#9a9a9a",
        "tree_bg": "#252526", "tree_fg": "#e6e6e6",
        "sel_bg": "#3d6ea5", "sel_fg": "#ffffff",
        "heading_bg": "#333335", "heading_fg": "#e6e6e6",
        "banner_bg": "#274b73", "banner_fg": "#ffffff",
        "alert_bg": "#b5701f", "unrec_bg": "#a5342a",
        "row_new_bg": "#4a4326", "row_new_fg": "#f4ead0",
        "row_untrusted_bg": "#5a2b28", "row_untrusted_fg": "#f4cfcb",
        "entry_bg": "#333335", "entry_fg": "#e6e6e6",
        "accent": "#4d8fd6", "border": "#3a3a3d",
    },
}


def resolve_theme(mode):
    """Turn the configured theme mode into a concrete 'light'/'dark'."""
    if mode in ("light", "dark"):
        return mode
    # "system": read the Windows apps theme preference.
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return "light" if val == 1 else "dark"
    except OSError:
        return "light"


# ----------------------------------------------------------------------------
# Known ("trusted") devices — identified by model, i.e. VID:PID
# ----------------------------------------------------------------------------

def device_key(dev):
    """Identity used for the known-devices list: the device model (VID:PID).

    Falls back to the full instance ID for the rare device with no VID:PID.
    """
    return dev.get("vid_pid") or dev.get("instance_id", "")


def load_known():
    """Return {device_key: friendly_name} of trusted device models."""
    try:
        with open(KNOWN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, ValueError):
        pass
    return {}


def save_known(known):
    try:
        with open(KNOWN_PATH, "w", encoding="utf-8") as f:
            json.dump(known, f, indent=2)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# USB enumeration (via PowerShell's Get-PnpDevice)
# ----------------------------------------------------------------------------

_PS_QUERY = (
    "Get-PnpDevice -PresentOnly | "
    "Where-Object { $_.InstanceId -like 'USB\\*' } | "
    "Select-Object FriendlyName, Class, Status, Manufacturer, InstanceId | "
    "ConvertTo-Json -Compress -Depth 3"
)

_VIDPID_RE = re.compile(r"VID_([0-9A-Fa-f]{4}).*?PID_([0-9A-Fa-f]{4})")
_COM_RE = re.compile(r"\((COM\d+)\)")
_HUB_RE = re.compile(r"root_hub|usb.*hub|generic usb hub", re.IGNORECASE)

# USB vendor IDs commonly used by dev-board serial bridges (for a friendly hint).
_SERIAL_VIDS = {
    "303A": "Espressif (ESP32)", "10C4": "Silicon Labs CP210x",
    "1A86": "WCH CH340", "0403": "FTDI", "2341": "Arduino", "2E8A": "Raspberry Pi",
}

_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def _run_powershell(script):
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        startupinfo=startupinfo,
        creationflags=_NO_WINDOW,
        timeout=20,
    )
    return proc.stdout


def is_hub(name, instance_id):
    text = f"{name} {instance_id}"
    return bool(_HUB_RE.search(text)) or "ROOT_HUB" in instance_id.upper()


def get_devices(show_hubs):
    """Return {instance_id: device_dict} for currently connected USB devices."""
    try:
        out = _run_powershell(_PS_QUERY)
    except (subprocess.SubprocessError, OSError):
        return {}

    out = (out or "").strip()
    if not out:
        return {}

    try:
        data = json.loads(out)
    except ValueError:
        return {}

    if isinstance(data, dict):
        data = [data]

    devices = {}
    for item in data:
        instance_id = (item.get("InstanceId") or "").strip()
        if not instance_id:
            continue
        name = (item.get("FriendlyName") or "Unknown device").strip()
        if not show_hubs and is_hub(name, instance_id):
            continue

        m = _VIDPID_RE.search(instance_id)
        vid_pid = f"{m.group(1).upper()}:{m.group(2).upper()}" if m else ""
        vid = m.group(1).upper() if m else ""

        cm = _COM_RE.search(name)
        com_port = cm.group(1) if cm else ""

        devices[instance_id] = {
            "instance_id": instance_id,
            "name": name,
            "class": (item.get("Class") or "").strip(),
            "status": (item.get("Status") or "").strip(),
            "manufacturer": (item.get("Manufacturer") or "").strip(),
            "vid_pid": vid_pid,
            "com_port": com_port,
            "drive": "",  # populated later for USB storage
            "board_hint": _SERIAL_VIDS.get(vid, ""),
        }
    return devices


# ----------------------------------------------------------------------------
# Tray icon image
# ----------------------------------------------------------------------------

def make_tray_image(alert=False):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = (220, 90, 60) if alert else (40, 110, 200)
    d.ellipse((4, 4, 60, 60), fill=bg)
    d.rectangle((28, 14, 36, 40), fill="white")
    d.polygon((26, 40, 38, 40, 32, 50), fill="white")
    d.ellipse((29, 9, 35, 15), fill="white")
    d.rectangle((22, 22, 26, 26), fill="white")
    d.rectangle((38, 28, 42, 32), fill="white")
    return img


def load_tray_images():
    """Return (normal, alert) tray images from the bundled icon, with a fallback."""
    try:
        base = Image.open(resource_path("assets", "USBSentry_256.png")).convert("RGBA")
    except Exception:
        return make_tray_image(False), make_tray_image(True)
    alert = base.copy()
    d = ImageDraw.Draw(alert)
    w = base.width
    d.ellipse((w * 0.52, w * 0.52, w * 0.97, w * 0.97), fill=(214, 40, 40, 255),
              outline=(255, 255, 255, 255), width=max(2, w // 40))
    return base, alert


# ----------------------------------------------------------------------------
# Main application
# ----------------------------------------------------------------------------

class USBWatchApp:
    def __init__(self):
        set_app_id()  # taskbar shows our icon, not pythonw.exe's
        self.cfg = load_config()
        self.devices = {}          # current snapshot {instance_id: dict}
        self.baseline_ready = False
        self.new_ids = set()
        self.events = queue.Queue()
        self._stop = threading.Event()

        self.known_file_existed = os.path.exists(KNOWN_PATH)
        self.known = load_known()
        self.hidden = set(self.cfg.get("hidden", []))
        self.watched = set(self.cfg.get("watched", []))

        # Resolve theme + base font size.
        self.theme = resolve_theme(self.cfg.get("theme", "system"))
        self.palette = PALETTES[self.theme]
        self.base = TEXT_SIZES.get(self.cfg.get("text_size", "Normal"), 10)

        self._ensure_history_file()
        self._build_ui()
        self.apply_theme()
        self._build_tray()

        self.worker = threading.Thread(target=self._scan_loop, daemon=True)
        self.worker.start()
        self.root.after(200, self._pump_events)

    # -- UI ------------------------------------------------------------------

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("920x600")
        self.root.minsize(760, 460)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        # Window / taskbar icon.
        try:
            self.root.iconbitmap(resource_path("assets", "USBSentry.ico"))
        except Exception:
            pass

        self.style = ttk.Style()
        # 'clam' honours colour configuration, which native themes largely ignore
        # — required for a working dark mode.
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        # Header strip: app icon + name + version (in-app, always visible).
        self.header = tk.Frame(self.root)
        self.header.pack(fill="x")
        self._header_img = None
        try:
            self._header_img = tk.PhotoImage(file=resource_path("assets", "USBSentry_32.png"))
            tk.Label(self.header, image=self._header_img, bd=0).pack(side="left", padx=(10, 0), pady=6)
        except Exception:
            pass
        title_text = f"{APP_NAME}" if self._header_img else f"  🛡  {APP_NAME}"
        self.header_title = tk.Label(self.header, text=f"  {title_text}",
                                     font=("Segoe UI", self.base + 4, "bold"), anchor="w")
        self.header_title.pack(side="left", pady=6)
        self.header_ver = tk.Label(self.header, text=f"v{APP_VERSION}  ",
                                   font=("Segoe UI", self.base, "bold"))
        self.header_ver.pack(side="right", pady=6)

        # Banner
        self.banner_var = tk.StringVar(value="Starting up…")
        self.banner = tk.Label(self.root, textvariable=self.banner_var, anchor="w",
                               font=("Segoe UI", self.base + 1, "bold"), padx=12, pady=8)
        self.banner.pack(fill="x")

        # Toolbar
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="Refresh now", command=self.refresh_now).pack(side="left")
        ttk.Button(bar, text="Trusted devices…", command=self.manage_known).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Export log…", command=self.export_log).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Quit", command=self.confirm_quit).pack(side="right")
        ttk.Button(bar, text="Settings…", command=self.open_settings).pack(side="right", padx=(0, 8))
        ttk.Button(bar, text="Hide to tray", command=self.hide_window).pack(side="right", padx=(0, 8))

        # Filter + view options row
        row2 = ttk.Frame(self.root, padding=(10, 0))
        row2.pack(fill="x")
        ttk.Label(row2, text="Filter:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._redraw_table())
        ent = ttk.Entry(row2, textvariable=self.search_var, width=30)
        ent.pack(side="left", padx=(6, 0))
        ttk.Button(row2, text="✕", width=3,
                   command=lambda: self.search_var.set("")).pack(side="left", padx=(4, 0))

        self.show_hubs_var = tk.BooleanVar(value=self.cfg["show_hubs"])
        ttk.Checkbutton(row2, text="Show hubs & internal", variable=self.show_hubs_var,
                        command=self._toggle_hubs).pack(side="left", padx=(16, 0))
        self.show_hidden_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Show hidden", variable=self.show_hidden_var,
                        command=self._redraw_table).pack(side="left", padx=(12, 0))
        ttk.Button(row2, text="Clear log", command=self.clear_log).pack(side="right")

        # Device table
        table_frame = ttk.Frame(self.root, padding=(10, 6))
        table_frame.pack(fill="both", expand=True)

        cols = ("known", "name", "port", "class", "status", "manufacturer", "vid_pid")
        headings = {
            "known": "Known?", "name": "Device", "port": "Port / Drive", "class": "Type",
            "status": "Status", "manufacturer": "Manufacturer", "vid_pid": "VID:PID",
        }
        widths = {"known": 60, "name": 250, "port": 90, "class": 100, "status": 70,
                  "manufacturer": 180, "vid_pid": 100}

        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=headings[c], anchor="center")
            self.tree.column(c, width=widths[c], anchor="center")  # centered text
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>", self._show_ctx_menu)
        self.tree.bind("<Double-1>", self._on_double_click)

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Right-click menu.
        self.ctx_menu = tk.Menu(self.tree, tearoff=0)
        self.ctx_menu.add_command(label="Trust this device (stop alerting)", command=self._ctx_trust)  # 0
        self.ctx_menu.add_command(label="Untrust this device", command=self._ctx_untrust)              # 1
        self.ctx_menu.add_separator()                                                                  # 2
        self.ctx_menu.add_command(label="Hide from list", command=self._ctx_hide)                      # 3
        self.ctx_menu.add_command(label="Unhide", command=self._ctx_unhide)                            # 4
        self.ctx_menu.add_separator()                                                                  # 5
        self.ctx_menu.add_command(label="Copy VID:PID", command=self._ctx_copy_vidpid)                 # 6
        self.ctx_menu.add_command(label="Copy COM port", command=self._ctx_copy_com)                   # 7
        self.ctx_menu.add_command(label="Copy esptool flash command", command=self._ctx_copy_esptool)  # 8
        self.ctx_menu.add_separator()                                                                  # 9
        self.ctx_menu.add_command(label="Watch this board (alert on reconnect)", command=self._ctx_watch)  # 10
        self.ctx_menu.add_command(label="Stop watching this board", command=self._ctx_unwatch)         # 11
        self.CTX_COM, self.CTX_ESPTOOL, self.CTX_WATCH, self.CTX_UNWATCH = 7, 8, 10, 11

        # Legend
        self.legend = tk.Frame(self.root)
        self.legend.pack(fill="x", padx=12, pady=(2, 0))
        self.legend_label = tk.Label(self.legend, text="Legend:", font=("Segoe UI", self.base - 2))
        self.legend_label.pack(side="left")
        self.legend_unrec = tk.Label(self.legend, text=" unrecognized ", font=("Segoe UI", self.base - 2))
        self.legend_unrec.pack(side="left", padx=(4, 0))
        self.legend_new = tk.Label(self.legend, text=" newly connected ", font=("Segoe UI", self.base - 2))
        self.legend_new.pack(side="left", padx=(8, 0))
        self.legend_hint = tk.Label(self.legend, text="   Right-click a device to trust / hide it.",
                                    font=("Segoe UI", self.base - 2))
        self.legend_hint.pack(side="left")

        # Detail line
        self.detail_var = tk.StringVar(value="Select a device to see its full instance ID.")
        self.detail_label = tk.Label(self.root, textvariable=self.detail_var, anchor="w",
                                     font=("Segoe UI", self.base - 1), padx=12, pady=4)
        self.detail_label.pack(fill="x")

        # Event log
        self.log_frame = ttk.LabelFrame(self.root, text="Activity log", padding=(8, 4))
        self.log_frame.pack(fill="both", expand=False, padx=10, pady=(0, 10))
        self.log = tk.Text(self.log_frame, height=7, wrap="none", state="disabled",
                           font=("Consolas", self.base - 1), relief="flat")
        self.log.pack(fill="both", expand=True)

    def apply_theme(self):
        """Apply the current palette + font size to every widget."""
        p = self.palette
        base = self.base
        st = self.style

        self.root.configure(bg=p["bg"])
        # Flatten clam's 3-D bevels so borders don't look odd, especially in dark.
        st.configure(".", background=p["bg"], foreground=p["fg"],
                     fieldbackground=p["surface"], font=("Segoe UI", base),
                     bordercolor=p["border"], lightcolor=p["bg"], darkcolor=p["bg"],
                     troughcolor=p["surface"], focuscolor=p["accent"])
        st.configure("TFrame", background=p["bg"])
        st.configure("TLabel", background=p["bg"], foreground=p["fg"])
        st.configure("TButton", background=p["surface"], foreground=p["fg"], padding=5,
                     bordercolor=p["border"], lightcolor=p["surface"], darkcolor=p["surface"])
        st.map("TButton",
               background=[("active", p["accent"]), ("pressed", p["accent"])],
               foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])
        st.configure("TCheckbutton", background=p["bg"], foreground=p["fg"])
        st.map("TCheckbutton", background=[("active", p["bg"])],
               foreground=[("disabled", p["muted"])],
               indicatorcolor=[("selected", p["accent"]), ("!selected", p["surface"])])
        st.configure("TEntry", fieldbackground=p["entry_bg"], foreground=p["entry_fg"],
                     insertcolor=p["fg"], bordercolor=p["border"], borderwidth=1)
        st.configure("TSpinbox", fieldbackground=p["entry_bg"], foreground=p["entry_fg"],
                     insertcolor=p["fg"], arrowcolor=p["fg"], bordercolor=p["border"])
        st.map("TSpinbox", foreground=[("readonly", p["entry_fg"])])
        # Combobox: fix invisible readonly text + theme the dropdown list.
        st.configure("TCombobox", fieldbackground=p["entry_bg"], foreground=p["entry_fg"],
                     arrowcolor=p["fg"], bordercolor=p["border"])
        st.map("TCombobox",
               fieldbackground=[("readonly", p["entry_bg"]), ("disabled", p["bg"])],
               foreground=[("readonly", p["entry_fg"]), ("disabled", p["muted"])],
               selectbackground=[("readonly", p["entry_bg"])],
               selectforeground=[("readonly", p["entry_fg"])],
               arrowcolor=[("readonly", p["fg"])])
        self.root.option_add("*TCombobox*Listbox.background", p["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", p["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", p["sel_bg"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", p["sel_fg"])
        st.configure("TLabelframe", background=p["bg"], foreground=p["fg"],
                     bordercolor=p["border"])
        st.configure("TLabelframe.Label", background=p["bg"], foreground=p["fg"])
        st.configure("TSeparator", background=p["border"])
        st.configure("Vertical.TScrollbar", background=p["surface"], troughcolor=p["bg"],
                     arrowcolor=p["fg"], bordercolor=p["border"])

        st.configure("Treeview", background=p["tree_bg"], fieldbackground=p["tree_bg"],
                     foreground=p["tree_fg"], font=("Segoe UI", base),
                     rowheight=int(base * 2.4), borderwidth=0, relief="flat")
        st.configure("Treeview.Heading", background=p["heading_bg"], foreground=p["heading_fg"],
                     font=("Segoe UI", base, "bold"), relief="flat", borderwidth=1)
        st.map("Treeview.Heading", background=[("active", p["accent"])],
               foreground=[("active", "#ffffff")])
        st.map("Treeview", background=[("selected", p["sel_bg"])],
               foreground=[("selected", p["sel_fg"])])
        self.tree.tag_configure("new", background=p["row_new_bg"], foreground=p["row_new_fg"])
        self.tree.tag_configure("untrusted", background=p["row_untrusted_bg"],
                                foreground=p["row_untrusted_fg"])

        # Non-ttk widgets.
        for w in (self.header, self.legend):
            w.configure(bg=p["bg"])
        self.header_title.configure(bg=p["bg"], fg=p["fg"], font=("Segoe UI", base + 4, "bold"))
        self.header_ver.configure(bg=p["bg"], fg=p["muted"], font=("Segoe UI", base, "bold"))
        self.legend_label.configure(bg=p["bg"], fg=p["muted"], font=("Segoe UI", base - 2))
        self.legend_unrec.configure(bg=p["row_untrusted_bg"], fg=p["row_untrusted_fg"],
                                    font=("Segoe UI", base - 2))
        self.legend_new.configure(bg=p["row_new_bg"], fg=p["row_new_fg"],
                                  font=("Segoe UI", base - 2))
        self.legend_hint.configure(bg=p["bg"], fg=p["muted"], font=("Segoe UI", base - 2))
        self.detail_label.configure(bg=p["bg"], fg=p["fg"], font=("Segoe UI", base - 1))
        self.banner.configure(font=("Segoe UI", base + 1, "bold"))
        self.log.configure(bg=p["surface"], fg=p["fg"], insertbackground=p["fg"],
                           font=("Consolas", base - 1))
        self.ctx_menu.configure(bg=p["surface"], fg=p["fg"],
                                activebackground=p["accent"], activeforeground="#ffffff")
        self._set_banner_idle()

        # Match the Windows title bar to the theme.
        set_dark_titlebar(self.root, self.theme == "dark")
        if self.root.winfo_viewable():
            # Nudge Windows to repaint the title bar after a live theme switch.
            self.root.withdraw()
            self.root.deiconify()

    def _build_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show window", self._tray_show, default=True),
            pystray.MenuItem("Refresh now", lambda: self.root.after(0, self.refresh_now)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self._tray_normal, self._tray_alert = load_tray_images()
        self.icon = pystray.Icon(APP_NAME, self._tray_normal, f"{APP_NAME} v{APP_VERSION}", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    # -- Background scanning --------------------------------------------------

    def _scan_loop(self):
        while not self._stop.is_set():
            devices = get_devices(self.cfg["show_hubs"])
            self.events.put(("snapshot", devices))
            interval = max(1, int(self.cfg["poll_seconds"]))
            for _ in range(interval * 2):
                if self._stop.is_set():
                    return
                time.sleep(0.5)

    def _pump_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "snapshot":
                    self._apply_snapshot(payload)
        except queue.Empty:
            pass
        self.root.after(200, self._pump_events)

    def _apply_snapshot(self, devices):
        old_ids = set(self.devices)
        new_ids = set(devices)
        added = new_ids - old_ids
        removed = old_ids - new_ids

        if not self.baseline_ready:
            self.devices = devices
            self.baseline_ready = True
            if not self.known_file_existed:
                for dev in devices.values():
                    self.known[device_key(dev)] = dev["name"]
                save_known(self.known)
                self._log(f"Auto-trusted {len(self.known)} device(s) present at first run.")
            self._redraw_table()
            self._set_banner_idle()
            self._log(f"Baseline: {len(devices)} device(s) already connected.")
            return

        if removed:
            for rid in removed:
                dev = self.devices.get(rid, {})
                self._log(f"REMOVED  {dev.get('name', rid)}")
                self._record_event("REMOVED", dev)

        self.devices = devices

        if added:
            self.new_ids |= added
            for a in added:
                dev = devices[a]
                trusted = self.is_trusted(dev)
                flag = "" if trusted else "  <-- UNRECOGNIZED"
                self._log(f"NEW      {dev['name']}  [{dev.get('vid_pid', '')}]{flag}")
                self._record_event("CONNECTED" if trusted else "CONNECTED-UNRECOGNIZED", dev)

            # Watched boards always get their own port-aware toast; keep them out
            # of the normal alert so we don't double-notify.
            watched_added = [devices[a] for a in added if device_key(devices[a]) in self.watched]

            if self.cfg.get("unrecognized_only", True):
                alert_devs = [devices[a] for a in added if not self.is_trusted(devices[a])]
            else:
                alert_devs = [devices[a] for a in added]
            alert_devs = [d for d in alert_devs if device_key(d) not in self.watched]

            if alert_devs:
                unrecognized = any(not self.is_trusted(d) for d in alert_devs)
                self._alert([d["name"] for d in alert_devs], unrecognized=unrecognized)

            for d in watched_added:
                self._announce_watched(d)

        self._redraw_table()

        if not added and not self.new_ids:
            self._set_banner_idle()

    # -- Trust (known devices) -----------------------------------------------

    def is_trusted(self, dev):
        return device_key(dev) in self.known

    def trust_device(self, dev):
        key = device_key(dev)
        if key:
            self.known[key] = dev["name"]
            save_known(self.known)
            self._log(f"TRUSTED    {dev['name']}  [{key}]")
            self._redraw_table()

    def untrust_device(self, dev):
        key = device_key(dev)
        if key in self.known:
            del self.known[key]
            save_known(self.known)
            self._log(f"UNTRUSTED  {dev['name']}  [{key}]")
            self._redraw_table()

    # -- Hide -----------------------------------------------------------------

    def hide_device(self, dev):
        key = device_key(dev)
        if key:
            self.hidden.add(key)
            self.cfg["hidden"] = sorted(self.hidden)
            save_config(self.cfg)
            self._log(f"HIDDEN     {dev['name']}  [{key}]")
            self._redraw_table()

    def unhide_device(self, dev):
        key = device_key(dev)
        if key in self.hidden:
            self.hidden.discard(key)
            self.cfg["hidden"] = sorted(self.hidden)
            save_config(self.cfg)
            self._log(f"UNHIDDEN   {dev['name']}  [{key}]")
            self._redraw_table()

    # -- Alerts ---------------------------------------------------------------

    def _alert(self, names, unrecognized=False):
        joined = ", ".join(names)
        p = self.palette

        if self.cfg["alert_banner"]:
            if unrecognized:
                self.banner.configure(bg=p["unrec_bg"], fg="#ffffff")
                self.banner_var.set(
                    f"⚠  UNRECOGNIZED device connected:  {joined}    (right-click it to trust)")
            else:
                self.banner.configure(bg=p["alert_bg"], fg="#ffffff")
                self.banner_var.set(f"\U0001F514  New device connected:  {joined}")

        if self.cfg["alert_sound"]:
            play_sound(self.cfg.get("sound_name", "Chime"))

        if self.cfg["alert_toast"]:
            title = "⚠ Unrecognized USB device!" if unrecognized else "USB device connected"
            try:
                self.icon.notify(joined, title)
            except Exception:
                pass

        try:
            self.icon.icon = self._tray_alert
            self.root.after(4000, lambda: setattr(self.icon, "icon", self._tray_normal))
        except Exception:
            pass

    def _announce_watched(self, dev):
        """A watched board reconnected — always tell the user, with its port."""
        port = dev.get("com_port") or dev.get("drive") or ""
        arrow = f"  →  {port}" if port else ""
        msg = f"{dev['name']}{arrow}"
        self._log(f"WATCHED board connected:  {msg}")
        if self.cfg["alert_banner"]:
            self.banner.configure(bg=self.palette["accent"], fg="#ffffff")
            self.banner_var.set(f"⚡  Watched board connected:  {msg}")
        if self.cfg["alert_sound"]:
            play_sound(self.cfg.get("sound_name", "Chime"))
        if self.cfg["alert_toast"]:
            try:
                self.icon.notify(msg, "⚡ Watched board connected")
            except Exception:
                pass
        try:
            self.icon.icon = self._tray_alert
            self.root.after(4000, lambda: setattr(self.icon, "icon", self._tray_normal))
        except Exception:
            pass

    def _set_banner_idle(self):
        p = self.palette
        self.banner.configure(bg=p["banner_bg"], fg=p["banner_fg"])
        n = len([d for d in self.devices.values() if device_key(d) not in self.hidden
                 or self.show_hidden_var.get()])
        self.banner_var.set(f"Monitoring — {n} USB peripheral(s) shown.")

    # -- Table ----------------------------------------------------------------

    def _visible_devices(self):
        """Devices after applying the hidden set and the search filter."""
        term = self.search_var.get().strip().lower()
        show_hidden = self.show_hidden_var.get()
        out = []
        for iid, dev in self.devices.items():
            if device_key(dev) in self.hidden and not show_hidden:
                continue
            if term:
                hay = " ".join([dev["name"], dev["class"], dev["manufacturer"],
                                dev["vid_pid"], dev["status"]]).lower()
                if term not in hay:
                    continue
            out.append((iid, dev))
        out.sort(key=lambda kv: kv[1]["name"].lower())
        return out

    def _redraw_table(self):
        selected = self.tree.selection()
        sel_id = selected[0] if selected else None

        self.tree.delete(*self.tree.get_children())
        for iid, dev in self._visible_devices():
            trusted = self.is_trusted(dev)
            hidden = device_key(dev) in self.hidden
            if not trusted:
                tags = ("untrusted",)
            elif iid in self.new_ids:
                tags = ("new",)
            else:
                tags = ()
            known_mark = "✓" if trusted else "✕"
            if hidden:
                known_mark += " (hidden)"
            port_drive = dev.get("com_port") or dev.get("drive") or ""
            self.tree.insert("", "end", iid=iid, tags=tags, values=(
                known_mark, dev["name"], port_drive, dev["class"], dev["status"],
                dev["manufacturer"], dev["vid_pid"],
            ))
        if sel_id and self.tree.exists(sel_id):
            self.tree.selection_set(sel_id)

    def _on_select(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        dev = self.devices.get(sel[0])
        if dev:
            status = "TRUSTED (known)" if self.is_trusted(dev) else "NOT trusted — unrecognized"
            self.detail_var.set(f"{status}    |    Instance ID:  {dev['instance_id']}")

    # -- Right-click menu -----------------------------------------------------

    def _show_ctx_menu(self, event):
        row = self.tree.identify_row(event.y)
        if not row or row not in self.devices:
            return
        self.tree.selection_set(row)
        self._ctx_target = row
        dev = self.devices[row]
        trusted = self.is_trusted(dev)
        hidden = device_key(dev) in self.hidden
        has_com = bool(dev.get("com_port"))
        watched = device_key(dev) in self.watched
        self.ctx_menu.entryconfigure(0, state="disabled" if trusted else "normal")
        self.ctx_menu.entryconfigure(1, state="normal" if trusted else "disabled")
        self.ctx_menu.entryconfigure(3, state="disabled" if hidden else "normal")
        self.ctx_menu.entryconfigure(4, state="normal" if hidden else "disabled")
        self.ctx_menu.entryconfigure(self.CTX_COM, state="normal" if has_com else "disabled")
        self.ctx_menu.entryconfigure(self.CTX_ESPTOOL, state="normal" if has_com else "disabled")
        self.ctx_menu.entryconfigure(self.CTX_WATCH, state="disabled" if watched else "normal")
        self.ctx_menu.entryconfigure(self.CTX_UNWATCH, state="normal" if watched else "disabled")
        self.ctx_menu.tk_popup(event.x_root, event.y_root)

    def _ctx_dev(self):
        return self.devices.get(getattr(self, "_ctx_target", None))

    def _ctx_trust(self):
        dev = self._ctx_dev()
        if dev:
            self.trust_device(dev)
            if not self.new_ids:
                self._set_banner_idle()

    def _ctx_untrust(self):
        dev = self._ctx_dev()
        if dev:
            self.untrust_device(dev)

    def _ctx_hide(self):
        dev = self._ctx_dev()
        if dev:
            self.hide_device(dev)

    def _ctx_unhide(self):
        dev = self._ctx_dev()
        if dev:
            self.unhide_device(dev)

    def _copy(self, text, what):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # keep clipboard after the app closes
        self._log(f"Copied {what}: {text}")

    def _ctx_copy_vidpid(self):
        dev = self._ctx_dev()
        if dev and dev.get("vid_pid"):
            self._copy(dev["vid_pid"], "VID:PID")

    def _ctx_copy_com(self):
        dev = self._ctx_dev()
        if dev and dev.get("com_port"):
            self._copy(dev["com_port"], "COM port")

    def _ctx_copy_esptool(self):
        dev = self._ctx_dev()
        if dev and dev.get("com_port"):
            cmd = (f"esptool --port {dev['com_port']} --baud 460800 "
                   f"write_flash 0x0 firmware.bin")
            self._copy(cmd, "esptool command")

    def _ctx_watch(self):
        dev = self._ctx_dev()
        if dev:
            self.watched.add(device_key(dev))
            self.cfg["watched"] = sorted(self.watched)
            save_config(self.cfg)
            self._log(f"WATCHING   {dev['name']}  — you'll be alerted when it reconnects.")

    def _ctx_unwatch(self):
        dev = self._ctx_dev()
        if dev and device_key(dev) in self.watched:
            self.watched.discard(device_key(dev))
            self.cfg["watched"] = sorted(self.watched)
            save_config(self.cfg)
            self._log(f"STOPPED WATCHING  {dev['name']}")

    def _on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        dev = self.devices.get(row)
        if dev and (dev.get("com_port") or dev.get("drive")):
            self._copy(dev.get("com_port") or dev.get("drive"), "port/drive")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # -- Log ------------------------------------------------------------------

    def _log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -- History --------------------------------------------------------------

    def _ensure_history_file(self):
        if not os.path.exists(HISTORY_PATH):
            try:
                with open(HISTORY_PATH, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow(HISTORY_HEADER)
            except OSError:
                pass

    def _record_event(self, action, dev):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [ts, action, dev.get("name", ""), dev.get("class", ""),
               dev.get("vid_pid", ""), dev.get("instance_id", "")]
        try:
            with open(HISTORY_PATH, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(row)
        except OSError:
            pass

    def export_log(self):
        default = f"usb-history-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Export USB event history",
            defaultextension=".csv", initialfile=default,
            filetypes=[("CSV file", "*.csv"), ("Text file", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        self._ensure_history_file()
        try:
            shutil.copyfile(HISTORY_PATH, path)
            self._log(f"Exported history to {path}")
        except OSError as exc:
            self._log(f"Export failed: {exc}")

    # -- Known-devices manager ------------------------------------------------

    def manage_known(self):
        win = tk.Toplevel(self.root)
        win.title("Trusted (known) devices")
        win.transient(self.root)
        win.geometry("520x360")
        win.configure(bg=self.palette["bg"])
        try:
            win.iconbitmap(resource_path("assets", "USBSentry.ico"))
        except Exception:
            pass
        set_dark_titlebar(win, self.theme == "dark")
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="These device models are trusted and won't raise an "
                            "“unrecognized” alert:",
                  font=("Segoe UI", self.base, "bold"), wraplength=480).pack(anchor="w", pady=(0, 8))

        list_frame = ttk.Frame(frm)
        list_frame.pack(fill="both", expand=True)
        p = self.palette
        lb = tk.Listbox(list_frame, activestyle="none", bg=p["surface"], fg=p["fg"],
                        selectbackground=p["sel_bg"], selectforeground=p["sel_fg"],
                        font=("Segoe UI", self.base), relief="flat")
        lb.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(list_frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")

        order = sorted(self.known.items(), key=lambda kv: kv[1].lower())
        keys = [k for k, _ in order]
        for k, name in order:
            lb.insert("end", f"{name}    [{k}]")

        def remove_selected():
            sel = lb.curselection()
            if not sel:
                return
            i = sel[0]
            self.known.pop(keys[i], None)
            save_known(self.known)
            keys.pop(i)
            lb.delete(i)
            self._redraw_table()

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Remove selected (untrust)", command=remove_selected).pack(side="left")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")
        win.grab_set()

    # -- Actions --------------------------------------------------------------

    def refresh_now(self):
        self.new_ids.clear()
        self._set_banner_idle()
        self._redraw_table()

        def once():
            devices = get_devices(self.cfg["show_hubs"])
            self.events.put(("snapshot", devices))
        threading.Thread(target=once, daemon=True).start()

    def _toggle_hubs(self):
        self.cfg["show_hubs"] = self.show_hubs_var.get()
        save_config(self.cfg)
        self.refresh_now()

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.transient(self.root)
        win.configure(bg=self.palette["bg"])
        try:
            win.iconbitmap(resource_path("assets", "USBSentry.ico"))
        except Exception:
            pass
        set_dark_titlebar(win, self.theme == "dark")
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        r = 0

        ttk.Label(frm, text="Alert me when a new device connects by:",
                  font=("Segoe UI", self.base, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1

        v_toast = tk.BooleanVar(value=self.cfg["alert_toast"])
        v_banner = tk.BooleanVar(value=self.cfg["alert_banner"])
        v_sound = tk.BooleanVar(value=self.cfg["alert_sound"])
        ttk.Checkbutton(frm, text="Windows toast notification", variable=v_toast).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        ttk.Checkbutton(frm, text="In-app banner + row highlight", variable=v_banner).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        ttk.Checkbutton(frm, text="Sound", variable=v_sound).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1

        ttk.Separator(frm, orient="horizontal").grid(row=r, column=0, columnspan=2, sticky="ew", pady=12); r += 1

        v_unrec = tk.BooleanVar(value=self.cfg.get("unrecognized_only", True))
        ttk.Checkbutton(frm, text="Only alert for unrecognized (untrusted) devices",
                        variable=v_unrec).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        ttk.Label(frm, text="When on, devices you've trusted connect silently.",
                  foreground=self.palette["muted"], wraplength=380).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1

        ttk.Separator(frm, orient="horizontal").grid(row=r, column=0, columnspan=2, sticky="ew", pady=12); r += 1

        # Appearance
        ttk.Label(frm, text="Appearance:", font=("Segoe UI", self.base, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 6)); r += 1
        ttk.Label(frm, text="Theme:").grid(row=r, column=0, sticky="w")
        v_theme = tk.StringVar(value=self.cfg.get("theme", "system"))
        ttk.Combobox(frm, textvariable=v_theme, state="readonly", width=18,
                     values=["system", "light", "dark"]).grid(row=r, column=1, sticky="w", padx=(8, 0)); r += 1
        ttk.Label(frm, text="Text size:").grid(row=r, column=0, sticky="w", pady=(6, 0))
        v_size = tk.StringVar(value=self.cfg.get("text_size", "Normal"))
        ttk.Combobox(frm, textvariable=v_size, state="readonly", width=18,
                     values=list(TEXT_SIZES.keys())).grid(row=r, column=1, sticky="w", padx=(8, 0), pady=(6, 0)); r += 1

        ttk.Label(frm, text="Alert sound:").grid(row=r, column=0, sticky="w", pady=(6, 0))
        v_sound_name = tk.StringVar(value=self.cfg.get("sound_name", "Chime"))
        sound_row = ttk.Frame(frm)
        sound_row.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=(6, 0)); r += 1
        ttk.Combobox(sound_row, textvariable=v_sound_name, state="readonly", width=13,
                     values=list(SOUNDS.keys())).pack(side="left")
        ttk.Button(sound_row, text="Test", width=6,
                   command=lambda: play_sound(v_sound_name.get())).pack(side="left", padx=(6, 0))

        ttk.Separator(frm, orient="horizontal").grid(row=r, column=0, columnspan=2, sticky="ew", pady=12); r += 1

        ttk.Label(frm, text="Scan every (seconds):").grid(row=r, column=0, sticky="w")
        v_poll = tk.IntVar(value=int(self.cfg["poll_seconds"]))
        ttk.Spinbox(frm, from_=1, to=60, textvariable=v_poll, width=6).grid(row=r, column=1, sticky="w", padx=(8, 0)); r += 1

        v_autostart = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(frm, text="Start USBSentry when Windows starts",
                        variable=v_autostart).grid(row=r, column=0, columnspan=2, sticky="w", pady=(10, 0)); r += 1

        def apply_and_close():
            self.cfg["alert_toast"] = v_toast.get()
            self.cfg["alert_banner"] = v_banner.get()
            self.cfg["alert_sound"] = v_sound.get()
            self.cfg["unrecognized_only"] = v_unrec.get()
            self.cfg["theme"] = v_theme.get()
            self.cfg["text_size"] = v_size.get()
            self.cfg["sound_name"] = v_sound_name.get()
            self.cfg["poll_seconds"] = max(1, int(v_poll.get()))
            save_config(self.cfg)
            set_autostart(v_autostart.get())
            # Re-resolve theme + font and re-skin everything live.
            self.theme = resolve_theme(self.cfg["theme"])
            self.palette = PALETTES[self.theme]
            self.base = TEXT_SIZES.get(self.cfg["text_size"], 10)
            self.apply_theme()
            self._redraw_table()
            win.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Save", command=apply_and_close).pack(side="right")
        win.grab_set()

    # -- Window / tray plumbing ----------------------------------------------

    def hide_window(self):
        self.root.withdraw()
        if self.cfg["alert_toast"]:
            try:
                self.icon.notify("Still watching in the background.", APP_NAME)
            except Exception:
                pass

    def _tray_show(self, *_):
        self.root.after(0, self.show_window)

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.new_ids.clear()
        self._set_banner_idle()
        self._redraw_table()

    def _tray_quit(self, *_):
        self.root.after(0, self.quit)

    def confirm_quit(self):
        """Fully exit the app (does NOT stay in the tray)."""
        if messagebox.askyesno(
                APP_NAME,
                "Quit USBSentry completely?\n\n"
                "It will stop monitoring and will NOT keep running in the system tray.",
                parent=self.root, icon="warning"):
            self.quit()

    def quit(self):
        self._stop.set()
        try:
            self.icon.stop()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("USBSentry is Windows-only.")
        sys.exit(1)
    USBWatchApp().run()

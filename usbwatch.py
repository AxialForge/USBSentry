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
- Lives in a normal window that hides to the system tray when closed;
  quit from the tray menu or the Quit button.

Requires: pystray, Pillow (Tkinter ships with Python on Windows).
"""

import csv
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import winsound
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog

import pystray
from PIL import Image, ImageDraw

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

APP_NAME = "USBSentry"

# Where to keep config / history / known-devices files.
# When frozen by PyInstaller, __file__ lives in a temp extraction dir that is
# deleted on exit, so we must use the folder that actually holds the .exe.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
}


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

# PowerShell that returns every present device sitting on the USB bus as JSON.
_PS_QUERY = (
    "Get-PnpDevice -PresentOnly | "
    "Where-Object { $_.InstanceId -like 'USB\\*' } | "
    "Select-Object FriendlyName, Class, Status, Manufacturer, InstanceId | "
    "ConvertTo-Json -Compress -Depth 3"
)

_VIDPID_RE = re.compile(r"VID_([0-9A-Fa-f]{4}).*?PID_([0-9A-Fa-f]{4})")
# Root hubs and generic USB hubs we hide unless "show hubs" is on.
_HUB_RE = re.compile(r"root_hub|usb.*hub|generic usb hub", re.IGNORECASE)

# Hide the child PowerShell console window.
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

    # ConvertTo-Json emits a bare object when there is exactly one device.
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

        devices[instance_id] = {
            "instance_id": instance_id,
            "name": name,
            "class": (item.get("Class") or "").strip(),
            "status": (item.get("Status") or "").strip(),
            "manufacturer": (item.get("Manufacturer") or "").strip(),
            "vid_pid": vid_pid,
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
    # A little USB-plug glyph in white.
    d.rectangle((28, 14, 36, 40), fill="white")
    d.polygon((26, 40, 38, 40, 32, 50), fill="white")
    d.ellipse((29, 9, 35, 15), fill="white")
    d.rectangle((22, 22, 26, 26), fill="white")
    d.rectangle((38, 28, 42, 32), fill="white")
    return img


# ----------------------------------------------------------------------------
# Main application
# ----------------------------------------------------------------------------

class USBWatchApp:
    def __init__(self):
        self.cfg = load_config()
        self.devices = {}          # current snapshot {instance_id: dict}
        self.baseline_ready = False  # first scan done -> alert on later changes
        self.new_ids = set()       # ids to highlight in the table
        self.events = queue.Queue()  # worker -> UI messages
        self._stop = threading.Event()

        # Known ("trusted") device models. If the file doesn't exist yet, this
        # is the very first run — we auto-trust whatever is plugged in now.
        self.known_file_existed = os.path.exists(KNOWN_PATH)
        self.known = load_known()  # {device_key: name}

        self._ensure_history_file()
        self._build_ui()
        self._build_tray()

        # Kick off the background scanner and the UI event pump.
        self.worker = threading.Thread(target=self._scan_loop, daemon=True)
        self.worker.start()
        self.root.after(200, self._pump_events)

    # -- UI ------------------------------------------------------------------

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("880x560")
        self.root.minsize(720, 420)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        # Banner
        self.banner_var = tk.StringVar(value="Starting up…")
        self.banner = tk.Label(
            self.root, textvariable=self.banner_var, anchor="w",
            font=("Segoe UI", 11, "bold"), bg="#2f6ec8", fg="white",
            padx=12, pady=8,
        )
        self.banner.pack(fill="x")

        # Toolbar
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="Refresh now", command=self.refresh_now).pack(side="left")
        ttk.Button(bar, text="Trusted devices…", command=self.manage_known).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Export log…", command=self.export_log).pack(side="left", padx=(8, 0))
        self.show_hubs_var = tk.BooleanVar(value=self.cfg["show_hubs"])
        ttk.Checkbutton(
            bar, text="Show hubs & internal devices",
            variable=self.show_hubs_var, command=self._toggle_hubs,
        ).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Settings…", command=self.open_settings).pack(side="right")
        ttk.Button(bar, text="Hide to tray", command=self.hide_window).pack(side="right", padx=(0, 8))

        # Device table
        table_frame = ttk.Frame(self.root, padding=(10, 0))
        table_frame.pack(fill="both", expand=True)

        cols = ("known", "name", "class", "status", "manufacturer", "vid_pid")
        headings = {
            "known": "Known?", "name": "Device", "class": "Type", "status": "Status",
            "manufacturer": "Manufacturer", "vid_pid": "VID:PID",
        }
        widths = {"known": 62, "name": 280, "class": 110, "status": 75, "manufacturer": 210, "vid_pid": 105}
        anchors = {"known": "center"}

        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], anchor=anchors.get(c, "w"))
        self.tree.tag_configure("new", background="#fff2b2")        # newly connected (trusted)
        self.tree.tag_configure("untrusted", background="#f8c9c4")  # not on the known list
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # Right-click menu to trust/untrust a device.
        self.ctx_menu = tk.Menu(self.tree, tearoff=0)
        self.ctx_menu.add_command(label="Trust this device (stop alerting)", command=self._ctx_trust)
        self.ctx_menu.add_command(label="Untrust this device", command=self._ctx_untrust)
        self.tree.bind("<Button-3>", self._show_ctx_menu)

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Legend for the row colors.
        legend = tk.Frame(self.root)
        legend.pack(fill="x", padx=12, pady=(4, 0))
        tk.Label(legend, text="  Legend:  ", font=("Segoe UI", 8)).pack(side="left")
        tk.Label(legend, text=" unrecognized ", bg="#f8c9c4", font=("Segoe UI", 8)).pack(side="left")
        tk.Label(legend, text="   ", font=("Segoe UI", 8)).pack(side="left")
        tk.Label(legend, text=" newly connected ", bg="#fff2b2", font=("Segoe UI", 8)).pack(side="left")
        tk.Label(legend, text="    Right-click a device to trust / untrust it.",
                 font=("Segoe UI", 8), fg="#555").pack(side="left")

        # Detail line for the selected device
        self.detail_var = tk.StringVar(value="Select a device to see its full instance ID.")
        ttk.Label(self.root, textvariable=self.detail_var, anchor="w",
                  padding=(12, 4)).pack(fill="x")

        # Event log
        log_frame = ttk.LabelFrame(self.root, text="Activity log", padding=(8, 4))
        log_frame.pack(fill="both", expand=False, padx=10, pady=(0, 10))
        self.log = tk.Text(log_frame, height=7, wrap="none", state="disabled",
                           font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

    def _build_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show window", self._tray_show, default=True),
            pystray.MenuItem("Refresh now", lambda: self.root.after(0, self.refresh_now)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self.icon = pystray.Icon(APP_NAME, make_tray_image(), APP_NAME, menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    # -- Background scanning --------------------------------------------------

    def _scan_loop(self):
        while not self._stop.is_set():
            show_hubs = self.cfg["show_hubs"]
            devices = get_devices(show_hubs)
            self.events.put(("snapshot", devices))
            # Sleep in small slices so config/interval changes take effect fast.
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

        # First scan just establishes the baseline (no alerts for what's
        # already plugged in).
        if not self.baseline_ready:
            self.devices = devices
            self.baseline_ready = True
            # On the very first run, trust everything already connected — it's
            # all the user's own gear.
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
                self._log(f"NEW      {dev['name']}  [{dev.get('vid_pid','')}]{flag}")
                self._record_event("CONNECTED" if trusted else "CONNECTED-UNRECOGNIZED", dev)

            # Decide which of the new devices actually warrant an alert.
            if self.cfg.get("unrecognized_only", True):
                alert_devs = [devices[a] for a in added if not self.is_trusted(devices[a])]
            else:
                alert_devs = [devices[a] for a in added]

            if alert_devs:
                unrecognized = any(not self.is_trusted(d) for d in alert_devs)
                self._alert([d["name"] for d in alert_devs], unrecognized=unrecognized)

        self._redraw_table()

        if not added:
            # keep banner fresh with the current count / clear stale alert color
            if not self.new_ids:
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

    # -- Alerts ---------------------------------------------------------------

    def _alert(self, names, unrecognized=False):
        joined = ", ".join(names)

        if self.cfg["alert_banner"]:
            if unrecognized:
                self.banner.configure(bg="#c0392b")
                self.banner_var.set(
                    f"⚠  UNRECOGNIZED device connected:  {joined}"
                    f"    (right-click it to trust)")
            else:
                self.banner.configure(bg="#d9822b")
                self.banner_var.set(f"\U0001F514  New device connected:  {joined}")

        if self.cfg["alert_sound"]:
            try:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except RuntimeError:
                pass

        if self.cfg["alert_toast"]:
            title = "⚠ Unrecognized USB device!" if unrecognized else "USB device connected"
            try:
                self.icon.notify(joined, title)
            except Exception:
                pass

        # Flash the tray icon red briefly.
        try:
            self.icon.icon = make_tray_image(alert=True)
            self.root.after(4000, lambda: setattr(self.icon, "icon", make_tray_image()))
        except Exception:
            pass

    def _set_banner_idle(self):
        self.banner.configure(bg="#2f6ec8")
        n = len(self.devices)
        self.banner_var.set(f"Monitoring — {n} USB peripheral(s) connected.")

    # -- Table ----------------------------------------------------------------

    def _redraw_table(self):
        selected = self.tree.selection()
        sel_id = selected[0] if selected else None

        self.tree.delete(*self.tree.get_children())
        for iid, dev in sorted(self.devices.items(), key=lambda kv: kv[1]["name"].lower()):
            trusted = self.is_trusted(dev)
            if not trusted:
                tags = ("untrusted",)
            elif iid in self.new_ids:
                tags = ("new",)
            else:
                tags = ()
            # The tree item id (iid) is the device's instance_id.
            self.tree.insert("", "end", iid=iid, tags=tags, values=(
                "✓" if trusted else "✕",
                dev["name"], dev["class"], dev["status"],
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

    # -- Right-click trust menu ----------------------------------------------

    def _show_ctx_menu(self, event):
        row = self.tree.identify_row(event.y)
        if not row or row not in self.devices:
            return
        self.tree.selection_set(row)
        self._ctx_target = row
        trusted = self.is_trusted(self.devices[row])
        self.ctx_menu.entryconfigure(0, state="disabled" if trusted else "normal")
        self.ctx_menu.entryconfigure(1, state="normal" if trusted else "disabled")
        self.ctx_menu.tk_popup(event.x_root, event.y_root)

    def _ctx_trust(self):
        dev = self.devices.get(getattr(self, "_ctx_target", None))
        if dev:
            self.trust_device(dev)
            if not self.new_ids:
                self._set_banner_idle()

    def _ctx_untrust(self):
        dev = self.devices.get(getattr(self, "_ctx_target", None))
        if dev:
            self.untrust_device(dev)

    # -- Log ------------------------------------------------------------------

    def _log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -- History (durable event log) -----------------------------------------

    def _ensure_history_file(self):
        if not os.path.exists(HISTORY_PATH):
            try:
                with open(HISTORY_PATH, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow(HISTORY_HEADER)
            except OSError:
                pass

    def _record_event(self, action, dev):
        """Append one connect/disconnect event to history.csv (persists across runs)."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [
            ts, action, dev.get("name", ""), dev.get("class", ""),
            dev.get("vid_pid", ""), dev.get("instance_id", ""),
        ]
        try:
            with open(HISTORY_PATH, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(row)
        except OSError:
            pass

    def export_log(self):
        """Save a copy of the full event history to a location the user picks."""
        default = f"usb-history-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export USB event history",
            defaultextension=".csv",
            initialfile=default,
            filetypes=[("CSV file", "*.csv"), ("Text file", "*.txt"), ("All files", "*.*")],
        )
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
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="These device models are trusted and won't raise an "
                            "“unrecognized” alert:",
                  font=("Segoe UI", 10, "bold"), wraplength=480).pack(anchor="w", pady=(0, 8))

        list_frame = ttk.Frame(frm)
        list_frame.pack(fill="both", expand=True)
        lb = tk.Listbox(list_frame, activestyle="none")
        lb.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(list_frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")

        # Keep a parallel list of keys matching the listbox order.
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
        ttk.Label(btns, text=f"{len(keys)} trusted", foreground="#555").pack(side="right")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right", padx=(0, 12))

        win.grab_set()

    # -- Actions --------------------------------------------------------------

    def refresh_now(self):
        # Clear "new" highlights and re-scan immediately in a thread.
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
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Alert me when a new device connects by:",
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        v_toast = tk.BooleanVar(value=self.cfg["alert_toast"])
        v_banner = tk.BooleanVar(value=self.cfg["alert_banner"])
        v_sound = tk.BooleanVar(value=self.cfg["alert_sound"])
        ttk.Checkbutton(frm, text="Windows toast notification", variable=v_toast).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(frm, text="In-app banner + row highlight", variable=v_banner).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(frm, text="Sound", variable=v_sound).grid(row=3, column=0, columnspan=2, sticky="w")

        ttk.Separator(frm, orient="horizontal").grid(row=4, column=0, columnspan=2, sticky="ew", pady=12)

        v_unrec = tk.BooleanVar(value=self.cfg.get("unrecognized_only", True))
        ttk.Checkbutton(frm, text="Only alert for unrecognized (untrusted) devices",
                        variable=v_unrec).grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Label(frm, text="When on, devices you've trusted connect silently. "
                            "Manage the list via “Trusted devices…”.",
                  foreground="#555", wraplength=360).grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 4))

        ttk.Separator(frm, orient="horizontal").grid(row=7, column=0, columnspan=2, sticky="ew", pady=12)

        ttk.Label(frm, text="Scan every (seconds):").grid(row=8, column=0, sticky="w")
        v_poll = tk.IntVar(value=int(self.cfg["poll_seconds"]))
        ttk.Spinbox(frm, from_=1, to=60, textvariable=v_poll, width=6).grid(row=8, column=1, sticky="w", padx=(8, 0))

        def apply_and_close():
            self.cfg["alert_toast"] = v_toast.get()
            self.cfg["alert_banner"] = v_banner.get()
            self.cfg["alert_sound"] = v_sound.get()
            self.cfg["unrecognized_only"] = v_unrec.get()
            self.cfg["poll_seconds"] = max(1, int(v_poll.get()))
            save_config(self.cfg)
            win.destroy()
            self._redraw_table()

        btns = ttk.Frame(frm)
        btns.grid(row=9, column=0, columnspan=2, sticky="e", pady=(16, 0))
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
        # Viewing the window clears the new-device highlights.
        self.new_ids.clear()
        self._set_banner_idle()
        self._redraw_table()

    def _tray_quit(self, *_):
        self.root.after(0, self.quit)

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

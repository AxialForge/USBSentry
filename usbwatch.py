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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
HISTORY_PATH = os.path.join(BASE_DIR, "history.csv")
HISTORY_HEADER = ["timestamp", "action", "device", "type", "vid_pid", "instance_id"]

DEFAULT_CONFIG = {
    "poll_seconds": 3,        # how often to re-scan the USB bus
    "alert_toast": True,      # Windows notification
    "alert_banner": True,     # in-app banner + row highlight
    "alert_sound": True,      # play a chime
    "show_hubs": False,       # include root hubs / internal USB devices
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

        cols = ("name", "class", "status", "manufacturer", "vid_pid")
        headings = {
            "name": "Device", "class": "Type", "status": "Status",
            "manufacturer": "Manufacturer", "vid_pid": "VID:PID",
        }
        widths = {"name": 300, "class": 110, "status": 80, "manufacturer": 220, "vid_pid": 110}

        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], anchor="w")
        self.tree.tag_configure("new", background="#fff2b2")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

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
            names = [devices[a]["name"] for a in added]
            for a in added:
                self._log(f"NEW      {devices[a]['name']}  [{devices[a].get('vid_pid','')}]")
                self._record_event("CONNECTED", devices[a])
            self._alert(names)

        self._redraw_table()

        if not added:
            # keep banner fresh with the current count / clear stale alert color
            if not self.new_ids:
                self._set_banner_idle()

    # -- Alerts ---------------------------------------------------------------

    def _alert(self, names):
        label = names[0] if len(names) == 1 else f"{len(names)} new devices"

        if self.cfg["alert_banner"]:
            self.banner.configure(bg="#d9822b")
            self.banner_var.set(f"\U0001F514  New device connected:  {', '.join(names)}")

        if self.cfg["alert_sound"]:
            try:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except RuntimeError:
                pass

        if self.cfg["alert_toast"]:
            try:
                self.icon.notify(", ".join(names), "USB device connected")
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
        sel_id = self.tree.item(selected[0])["values"][-1] if selected else None

        self.tree.delete(*self.tree.get_children())
        for iid, dev in sorted(self.devices.items(), key=lambda kv: kv[1]["name"].lower()):
            tags = ("new",) if iid in self.new_ids else ()
            # Stash the instance_id as an extra (hidden) trailing value via iid.
            self.tree.insert("", "end", iid=iid, tags=tags, values=(
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
            self.detail_var.set(f"Instance ID:  {dev['instance_id']}")

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

        ttk.Label(frm, text="Scan every (seconds):").grid(row=5, column=0, sticky="w")
        v_poll = tk.IntVar(value=int(self.cfg["poll_seconds"]))
        ttk.Spinbox(frm, from_=1, to=60, textvariable=v_poll, width=6).grid(row=5, column=1, sticky="w", padx=(8, 0))

        def apply_and_close():
            self.cfg["alert_toast"] = v_toast.get()
            self.cfg["alert_banner"] = v_banner.get()
            self.cfg["alert_sound"] = v_sound.get()
            self.cfg["poll_seconds"] = max(1, int(v_poll.get()))
            save_config(self.cfg)
            win.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(16, 0))
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

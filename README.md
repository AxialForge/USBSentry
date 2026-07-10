# USB Watch

A simple USB peripheral monitor for Windows 11. It lists every USB device
currently connected, watches the USB bus in the background, and alerts you the
moment a new device is plugged in.

## How to run it

**Easiest of all:** double-click **`USBWatch.exe`** — a self-contained build
that needs nothing installed. Copy it to any Windows PC and it just runs.

**Or (needs Python):** double-click **`USB Watch.bat`**.

That's it — a window opens showing all connected USB peripherals. Close the
window and it keeps running quietly in the **system tray** (bottom-right, near
the clock). Left-click the tray icon to bring the window back; right-click it
for **Refresh / Quit**.

You can also run it from a terminal:

```
python usbwatch.py
```

## What it does

- **Live device list** — Device name, Type, Status, Manufacturer, and VID:PID.
  Click a row to see its full instance ID at the bottom.
- **New-device alerts** — when something is plugged in after startup, you get
  (any combination of): a Windows toast notification, an in-app orange banner +
  yellow row highlight, and a sound. The tray icon briefly turns red.
- **Removed devices** are noted in the Activity log at the bottom.
- **Event history** — every connect/disconnect is saved to `history.csv` in this
  folder automatically (it survives restarts). Click **Export log…** to save a
  copy of the full history anywhere you like (CSV, opens in Excel).
- **"Real peripherals only"** by default — internal root hubs and generic USB
  hubs are hidden. Tick **"Show hubs & internal devices"** to see everything.

## Settings

Click **Settings…** to toggle each of the three alert types independently and to
change how often it scans (default: every 3 seconds). Your choices are saved to
`config.json` in this folder.

## Start automatically when Windows boots (optional)

1. Press `Win + R`, type `shell:startup`, press Enter.
2. Right-click `USB Watch.bat` → **Copy**, then paste a **shortcut** to it into
   that Startup folder.

Now it launches (and starts watching) every time you log in.

## Rebuilding the .exe (only if you edit the code)

`USBWatch.exe` is already built and ready. If you change `usbwatch.py` and want
a fresh exe, rebuild it with:

```
cd "C:\Users\Joseph Costarella\USBWatch"
python -m PyInstaller --noconsole --onefile --name "USBWatch" --clean usbwatch.py
```

The new exe lands in `dist\USBWatch.exe`; copy it up into this folder to replace
the old one.

## Notes / troubleshooting

- **How it reads USB info:** it runs Windows' built-in `Get-PnpDevice`
  PowerShell command every few seconds and compares snapshots. No drivers, no
  admin rights needed.
- **Not seeing toasts?** Check Windows **Settings → System → Notifications** is
  on, and that the toast option is enabled in the app's Settings.
- **Requirements:** Python 3 with `pystray` and `Pillow`
  (`pip install pystray pillow`). Tkinter ships with Python on Windows.

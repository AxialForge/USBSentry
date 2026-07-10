<h1 align="center">🛡️ USBSentry</h1>

<p align="center">
  <strong>A simple, lightweight USB peripheral monitor for Windows 11.</strong><br>
  See every connected USB device at a glance — and get alerted the instant a new one is plugged in.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%2011-0078D6?logo=windows&logoColor=white" alt="Windows 11">
  <img src="https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white" alt="Python 3.13">
  <img src="https://img.shields.io/badge/UI-Tkinter-FF8C00" alt="Tkinter">
  <img src="https://img.shields.io/badge/install-none%20needed-brightgreen" alt="No install needed">
</p>

---

## ✨ What it does

USBSentry sits quietly in your system tray and keeps an eye on the USB bus. When
you plug in a new device, it lets you know — with a notification, a sound, and a
highlight in its live device list. Every connect and disconnect is logged so you
have a permanent record of what's been plugged into your machine.

## 🚀 Features

- **📋 Live device list** — every connected USB peripheral with its name, type, status, manufacturer, and `VID:PID`. Click any device to see its full instance ID.
- **🔔 New-device alerts, three ways** — a Windows toast notification, an in-app banner + highlighted row, and a sound. Each can be toggled on or off independently.
- **🧹 "Real peripherals only"** — internal root hubs and generic USB hubs are hidden by default so the list stays clean. One checkbox reveals *everything* on the bus.
- **📝 Durable event history** — every connect/disconnect is saved to `history.csv` automatically and survives restarts. Export the full log anywhere with one click.
- **🪟 Stays out of the way** — closing the window hides it to the system tray, where it keeps watching. Quit only when *you* choose to.
- **⚡ Lightweight & permission-free** — no drivers, no admin rights. Reads device info through Windows' own tooling.

## 📥 Download & run

### Option A — the ready-made app (easiest, no Python needed)

1. Go to the [**Releases**](https://github.com/joe963cost/USBSentry/releases/latest) page.
2. Download **`USBSentry.exe`**.
3. Double-click it. That's it.

> The first time you run it, Windows SmartScreen may show *"Windows protected
> your PC"* because the app isn't code-signed. Click **More info → Run anyway**.
> This is normal for small independent apps and only happens once.

### Option B — run from source (for tinkering)

Requires [Python 3](https://www.python.org/downloads/) with two small packages:

```bash
pip install pystray pillow
python usbwatch.py
```

Or just double-click **`USBSentry.bat`**.

## 🖱️ Using it

| Action | How |
|---|---|
| See all connected devices | It's the main window |
| Inspect one device | Click its row → full instance ID shows at the bottom |
| Hide but keep watching | Close the window (it goes to the tray) |
| Bring the window back | Left-click the tray icon |
| Refresh / Quit | Right-click the tray icon |
| Show internal hubs too | Tick **"Show hubs & internal devices"** |
| Export the event log | **Export log…** button → pick a location |
| Change alerts / scan speed | **Settings…** button |

## ⚙️ Settings

Open **Settings…** to independently toggle each alert type (toast / banner /
sound) and set how often USBSentry scans the bus (default: every 3 seconds).
Your preferences are saved to `config.json` next to the app.

## 🟢 Start automatically on boot (optional)

1. Press `Win + R`, type `shell:startup`, and press Enter.
2. Drop a **shortcut** to `USBSentry.exe` (or `USBSentry.bat`) into that folder.

USBSentry will now start watching every time you log in.

## 🛠️ Building the .exe yourself

The release exe is built with [PyInstaller](https://pyinstaller.org/):

```bash
pip install pyinstaller
python -m PyInstaller --noconsole --onefile --name "USBSentry" --clean usbwatch.py
```

The finished `USBSentry.exe` appears in the new `dist\` folder.

## 🔍 How it works

USBSentry runs Windows' built-in `Get-PnpDevice` command every few seconds,
filters the results to devices on the USB bus, and compares each snapshot to the
last one. Anything new triggers an alert; anything missing is logged as removed.
No kernel hooks, no polling drivers — just a light, transparent approach that
needs no special privileges.

## 🧯 Troubleshooting

| Problem | Fix |
|---|---|
| No toast notifications | Check **Windows Settings → System → Notifications** is on, and that toasts are enabled in the app's **Settings…** |
| SmartScreen warning on the exe | **More info → Run anyway** (unsigned app; happens once) |
| A device flickered and was missed | Lower the scan interval in **Settings…** |
| Running from source fails | `pip install pystray pillow` (Tkinter ships with Python on Windows) |

## 📂 Project structure

```
USBSentry/
├── usbwatch.py      # the entire app (one file)
├── USBSentry.bat    # launcher for the source version
├── README.md        # this file
└── .gitignore       # keeps build artifacts & personal files out of Git
```

Runtime files created on your machine (`USBSentry.exe`, `config.json`,
`history.csv`) are intentionally **not** tracked in Git.

---

<p align="center"><sub>Built for Windows 11 · Python + Tkinter · No install required</sub></p>

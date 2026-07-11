<h1 align="center">USBSentry</h1>

<p align="center">
  <strong>A simple, lightweight USB peripheral monitor for Windows 11.</strong><br>
  See every connected USB device at a glance, know the instant a new one is plugged in,
  and read off the COM port or drive letter without opening Device Manager.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.2.0-blue" alt="Version 1.2.0">
  <img src="https://img.shields.io/badge/platform-Windows%2011-0078D6?logo=windows&logoColor=white" alt="Windows 11">
  <img src="https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white" alt="Python 3.13">
  <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="Apache 2.0">
</p>

---

## What it does

USBSentry sits quietly in your system tray and watches the USB bus. When you plug in a
new device, it lets you know, shows you its details, and logs the event. It is built to
be simple, honest, and out of the way.

## Features

- **Live device list** with each device's type, status, manufacturer, and VID:PID.
- **Port / Drive column** that shows the COM port for serial devices (ESP32, Arduino,
  CP210x, CH340, FTDI) and the drive letter for USB storage.
- **New-device alerts** three ways, each independently toggleable: a Windows toast, an
  in-app banner with a highlighted row, and a sound.
- **Unrecognized-device mode** that trusts the gear you already own and only alerts when
  an unfamiliar make/model appears. Right-click any device to trust, untrust, or hide it.
- **Dark, light, or follow-Windows theming**, an adjustable text size, and a live filter box.
- **ESP32 helpers**: right-click a serial device to copy its COM port or a ready-made
  esptool flash command.
- **Durable event history** written to `history.csv` and exportable anywhere.
- **System-tray operation**, plus a Quit button that fully exits when you want it gone.

## Download and run

### Ready-made app (easiest, no Python needed)

1. Open the [Releases](https://github.com/AxialForge/USBSentry/releases/latest) page.
2. Download `USBSentry.exe`.
3. Double-click it.

The first time you run a downloaded copy, Windows SmartScreen may show an
"unrecognized app" notice. See [Getting past the SmartScreen warning](#getting-past-the-smartscreen-warning).

### Run from source

Requires Python 3 with two small packages:

```
pip install -r requirements.txt
python usbwatch.py
```

Or double-click `USBSentry.bat`.

## Using it

| Action | How |
|---|---|
| See all connected devices | The main window |
| Read a device's COM port / drive | The Port / Drive column |
| Filter the list | Type in the Filter box |
| Trust / untrust / hide a device | Right-click it |
| Copy a COM port or esptool command | Right-click a serial device |
| See or edit trusted devices | Trusted devices button |
| Export the event log | Export log button |
| Change theme, text size, alerts | Settings |
| Fully exit (no tray) | Quit button |

## Settings

Open Settings to toggle each alert type, switch between the unrecognized-only and
all-new-devices alert modes, choose a theme (System / Light / Dark), pick a text size,
and set how often USBSentry scans the bus. Preferences are saved to `config.json`.

## Unrecognized-device mode

Turn on "Only alert for unrecognized devices" and USBSentry stops nagging you about your
own keyboard and mouse. Everything present at first launch is auto-trusted; after that,
only an unfamiliar model (matched by VID:PID) raises an alert and shows in red. Trusted
models are remembered in `known_devices.json`. Because matching is by model, moving your
own device to a different port never counts as new.

## Start automatically on boot

1. Press `Win + R`, type `shell:startup`, and press Enter.
2. Drop a shortcut to `USBSentry.exe` into that folder.

## Building the exe

```
pip install pyinstaller
python -m PyInstaller --noconsole --onefile --name "USBSentry" --icon "assets/USBSentry.ico" --add-data "assets;assets" --clean usbwatch.py
```

## Getting past the SmartScreen warning

USBSentry is not code-signed, so Windows SmartScreen shows "Windows protected your PC"
the first time you run a copy downloaded from the internet. It is an unrecognized-publisher
notice, not a virus warning. Any of these clears it:

- On the warning, click More info, then Run anyway (one time per download).
- Right-click `USBSentry.exe`, choose Properties, tick Unblock, then OK.
- Run from source instead, which never triggers SmartScreen.

A copy you build yourself locally has no "Mark of the Web" and runs without any warning.

## How it works

USBSentry runs Windows' built-in `Get-PnpDevice` command, filters the results to the USB
bus, and compares each scan to the previous one. Anything new triggers an alert; anything
missing is logged as removed. No drivers, no admin rights.

## Version history

See [CHANGELOG.md](CHANGELOG.md) for the full list of changes in each version.

## License

Copyright 2026 Joseph Costarella (AxialForge). Licensed under the
[Apache License 2.0](LICENSE). You may use, modify, and distribute it, including
commercially, provided you keep the copyright and license notices. The names "USBSentry"
and "AxialForge" are reserved. Provided "as is", without warranty of any kind.

# Changelog

All notable changes to USBSentry are documented here, newest first. This project
follows [Semantic Versioning](https://semver.org/) (MAJOR.MINOR.PATCH).

## [1.2.0] - in development

### Added
- Port / Drive column: shows the COM port for serial devices (ESP32, Arduino,
  CP210x, CH340, FTDI) and, for USB storage, the drive letter.
- ESP32 workflow: right-click a serial device to copy its COM port, its VID:PID,
  or a ready-made esptool flash command.
- Dark / light / follow-Windows theming, including a matching Windows title bar.
- Adjustable text size (Small to Extra-large).
- Live filter box to narrow the device list.
- Hide devices from the list, with a "Show hidden" toggle.
- In-app header showing the app name and version.
- Custom application icon across the window, taskbar, tray, and dialogs.
- Quit button that fully exits instead of hiding to the tray.

### Changed
- Column text is now centered; dialogs are resizable.

### Fixed
- Taskbar icon now shows the USBSentry icon instead of the default Python icon.
- Dark-theme borders and previously unreadable Settings dropdown text.

## [1.1.0] - 2026-07-10

### Added
- Unrecognized-device mode: trust the devices you already own and get alerted only
  when an unfamiliar make/model (VID:PID) is connected. Everything present at first
  launch is auto-trusted; right-click any device to trust or untrust it. Includes a
  Known? column, red highlighting for untrusted devices, and a Trusted devices manager.
- Version number shown in the window title and tray tooltip.

### Fixed
- Settings, event history, and the trusted-devices list were being written to a
  temporary folder when running the packaged exe, so they did not persist between
  runs. They now save correctly next to the app.

## [1.0.0] - 2026-07-09

### Added
- Initial release: live list of connected USB peripherals, alerts on new connections
  (toast, in-app banner, sound, each toggleable), "real peripherals only" filtering,
  durable event history with CSV export, and system-tray operation.

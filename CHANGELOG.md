# Changelog

All notable changes to USBSentry are documented here. This project follows
[Semantic Versioning](https://semver.org/) (MAJOR.MINOR.PATCH).

## [1.1.0] — 2026-07-10

### Added
- **Unrecognized-device mode** — trust the devices you already own and get
  alerted *only* when an unfamiliar make/model (VID:PID) is connected. Everything
  present at first launch is auto-trusted; right-click any device to trust or
  untrust it. Includes a "Known?" column, red highlighting for untrusted
  devices, and a **Trusted devices…** manager.
- Version number now shown in the window title and tray tooltip.

### Fixed
- Settings, event history, and the trusted-devices list were being written to a
  temporary folder when running the packaged `.exe` (PyInstaller `__file__`
  quirk), so they didn't persist between runs. They now save correctly next to
  the app.

## [1.0.0] — 2026-07-09

### Added
- Initial release: live list of connected USB peripherals, alerts on new
  connections (toast / in-app banner / sound, each toggleable), "real
  peripherals only" filtering, durable event history with CSV export, and
  system-tray operation.

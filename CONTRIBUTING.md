# Contributing to USBSentry

Thanks for your interest. USBSentry is a small, Windows-only USB monitor built
with Python and Tkinter. Bug reports, ideas, and pull requests are all welcome.

## Reporting bugs or ideas

Open an issue using the templates provided. For bugs, please include your
Windows version, the device involved (name and VID:PID if you have it), and what
you expected versus what actually happened.

## Running from source

Requires Python 3 on Windows.

```
pip install -r requirements.txt
python usbwatch.py
```

## Running the tests

```
python -m pytest -q
```

(or `python test_usbwatch.py`, which needs nothing beyond the app's own
dependencies.)

## Building the standalone exe

```
python -m PyInstaller --noconsole --onefile --name "USBSentry" --icon "assets/USBSentry.ico" --add-data "assets;assets" --clean usbwatch.py
```

## Code style

- Plain, readable Python; match the existing style in `usbwatch.py`.
- Keep it a single-file app where practical, and avoid heavy new dependencies.
- USBSentry is Windows-only by design (it relies on Windows device APIs).

## Pull requests

Keep changes focused, and describe what you changed and why. If your change is
user-facing, add a line to `CHANGELOG.md`.

## License

By contributing, you agree that your contributions are licensed under the
project's [Apache License 2.0](LICENSE).

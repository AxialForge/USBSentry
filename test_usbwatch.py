"""Unit tests for USBSentry's pure helper logic (no GUI, no hardware).

Run with:  python -m pytest -q      (or)      python test_usbwatch.py
"""

import usbwatch as u


def test_device_key_prefers_vid_pid():
    assert u.device_key({"vid_pid": "1234:5678", "instance_id": "X"}) == "1234:5678"
    assert u.device_key({"vid_pid": "", "instance_id": "USB\\FOO"}) == "USB\\FOO"


def test_serial_correlation():
    # A device's USB serial must match the same disk's USBSTOR serial.
    dev = u._serial_of(r"USB\VID_FFFF&PID_5678\HEADER12F9908655623")
    pnp = u._serial_of(r"USBSTOR\DISK&VEN_X&PROD_Y&REV_2.00\HEADER12F9908655623&0")
    assert dev == pnp == "HEADER12F9908655623"


def test_fmt_gb():
    assert u._fmt_gb(1024 ** 3) == "1.0 GB"
    assert u._fmt_gb(None) == ""
    assert u._fmt_gb("nonsense") == ""


def test_is_hub():
    assert u.is_hub("Generic USB Hub", r"USB\VID_0BDA&PID_5411\x") is True
    assert u.is_hub("USB Root Hub (USB 3.0)", r"USB\ROOT_HUB30\x") is True
    assert u.is_hub("NexiGo N60 FHD Webcam", r"USB\VID_1D6C&PID_0103\x") is False


def test_vidpid_regex():
    m = u._VIDPID_RE.search(r"USB\VID_303A&PID_4001&MI_00\6&x")
    assert m and m.group(1).upper() == "303A" and m.group(2).upper() == "4001"


def test_com_regex():
    assert u._COM_RE.search("USB Serial Device (COM3)").group(1) == "COM3"
    assert u._COM_RE.search("Some device") is None


def test_resolve_theme():
    assert u.resolve_theme("dark") == "dark"
    assert u.resolve_theme("light") == "light"
    assert u.resolve_theme("system") in ("light", "dark")


def test_serial_vids_known():
    assert "303A" in u._SERIAL_VIDS  # Espressif / ESP32


def test_config_has_expected_keys():
    for key in ("theme", "text_size", "sound_name", "alert_storage",
                "unrecognized_only", "watched", "hidden"):
        assert key in u.DEFAULT_CONFIG


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")

"""Make the macOS Dock / Cmd-Tab label say Dvorak instead of Python.

When the GUI is launched as ``python -m dvorak_gui``, the process is hosted by
Python.app, so the Dock tooltip is \"Python\" regardless of QApplication's
application name. Patching the main bundle's Info dictionary (and the process
name) before QApplication is constructed is what changes that label.
"""

from __future__ import annotations

import sys


def configure(name: str = "Dvorak") -> None:
    """Apply the Dock / Cmd-Tab name. Call before constructing QApplication."""
    if sys.platform != "darwin":
        return
    if not _via_pyobjc(name):
        _via_ctypes(name)


def _via_pyobjc(name: str) -> bool:
    try:
        from Foundation import NSBundle, NSProcessInfo
    except ImportError:
        return False

    try:
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = name
            info["CFBundleDisplayName"] = name
        NSProcessInfo.processInfo().setProcessName_(name)
        return True
    except Exception:
        return False


def _via_ctypes(name: str) -> None:
    """Fallback when pyobjc is not installed."""
    try:
        import ctypes
        import ctypes.util
    except Exception:
        return

    lib_name = ctypes.util.find_library("objc")
    if not lib_name:
        return

    objc = ctypes.cdll.LoadLibrary(lib_name)
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]

    msg_send = objc.objc_msgSend
    msg_send.restype = ctypes.c_void_p

    def _cls(n: str) -> int:
        return objc.objc_getClass(n.encode("utf-8"))

    def _sel(n: str) -> int:
        return objc.sel_registerName(n.encode("utf-8"))

    try:
        msg_send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
        ns_name = msg_send(
            _cls("NSString"),
            _sel("stringWithUTF8String:"),
            name.encode("utf-8"),
        )
        if not ns_name:
            return

        msg_send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        process_info = msg_send(_cls("NSProcessInfo"), _sel("processInfo"))
        if not process_info:
            return

        msg_send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        msg_send(process_info, _sel("setProcessName:"), ns_name)

        msg_send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        bundle = msg_send(_cls("NSBundle"), _sel("mainBundle"))
        if not bundle:
            return

        info = msg_send(bundle, _sel("localizedInfoDictionary"))
        if not info:
            info = msg_send(bundle, _sel("infoDictionary"))
        if not info:
            return

        for key in ("CFBundleName", "CFBundleDisplayName"):
            msg_send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
            ns_key = msg_send(
                _cls("NSString"),
                _sel("stringWithUTF8String:"),
                key.encode("utf-8"),
            )
            msg_send.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            msg_send(info, _sel("setObject:forKey:"), ns_name, ns_key)
    except Exception:
        return

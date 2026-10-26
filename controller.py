"""Optional radio button. Reads controller buttons with the same read-only Windows APIs any
game or launcher uses (XInput for Xbox/Steam-mapped pads, winmm joystick for DualSense over HID).
Nothing touches the game process, so it can't trip anti-cheat.

A combo is stored as one int: XInput buttons in the low 16 bits, joystick buttons shifted up 16.
"""
import ctypes
import threading
import time
from ctypes import wintypes

XINPUT_NAMES = {0x1: 'D-pad up', 0x2: 'D-pad down', 0x4: 'D-pad left', 0x8: 'D-pad right',
                0x10: 'Menu', 0x20: 'View', 0x40: 'L3', 0x80: 'R3', 0x100: 'LB', 0x200: 'RB',
                0x1000: 'A', 0x2000: 'B', 0x4000: 'X', 0x8000: 'Y'}


class _Pad(ctypes.Structure):
    _fields_ = [('buttons', wintypes.WORD), ('lt', wintypes.BYTE), ('rt', wintypes.BYTE),
                ('lx', wintypes.SHORT), ('ly', wintypes.SHORT), ('rx', wintypes.SHORT), ('ry', wintypes.SHORT)]


class _XState(ctypes.Structure):
    _fields_ = [('packet', wintypes.DWORD), ('pad', _Pad)]


class _JoyInfoEx(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('flags', wintypes.DWORD)] + \
               [(n, wintypes.DWORD) for n in ('x', 'y', 'z', 'r', 'u', 'v', 'buttons', 'button_number',
                                              'pov', 'res1', 'res2')]


def _load_xinput():
    for name in ('xinput1_4', 'xinput1_3', 'xinput9_1_0'):
        try:
            return ctypes.WinDLL(name).XInputGetState
        except OSError:
            pass
    return None


_xinput = _load_xinput()
_winmm = ctypes.WinDLL('winmm')
_joys = []          # connected joystick ids; polling missing ids is slow, so we scan rarely
_last_scan = 0.0


def pressed():
    global _joys, _last_scan
    mask = 0
    if _xinput:
        st = _XState()
        for i in range(4):
            if _xinput(i, ctypes.byref(st)) == 0:
                mask |= st.pad.buttons
    info = _JoyInfoEx(size=ctypes.sizeof(_JoyInfoEx), flags=0x80)  # JOY_RETURNBUTTONS
    if time.monotonic() - _last_scan > 10:
        _joys = [i for i in range(16) if _winmm.joyGetPosEx(i, ctypes.byref(info)) == 0]
        _last_scan = time.monotonic()
    for i in _joys:
        if _winmm.joyGetPosEx(i, ctypes.byref(info)) == 0:
            mask |= (info.buttons & 0xFFFF) << 16
    return mask


def describe(combo):
    names = [n for bit, n in XINPUT_NAMES.items() if combo & bit]
    names += [f'button {i + 1}' for i in range(16) if combo >> 16 & 1 << i]
    return ' + '.join(names) or 'nothing'


def capture(hold=1.5):
    """Wait until the same buttons are held for `hold` seconds and return that combo."""
    combo, since = 0, None
    while True:
        now = pressed()
        if now and now == combo:
            if time.monotonic() - since >= hold:
                return combo
        else:
            combo, since = now, time.monotonic()
        time.sleep(0.03)


def watch(combo, on_press, on_release=None):
    """Background thread: calls on_press once each time the full combo goes down,
    and on_release(seconds held) when it comes back up."""
    def loop():
        down, since = False, 0.0
        while True:
            now = pressed() & combo == combo
            if now and not down:
                since = time.monotonic()
                on_press()
            elif down and not now and on_release:
                on_release(time.monotonic() - since)
            down = now
            time.sleep(0.03)
    threading.Thread(target=loop, daemon=True).start()

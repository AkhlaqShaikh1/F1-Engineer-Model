"""Fake F1 25 UDP packets (2025 format), for the tests and the race simulator.

The fake track: 5 km, three braking zones (brake points below, apex 100 m later).
"""
import struct

import engineer as e

ZONES = (1000, 2500, 4000)
UID = 42   # session id; a new one makes the engineer start a new session
LENGTH = 5000


def hdr(pid, uid=None):
    return e.HEADER.pack(2025, 25, 1, 0, 1, pid, uid or UID, 0.0, 0, 0, 0, 255)


def session(stype, laps=10, uid=None, paused=0, track=7, weather=0):
    return hdr(1, uid) + e.SESSION.pack(weather, 30, 22, laps, LENGTH, stype, track, 0, 0, 0, 80, paused) + bytes(600)


def lap_pkt(num, last_ms=0, cur_ms=0, dist=0.0, pos=1, pit=0, status=4, uid=None, stops=0):
    cars = []
    for i in range(22):
        p = pos if i == 0 else (i + 1 if i + 1 != pos else 1)
        cars.append(e.LAP.pack(last_ms, int(cur_ms), 0, 0, 0, 0, 800, 0, 0, 0, dist, 0., 0., p, num, pit,
                               stops, *[0] * 8, status, 2, 0, 0, 0, 0, 0., 0))
    return hdr(2, uid) + b''.join(cars) + bytes(2)


def tele(speed, thr, brk, inner=(90, 90, 90, 90), fbrake=800, uid=None):
    car = e.TELEMETRY.pack(int(speed), thr, 0., brk, 0, 5, 10000, 0, 0, 0, 700, 700, fbrake, fbrake,
                           90, 90, 90, 90, *inner, 100, 23., 23., 23., 23., 0, 0, 0, 0)
    return hdr(6, uid) + car * 22 + bytes(3)


def motion_ex(front_slip=0.0, rear_slip=0.0, uid=None):
    return hdr(13, uid) + struct.pack('<16f', *[0.] * 16) + struct.pack('<4f', rear_slip, rear_slip,
                                                                        front_slip, front_slip) + bytes(160)


def setup(fw=25, on=60):
    car = e.SETUP.pack(fw, 20, on, 50, -3., -2., 0., 0., 20, 10, 10, 10, 30, 40, 100, 56, 50,
                       22., 22., 23., 23., 0, 20.)
    return hdr(5) + car * 22 + bytes(4)


def status(fuel_delta=0.2, ers=4e6, age=3, fuel=50., compound=16):
    car = e.STATUS.pack(0, 0, 1, 56, 0, fuel, 110., fuel_delta, 12000, 4000, 8, 0, 0, compound, compound, age, 0,
                        0., 0., ers, 1, 0., 0., 0., 0)
    return hdr(7) + car * 22


def damage(wear, wing=0):
    return hdr(10) + e.DAMAGE.pack(*[wear] * 4, *[0] * 12, wing, wing, *[0] * 16) * 22


def safety_car(kind, event):
    """kind 1 safety car, 2 virtual; event 0 deployed, 1 returning, 2 returned, 3 resume race."""
    return hdr(3) + b'SCAR' + bytes([kind, event]) + bytes(10)


def drive(eng, num, last_ms, early=0, lock_zone=None, inner=(90, 90, 90, 90), fbrake=800, pos=1, pit=0, stops=0):
    """One lap at 20 m steps into eng.handle. early: metres earlier braking into the second zone. Returns lap time ms."""
    t = 0.0
    for d in range(0, LENGTH, 20):
        speed, thr, brk, slip = 300.0, 1.0, 0.0, 0.0
        for k, z in enumerate(ZONES):
            bp = z - (early if k == 1 else 0)
            if bp <= d < z + 100:
                speed, thr, brk = 300 - 200 * (d - bp) / (z + 100 - bp), 0.0, 1.0
                slip = 0.3 if k == lock_zone else 0.0
            elif z + 100 <= d < z + 200:
                speed = 100 + 2 * (d - z - 100)
        eng.handle(lap_pkt(num, last_ms, t, float(d), pos=pos, pit=pit, stops=stops))
        eng.handle(motion_ex(front_slip=slip))
        eng.handle(tele(speed, thr, brk, inner, fbrake))
        t += 20 / (speed / 3.6) * 1000
    return int(t)

"""Drives fake F1 25 laps through the engineer and checks what it says. Run: python tests/test_engineer.py"""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import engineer as e

e.MEMORY = os.path.join(tempfile.mkdtemp(), 'tracks.json')
e.coach.MODELS = tempfile.mkdtemp()
said = []
TRACK = {'7': {'name': 'Silverstone', 'race_laps': 52, 'stops': 1, 'first_stop_frac': 0.4,
               'compounds': ['MEDIUM', 'HARD'], 'deg_s_per_lap': 0.12, 'deg': 'high',
               'full_throttle_pct': 66, 'heavy_brake_zones': 4, 'top_speed': 320, 'downforce': 'medium',
               'corners': [['1', 0.22], ['7', 0.52], ['15', 0.82]]}}
ZONES = (1000, 2500, 4000)   # brake points; apex 100 m later (turn 1, 7, 15 above)
LENGTH = 5000


def new_engineer():
    eng = e.Engineer(lambda t, key=None, every=0: said.append(t))
    eng.track_data = TRACK
    return eng


def hdr(pid, uid=42):
    return e.HEADER.pack(2025, 25, 1, 0, 1, pid, uid, 0.0, 0, 0, 0, 255)


def session(stype, laps=10, uid=42, paused=0):
    return hdr(1, uid) + e.SESSION.pack(0, 30, 22, laps, LENGTH, stype, 7, 0, 0, 0, 80, paused) + bytes(600)


def lap_pkt(num, last_ms=0, cur_ms=0, dist=0.0, pos=1, pit=0, status=4, uid=42):
    cars = []
    for i in range(22):
        p = pos if i == 0 else (i + 1 if i + 1 != pos else 1)
        cars.append(e.LAP.pack(last_ms, int(cur_ms), 0, 0, 0, 0, 800, 0, 0, 0, dist, 0., 0., p, num, pit,
                               *[0] * 9, status, 2, 0, 0, 0, 0, 0., 0))
    return hdr(2, uid) + b''.join(cars) + bytes(2)


def tele(speed, thr, brk, inner=(90, 90, 90, 90), fbrake=800, uid=42):
    car = e.TELEMETRY.pack(int(speed), thr, 0., brk, 0, 5, 10000, 0, 0, 0, 700, 700, fbrake, fbrake,
                           90, 90, 90, 90, *inner, 100, 23., 23., 23., 23., 0, 0, 0, 0)
    return hdr(6, uid) + car * 22 + bytes(3)


def motion_ex(front_slip=0.0, rear_slip=0.0, uid=42):
    return hdr(13, uid) + struct.pack('<16f', *[0.] * 16) + struct.pack('<4f', rear_slip, rear_slip,
                                                                        front_slip, front_slip) + bytes(160)


def setup(fw=25, on=60):
    car = e.SETUP.pack(fw, 20, on, 50, -3., -2., 0., 0., 20, 10, 10, 10, 30, 40, 100, 56, 50,
                       22., 22., 23., 23., 0, 20.)
    return hdr(5) + car * 22 + bytes(4)


def status(fuel_delta=0.2, ers=4e6):
    car = e.STATUS.pack(0, 0, 1, 56, 0, 50., 110., fuel_delta, 12000, 4000, 8, 0, 0, 16, 16, 3, 0,
                        0., 0., ers, 1, 0., 0., 0., 0)
    return hdr(7) + car * 22


def damage(wear, wing=0):
    return hdr(10) + e.DAMAGE.pack(*[wear] * 4, *[0] * 12, wing, wing, *[0] * 16) * 22


def drive(eng, num, last_ms, early=0, lock_zone=None, inner=(90, 90, 90, 90), fbrake=800, pos=1):
    """One lap at 20 m steps. early: metres earlier braking into turn 7. Returns lap time ms."""
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
        eng.handle(lap_pkt(num, last_ms, t, float(d), pos=pos))
        eng.handle(motion_ex(front_slip=slip))
        eng.handle(tele(speed, thr, brk, inner, fbrake))
        t += 20 / (speed / 3.6) * 1000
    return int(t)


def has(text):
    assert any(text in s for s in said), f'missing: {text}'


# ---- Time trial: corner coaching against your best lap ----
eng = new_engineer()
for p in [session(18), setup(), lap_pkt(1, pit=1)]:
    eng.handle(p)
t1 = drive(eng, 1, 0, inner=(95, 95, 85, 85))       # out lap, skipped (pit)
t2 = drive(eng, 2, t1, inner=(95, 95, 85, 85))      # reference lap
t3 = drive(eng, 3, t2, early=25)                     # braked 25 m early into turn 7
t4 = drive(eng, 4, t3, early=25, lock_zone=0, inner=(95, 95, 85, 85))  # again, plus a lock-up into turn 1
eng.handle(lap_pkt(5, t4))
print('\n'.join(said)); print('---')
has('Time trial, Silverstone')
has('Medium downforce, wings around 25 to 35')
has('Turn 7, lost 0.')
has('metres early')  # 20 m test sampling rounds the 25 m
has('Lock up into turn 1, brake a touch earlier')
assert not any('Fronts hotter' in s or 'Rears hotter' in s for s in said), 'setup advice must wait for the garage'

# setup tips only when paused / in garage
said.clear()
eng.handle(session(18, paused=1))
has('Setup for the next run. Rears hotter than fronts')
eng.report()
has('Copy. Best')

# ---- next session: it remembers you ----
said.clear()
for n in range(3):  # build the braking-early habit over clean laps
    eng.learn_habits([(0.2, {'apex': 2600}, {'lock': False, 'spin': False}, 'early')])
eng2 = new_engineer()
eng2.handle(session(18, uid=99))
print('\n'.join(said)); print('---')
has('Your best here is')
has('Watch turn 7, you keep braking early there')
assert eng2.ref is not None, 'all-time best lap loaded as reference'

# ---- Race: gaps, fuel, lift and coast before the big stops ----
said.clear()
eng = new_engineer()
for p in [session(15, laps=10), status(-0.5, ers=0.5e6), damage(56, wing=30), lap_pkt(1, pos=3)]:
    eng.handle(p)
r1 = drive(eng, 1, 0, pos=3, inner=(110, 110, 95, 95))
r2 = drive(eng, 2, r1, pos=3)
eng.handle(hdr(3) + b'SCAR' + bytes([1, 0]) + bytes(10))
for n in (3, 4):
    eng.handle(lap_pkt(n, r2, pos=3))
eng.report()
print('\n'.join(said))
has('Real race here was one stop, medium to hard. Window around lap 4.')
has('P3. Gap ahead 0.8')
has('DRS, push now')
has('Fuel minus 0.5')
has('Battery 12 percent, ERS to medium')
has('Tyres 56 percent')
has('Front wing damage, 30')
has('Rears hot, diff down a few on the MFD')
has('Lift and coast into turn')
has('save fuel')
has('Near the pit window')
has('In the pit window')
has('Copy. P3. Ahead 0.8')
assert not any('mix' in s for s in said), 'fuel mix is not a confirmed F1 25 control'

# ---- Live corner coach (only with PyTorch): learns clean laps, then calls a slow corner at the apex ----
if e.coach.torch:
    import time
    said.clear()
    eng = new_engineer()
    for p in [session(18), lap_pkt(1)]:
        eng.handle(p)
    t = 0
    for n in range(1, e.coach.MIN_LAPS + 2):  # reference lap, then MIN_LAPS laps of data
        t = drive(eng, n, t)
    eng.handle(lap_pkt(e.coach.MIN_LAPS + 2, t))
    while eng.coach.busy or eng.coach.net is None:
        time.sleep(0.1)
    assert not any('losing' in s for s in said), 'no live call on clean laps'
    drive(eng, e.coach.MIN_LAPS + 2, t, early=80)
    print('\n'.join(said))
    has('Turn 7, losing')
    assert sum('losing' in s for s in said) == 1, 'only the slow corner'
print('ALL OK')

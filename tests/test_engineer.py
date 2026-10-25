"""Feeds fake F1 25 packets through the engineer and checks what it says. Run: python tests/test_engineer.py"""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import engineer as e

e.MEMORY = os.path.join(tempfile.mkdtemp(), 'tracks.json')
said = []
eng = e.Engineer(lambda t, key=None, every=0: said.append(t))
eng.track_data = {'7': {'name': 'Silverstone', 'race_laps': 52, 'stops': 1, 'first_stop_frac': 0.4,
                        'compounds': ['MEDIUM', 'HARD'], 'deg_s_per_lap': 0.12, 'deg': 'high',
                        'full_throttle_pct': 66, 'heavy_brake_zones': 4, 'top_speed': 320,
                        'downforce': 'medium'}}


def hdr(pid, uid=42):
    return e.HEADER.pack(2025, 25, 1, 0, 1, pid, uid, 0.0, 0, 0, 0, 255)


def session(stype, laps=10, uid=42):
    return hdr(1, uid) + e.SESSION.pack(0, 30, 22, laps, 5000, stype, 7, 0, 0, 0) + bytes(600)


def lap(num, ms, pos=1, pit=0):
    cars = []
    for i in range(22):
        p = pos if i == 0 else (i + 1 if i + 1 != pos else 1)
        cars.append(e.LAP.pack(ms, 0, 0, 0, 0, 0, 800, 0, 0, 0, 0., 0., 0., p, num, pit,
                               *[0] * 10, 2, 0, 0, 0, 0, 0., 0))
    return hdr(2) + b''.join(cars) + bytes(2)


def tele(rear, front, fbrake=800):
    car = e.TELEMETRY.pack(200, 1., 0., 0., 0, 5, 10000, 0, 0, 0, 700, 700, fbrake, fbrake,
                           rear, rear, front, front, 90, 90, 90, 90, 100, 23., 23., 23., 23., 0, 0, 0, 0)
    return hdr(6) + car * 22 + bytes(3)


def setup(fw=25, on=60):
    car = e.SETUP.pack(fw, 20, on, 50, -3., -2., 0., 0., 20, 10, 10, 10, 30, 40, 100, 56, 50,
                       22., 22., 23., 23., 0, 20.)
    return hdr(5) + car * 22 + bytes(4)


def status(fuel_delta=0.2, ers=4e6):
    car = e.STATUS.pack(0, 0, 1, 56, 0, 50., 110., fuel_delta, 12000, 4000, 8, 0, 0, 16, 16, 3, 0,
                        0., 0., ers, 1, 0., 0., 0., 0)
    return hdr(7) + car * 22


def damage(wear, wing=0):
    car = e.DAMAGE.pack(*[wear] * 4, *[0] * 12, wing, wing, *[0] * 16)
    return hdr(10) + car * 22


def has(text):
    assert any(text in s for s in said), f'missing: {text}'


# Practice: briefing from real data, rear-hot diff advice, brake bias, setup change verdict
for p in [session(1), setup(), lap(1, 0, pit=1)]:
    eng.handle(p)
for _ in range(150): eng.handle(tele(rear=100, front=88))
eng.handle(lap(2, 0))                 # out lap done (skipped)
for _ in range(150): eng.handle(tele(rear=100, front=88, fbrake=1100))
eng.handle(lap(3, 91234))             # clean lap
eng.handle(setup(on=50))
for _ in range(150): eng.handle(tele(rear=92, front=91))
eng.handle(lap(4, 90900))
eng.report()
print('\n'.join(said)); print('---')
has('Practice at Silverstone')
has('Medium downforce track, 66 percent full throttle. Start with wings around 25 to 35.')
has('Tyre deg is high here')
has('On throttle diff is 60')
has('Move brake bias back')
has('on throttle diff 60 to 50')
has('0.3 faster')
has('Copy. Best lap 1 30.9. Tyre and brake temps look balanced')
assert os.path.exists(e.MEMORY)

# New practice session, same track: own best recalled
said.clear()
eng.handle(session(1, uid=99))
print('\n'.join(said)); print('---')
has('Last time here your best was 1 30.9')

# Race
said.clear()
for p in [session(15, laps=10), status(-0.5, ers=0.5e6), damage(56, wing=30), lap(1, 0, pos=3)]:
    eng.handle(p)
for _ in range(150): eng.handle(tele(rear=110, front=95))
eng.handle(lap(2, 92000, pos=3))
eng.handle(hdr(3) + b'SCAR' + bytes([1, 0]) + bytes(10))
for n in (3, 4): eng.handle(lap(n, 92000, pos=3))
eng.report()
print('\n'.join(said))
has('most cars did one stop, medium to hard. Tyre deg is high. Pit window around lap 4.')
has('P3. Gap ahead 0.8')
has('You have DRS')
has('Fuel minus 0.5. Lift and coast')
has('Battery 12 percent. ERS to medium')
has('Tyre wear 56 percent')
has('Front wing damage, 30')
has('Rears overheating')
has('Safety car deployed')
has('Cheap stop under the safety car')
has('We are in the pit window')
has('Copy. P3. Ahead 0.8')
assert not any('mix' in s for s in said), 'fuel mix is not a confirmed F1 25 control'
print('ALL OK')

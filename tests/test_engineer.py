"""Drives fake F1 25 laps through the engineer and checks what it says. Run: python tests/test_engineer.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import engineer as e
from fakegame import damage, drive, hdr, lap_pkt, session, setup, status

e.MEMORY = os.path.join(tempfile.mkdtemp(), 'tracks.json')
e.coach.MODELS = tempfile.mkdtemp()
e.session_report.REPORTS = tempfile.mkdtemp()
said = []
TRACK = {'7': {'name': 'Silverstone', 'race_laps': 52, 'stops': 1, 'first_stop_frac': 0.4,
               'compounds': ['MEDIUM', 'HARD'], 'deg_s_per_lap': 0.12, 'deg': 'high',
               'full_throttle_pct': 66, 'heavy_brake_zones': 4, 'top_speed': 320, 'downforce': 'medium',
               'corners': [['1', 0.22], ['7', 0.52], ['15', 0.82]]}}


def new_engineer():
    eng = e.Engineer(lambda t, key=None, every=0: said.append(t))
    eng.track_data = TRACK
    return eng


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

# ---- Theoretical best: best lap plus your best gain in every corner ----
eng = new_engineer()
eng.ref_best, eng.gains = 80000, {'turn 1': -0.4, 'turn 7': -0.1, 'turn 15': 0.0}
assert eng.theoretical() == (79500, ['turn 1', 'turn 7']), eng.theoretical()

# ---- Race strategy from your own tyre deg, and talk-back answers ----
said.clear()
eng = new_engineer()
for p in [session(15, laps=30), damage(20), lap_pkt(1)]:
    eng.handle(p)
for n in range(2, 9):  # tyres lose 0.2 s a lap; a 3 s mistake on lap 5 must not count as deg
    eng.handle(status(age=n - 1))
    eng.handle(lap_pkt(n, 90000 + 200 * (n - 2) + (3000 if n == 6 else 0)))
print('\n'.join(said))
assert abs(eng.deg - 0.2) < 1e-6, eng.deg
has('Your tyres are losing 0.20 a lap. Best stop around lap')
assert eng.pit_window() == eng.own_window, 'your own deg replaces the real-race pit window'
said.clear()
eng.answer('how are the tyres')
eng.answer('when do we box')
eng.answer('mumble mumble')
print('\n'.join(said))
has('Copy. Tyres 20 percent, losing 0.20 a lap.')
has(f'Copy. Box around lap {eng.own_window}.')
has('Copy. P1.')  # anything else: the usual update
said.clear()
for n in range(9, eng.own_window + 1):  # drive on to the computed stop lap
    eng.handle(status(age=n - 1))
    eng.handle(lap_pkt(n, 91400))
has('Pit window in 2 laps')
assert said[-1].startswith('Box, box. Pit this lap.'), said[-1]

# ---- Session report pages ----
pages = os.listdir(e.session_report.REPORTS)
tt = [f for f in pages if 'Silverstone_Time_trial' in f]
assert tt and 'Average per corner' in open(os.path.join(e.session_report.REPORTS, tt[0]), encoding='utf-8').read()
assert any('Silverstone_Race' in f for f in pages), pages
print('ALL OK')

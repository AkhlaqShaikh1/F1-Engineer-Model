"""The race engineer brain: parses F1 25 UDP telemetry (2025 format) and decides what to say.

On track it only talks about things you can change while driving: brake bias, differential and
ERS on the MFD, and driving technique (braking points, apex speed, lift and coast).
Setup menu changes are saved up and read out when you are in the garage or the game is paused.

It learns you: per track it keeps your all-time best lap corner by corner and your habits
per corner (early braking, lock-ups, wheelspin, late throttle) in tracks.json.
"""
import bisect
import json
import os
import struct
import threading
import time

import coach
import session_report

CARS = 22
HEADER = struct.Struct('<HBBBBBQfIIBB')                 # 29 bytes
SESSION = struct.Struct('<BbbBHBbBHHBB')                # first fields of the session packet only
LAP = struct.Struct('<IIHBHBHBHBfff15BHHBfB')            # 57 bytes per car
TELEMETRY = struct.Struct('<HfffBbHBBH4H4B4BH4f4B')      # 60 bytes per car
STATUS = struct.Struct('<5B3f2H2BH3Bb3fB3fB')            # 55 bytes per car
SETUP = struct.Struct('<4B4f9B4fBf')                     # 50 bytes per car
DAMAGE = struct.Struct('<4f4B4B4B18B')                   # 46 bytes per car (F1 25 added tyre blisters)
SLIP_OFFSET = 29 + 4 * 16                                # motion ex: wheel slip ratio after 4 float[4] arrays
assert (HEADER.size, LAP.size, TELEMETRY.size, STATUS.size, SETUP.size, DAMAGE.size) == (29, 57, 60, 55, 50, 46)

ERS_MAX = 4e6  # joules
# Wheel order in every array: RL, RR, FL, FR
SETUP_NAMES = ('front wing', 'rear wing', 'on throttle diff', 'off throttle diff', 'front camber',
               'rear camber', 'front toe', 'rear toe', 'front suspension', 'rear suspension',
               'front anti roll bar', 'rear anti roll bar', 'front ride height', 'rear ride height',
               'brake pressure', 'brake bias', 'engine braking', 'rear left pressure',
               'rear right pressure', 'front left pressure', 'front right pressure', 'ballast')
WEATHER = ('clear', 'light cloud', 'overcast', 'light rain', 'heavy rain', 'storm')
SAFETY_CAR = {1: 'Safety car', 2: 'Virtual safety car', 3: 'Formation lap'}
TRACKS = {0: 'Melbourne', 2: 'Shanghai', 3: 'Bahrain', 4: 'Barcelona', 5: 'Monaco', 6: 'Montreal',
          7: 'Silverstone', 9: 'Hungaroring', 10: 'Spa', 11: 'Monza', 12: 'Singapore', 13: 'Suzuka',
          14: 'Abu Dhabi', 15: 'Austin', 16: 'Interlagos', 17: 'Austria', 19: 'Mexico', 20: 'Baku',
          26: 'Zandvoort', 27: 'Imola', 29: 'Jeddah', 30: 'Miami', 31: 'Las Vegas', 32: 'Qatar',
          39: 'Silverstone reverse', 40: 'Austria reverse', 41: 'Zandvoort reverse'}
WING_RANGE = {'low': '10 to 20', 'medium': '25 to 35', 'high': '40 to 50'}
HABITS = {'early': 'braking early', 'lock': 'locking up', 'spin': 'wheelspin on exit',
          'power': 'late on the power', 'apex': 'too slow at the apex'}
HERE = os.path.dirname(os.path.abspath(__file__))
MEMORY = os.path.join(HERE, 'tracks.json')          # what it learned about you, per track
TRACK_DATA = os.path.join(HERE, 'track_data.json')  # real race data, built by build_track_data.py

# trace sample: (lap distance m, lap time ms, speed kph, throttle 0-1, brake 0-1, front lock, rear spin)
LOCK_SLIP = 0.15   # shortcut: slip thresholds are first guesses, tune from real laps
SPIN_SLIP = 0.15
LIVE_LOSS = 0.15   # seconds a corner must be heading to lose before the live call
FUEL_S_PER_KG = 0.03  # shortcut: textbook fuel effect, upgrade to fitting it from your laps
MISTAKE = 1.5         # seconds over the stint's median lap: a mistake or traffic, not tyre deg
PIT_LOSS = 22.0      # shortcut: one pit loss for every track, upgrade to per-track values in track_data.json
COMPOUND_GRIP = {16: 0.0, 17: 0.5, 18: 1.0}  # visual compound: soft, medium, hard
WET_COMPOUNDS = (7, 8)                        # inter, wet


def load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_memory(mem):
    with open(MEMORY, 'w', encoding='utf-8') as f:
        json.dump(mem, f)


def lap_words(ms):
    m, s = divmod(ms / 1000, 60)
    return f"{int(m)} {s:04.1f}" if m else f"{s:.1f}"


def gap(car):
    return (car[7] * 60000 + car[6]) / 1000  # delta to car in front, minutes part + ms part


def zones(trace):
    """Braking zones of one lap: brake point, apex (min speed), power point, lock-up and wheelspin."""
    out, i, n = [], 1, len(trace)
    while i < n:
        if trace[i][4] > 0.2 >= trace[i - 1][4]:
            j = i
            while j < n and not (trace[j][3] > 0.8 and trace[j][4] < 0.05):
                j += 1
            if j >= n:
                break
            seg = trace[i:j + 1]
            apex = min(seg, key=lambda s: s[2])
            k = j
            while k < n and trace[k][0] < trace[j][0] + 150 and trace[k][4] <= 0.2:
                k += 1
            if trace[i][2] - apex[2] >= 25:
                out.append({'brake': trace[i][0], 'apex': apex[0], 'power': trace[j][0],
                            'entry': trace[i][2], 'vmin': apex[2],
                            'lock': any(s[5] for s in seg), 'spin': any(s[6] for s in trace[j:k])})
            i = j
        i += 1
    return out


def make_lap(trace):
    """Keep samples with rising distance (flashbacks and resets go backwards) and index them."""
    clean, last = [], -1e9
    for s in trace:
        if s[0] > last:
            clean.append(s)
            last = s[0]
    return {'trace': clean, 'dists': [s[0] for s in clean], 'zones': zones(clean)}


def time_at(lap, d):
    ds, tr = lap['dists'], lap['trace']
    k = bisect.bisect_left(ds, d)
    if k <= 0:
        return tr[0][1]
    if k >= len(ds):
        return tr[-1][1]
    d0, d1 = ds[k - 1], ds[k]
    return tr[k - 1][1] + (tr[k][1] - tr[k - 1][1]) * (d - d0) / ((d1 - d0) or 1)


def compare(cur, ref):
    """Per reference corner: (seconds lost, ref zone, matching current zone or None, habit key or None)."""
    out = []
    for rz in ref['zones']:
        a, b = rz['brake'] - 60, rz['power'] + 60
        loss = ((time_at(cur, b) - time_at(cur, a)) - (time_at(ref, b) - time_at(ref, a))) / 1000
        cz = min(cur['zones'], key=lambda z: abs(z['apex'] - rz['apex']), default=None)
        if cz and abs(cz['apex'] - rz['apex']) > 80:
            cz = None
        habit = None
        if cz:
            if cz['brake'] - rz['brake'] < -10:
                habit = 'early'
            elif cz['vmin'] < rz['vmin'] - 3:
                habit = 'apex'
            elif cz['power'] - rz['power'] > 15:
                habit = 'power'
        out.append((loss, rz, cz, habit))
    return out


def reason(rz, cz, habit):
    if habit == 'early':
        return f"braked {rz['brake'] - cz['brake']:.0f} metres early"
    if habit == 'apex':
        if cz['brake'] - rz['brake'] > 10:
            return "braked too late and overshot it"
        return f"{rz['vmin'] - cz['vmin']:.0f} k slower at the apex"
    if habit == 'power':
        return "late on the power"
    return ""


class Radio:
    """Prints, speaks, and hands each message to on_say (the overlay)."""

    def __init__(self, voice=None, on_say=None):
        self.last = {}
        self.lock = threading.Lock()
        self.on_say = on_say
        self.voice = voice  # anything with .say(text), see voice.py; None = text only

    def __call__(self, text, key=None, every=0):
        with self.lock:  # telemetry thread and radio button thread both talk
            self.speak(text, key, every)

    def speak(self, text, key, every):
        now = time.monotonic()
        if key and now - self.last.get(key, -1e9) < every:
            return
        if key:
            self.last[key] = now
        print(time.strftime('%H:%M:%S'), text, flush=True)
        if self.on_say:
            self.on_say(text)
        if self.voice:
            try:
                self.voice.say(text)
            except OSError:
                self.voice = None


class Engineer:
    def __init__(self, say):
        self.say = say
        self.track_data = load_json(TRACK_DATA)
        self.reset(None)

    def reset(self, uid):
        self.uid = uid
        self.session_type = None
        self.track = None
        self.track_len = 0
        self.total_laps = 0
        self.weather = None
        self.paused = False
        self.lap = 0
        self.skip_lap = True      # out laps, pit laps, invalid laps and flashbacks get no lap verdict
        self.clear_samples()
        self.status = self.setup = self.wear = None
        self.cars = None
        self.me = 0
        self.dist = -1.0
        self.lap_ms = 0
        self.speed = 0
        self.slip = (0.0, 0.0, 0.0, 0.0)
        self.in_garage = False
        self.wing = 0
        self.best = None
        self.ref = None           # best lap of this session (or all-time, from memory) for corner compare
        self.ref_best = None
        self.last_lap = None      # last completed lap, for lift and coast points in the race
        self.lnc_reason = None
        self.lnc_done = set()
        self.coach = None         # live corner predictor (coach.py), only when PyTorch is installed
        self.live_done = set()
        self.gains = {}           # corner -> best time gained there against the reference lap, for the theoretical best
        self.theo_said = None
        self.stint = []           # race laps on this set of tyres: (tyre age, lap ms, fuel kg)
        self.deg = None           # your tyre deg, seconds per lap, fitted from the stint
        self.own_window = None    # best pit lap from your own deg; 0 = no stop needed
        self.sc = False
        self.laps_log = []        # per lap, for the session report
        self.started = time.strftime('%Y-%m-%d_%H%M')
        self.setup_best = {}
        self.compare_to = None
        self.wear_called = set()
        self.setup_tips = None    # garage advice waiting for the garage
        self.last_tip = None
        self.streak = {}          # corner -> (habit, laps in a row)

    def clear_samples(self):
        self.inner = [0.0] * 4
        self.brake_max = [0] * 4
        self.n = 0
        self.trace = []

    def mode(self):
        t = self.session_type
        if t is None:
            return None
        if 5 <= t <= 14:
            return 'quali'
        if 15 <= t <= 17:
            return 'race'
        return 'practice'  # practice, time trial (18) and anything unlabelled get coaching

    def info(self):
        return self.track_data.get(self.track or '')

    def pit_window(self):
        if self.own_window is not None:
            return self.own_window or None
        if self.cars and self.cars[self.me][16]:
            return None  # already stopped: the real-race window is behind us
        info = self.info()
        if not info or not info.get('stops') or not self.total_laps:
            return None
        return max(2, round(info['first_stop_frac'] * self.total_laps))

    def corner(self, d):
        info = self.info()
        if info and info.get('corners') and self.track_len:
            f = (d % self.track_len) / self.track_len
            return 'turn ' + min(info['corners'], key=lambda c: min(abs(c[1] - f), 1 - abs(c[1] - f)))[0]
        zs = self.ref['zones'] if self.ref else []
        return f"turn {min(range(len(zs)), key=lambda k: abs(zs[k]['apex'] - d), default=0) + 1}"

    def handle(self, data):
        if len(data) < HEADER.size:
            return
        h = HEADER.unpack_from(data)
        pid, uid, me = h[5], h[6], h[10]
        if uid != self.uid:
            self.reset(uid)
        fn = {1: self.on_session, 2: self.on_lap, 3: self.on_event, 5: self.on_setup,
              6: self.on_telemetry, 7: self.on_status, 10: self.on_damage, 13: self.on_motion_ex}.get(pid)
        if fn and me < CARS:
            self.me = me
            fn(data, me)

    # ---------- memory: what it learned about you ----------

    def memory(self):
        return load_json(MEMORY).get(self.track) or {}

    def remember(self, **fields):
        mem = load_json(MEMORY)
        mem.setdefault(self.track, {}).update(fields)
        save_memory(mem)

    def learn_habits(self, comp):
        """Count, per corner, how often each habit shows up on your clean laps."""
        habits = self.memory().get('habits', {})
        for _, rz, cz, habit in comp:
            name = self.corner(rz['apex'])
            h = habits.setdefault(name, {'laps': 0})
            h['laps'] += 1
            for key in [habit] + [k for k in ('lock', 'spin') if cz and cz[k]]:
                if key:
                    h[key] = h.get(key, 0) + 1
        self.remember(habits=habits)

    def worst_habit(self):
        best = None
        for name, h in self.memory().get('habits', {}).items():
            if h['laps'] < 3:
                continue
            for key in HABITS:
                share = h.get(key, 0) / h['laps']
                if share >= 0.5 and (not best or share > best[0]):
                    best = (share, name, key)
        return best

    # ---------- packets ----------

    def on_session(self, data, me):
        s = SESSION.unpack_from(data, 29)
        weather, track_temp = s[0], s[1]
        first = self.session_type is None
        self.session_type, self.total_laps, self.track, self.track_len = s[5], s[3], str(s[6]), s[4]
        paused = bool(s[11])
        if paused and not self.paused:
            self.give_setup_tips()
        self.paused = paused
        if first:
            if coach.torch:
                self.coach = coach.Coach(self.track)
            self.briefing(track_temp)
        if self.weather is not None and weather != self.weather and weather < len(WEATHER):
            msg = f"Weather changing to {WEATHER[weather]}."
            if weather >= 3 > self.weather:
                msg += " Rain coming, think about inters."
            elif weather < 3 <= self.weather:
                msg += " Track drying, slicks soon."
            self.say(msg)
        self.weather = weather

    def on_motion_ex(self, data, me):
        self.slip = struct.unpack_from('<4f', data, SLIP_OFFSET)

    def on_telemetry(self, data, me):
        t = TELEMETRY.unpack_from(data, 29 + me * TELEMETRY.size)
        self.speed = t[0]
        if self.dist >= 0:
            lock = t[3] > 0.3 and max(abs(self.slip[2]), abs(self.slip[3])) > LOCK_SLIP
            spin = t[1] > 0.5 and t[3] < 0.05 and max(abs(self.slip[0]), abs(self.slip[1])) > SPIN_SLIP
            self.trace.append((self.dist, self.lap_ms, t[0], t[1], t[3], lock, spin))
        if t[0] < 60:  # ignore pit lane and spins for temperatures
            return
        for i in range(4):
            self.inner[i] += t[18 + i]  # inner (carcass) temps: steady, and what the MFD shows
            self.brake_max[i] = max(self.brake_max[i], t[10 + i])
        self.n += 1

    def on_status(self, data, me):
        self.status = STATUS.unpack_from(data, 29 + me * STATUS.size)

    def on_setup(self, data, me):
        s = SETUP.unpack_from(data, 29 + me * SETUP.size)[:22]  # drop fuel load, it is not a car balance change
        s = tuple(round(x, 2) for x in s)
        if not any(s):
            return
        if self.setup and s != self.setup and self.mode() == 'practice':
            changes = [f"{SETUP_NAMES[i]} {self.setup[i]:g} to {s[i]:g}"
                       for i in range(22) if s[i] != self.setup[i]]
            self.say("Copy, " + ", ".join(changes[:3]) + ".", 'setup_change', 5)
            self.compare_to = self.setup_best.get(self.setup)
        self.setup = s

    def on_damage(self, data, me):
        d = DAMAGE.unpack_from(data, 29 + me * DAMAGE.size)
        self.wear = d[:4]
        wing = max(d[16], d[17])  # front left / front right wing damage
        if wing - self.wing >= 15:
            self.say(f"Front wing damage, {wing} percent." +
                     (" Box for a new nose." if wing >= 40 else " We can live with it."))
        self.wing = wing
        if max(self.wear) < 5:
            self.wear_called.clear()  # fresh tyres after a stop

    def on_lap(self, data, me):
        cars = [LAP.unpack_from(data, 29 + i * LAP.size) for i in range(CARS)]
        self.cars = cars
        mine = cars[me]
        lap_num = mine[14]
        if self.lap and lap_num == self.lap + 1:
            self.lap_done(mine, cars)
        if lap_num != self.lap:
            self.lap = lap_num
            self.skip_lap = mine[15] != 0 or self.sc
            self.clear_samples()
            self.lnc_done = set()
            self.live_done = set()
        if mine[15] != 0 or mine[18]:  # in pit lane, or lap invalid
            self.skip_lap = True
        self.dist, self.lap_ms = mine[10], mine[1]
        garage = mine[25] == 0  # driverStatus 0 = in garage
        if garage and not self.in_garage:
            self.give_setup_tips()
        self.in_garage = garage
        if self.mode() == 'race':
            self.lift_and_coast()
        elif self.coach and self.ref and not self.skip_lap:
            self.live_corner()

    def on_event(self, data, me):
        code = data[29:33]
        if code == b'LGOT':
            self.say("Lights out, go go go!")
        elif code == b'DRSE':
            self.say("DRS enabled.")
        elif code == b'DRSD':
            self.say("DRS disabled.")
        elif code == b'FLBK':
            self.skip_lap = True  # a flashback lap is not a fair corner comparison
        elif code == b'SCAR':
            name, ev = SAFETY_CAR.get(data[33], 'Safety car'), data[34]
            self.sc = ev in (0, 1)
            self.skip_lap = True  # a safety car lap says nothing about your tyres
            if ev == 0:
                self.say(f"{name}. Slow down, keep the delta positive, keep tyre temps up.")
                window = self.pit_window()
                if self.mode() == 'race' and window and abs(self.lap - window) <= 3:
                    self.say("Near the pit window. Cheap stop if you want it.")
            elif ev == 1:
                self.say(f"{name} ending. Get ready.")
            elif ev == 3:
                self.say("Green flag, push now.")
        elif code == b'FTLP' and data[33] == me:
            self.say("Fastest lap. Purple.")
        elif code == b'PENA' and data[35] == me:
            kind, secs = data[33], data[37]
            msg = {0: "Drive through penalty.", 1: "Stop go penalty.",
                   4: f"{secs} second time penalty.", 5: "Track limits warning."}.get(kind)
            if msg:
                self.say(msg)
        elif code == b'CHQF':
            self.say("Chequered flag. Good job mate.")

    # ---------- the thinking ----------

    def briefing(self, track_temp):
        mode = self.mode()
        info = self.info()
        name = (info or {}).get('name') or TRACKS.get(int(self.track), 'this track')
        mem = self.memory()
        if mode == 'practice':
            label = 'Time trial' if self.session_type == 18 else 'Practice'
            msg = f"Radio check. {label}, {name}, track {track_temp} degrees."
            if info:
                msg += f" {info['downforce'].capitalize()} downforce, wings around {WING_RANGE[info['downforce']]}."
            if mem.get('best'):
                msg += f" Your best here is {lap_words(mem['best'])}."
            habit = self.worst_habit()
            if habit:
                msg += f" Watch {habit[1]}, you keep {HABITS[habit[2]]} there."
            self.say(msg)
            if mem.get('ref'):
                self.ref = make_lap([tuple(s) + (False, False) for s in mem['ref']])
                self.ref_best = mem['best']
        elif mode == 'quali':
            self.say("Radio check. Qualifying. Warm the tyres on the out lap.")
        elif mode == 'race':
            msg = f"Radio check. {self.total_laps} laps."
            window = self.pit_window()
            if info:
                plan = ' to '.join(c.lower() for c in info['compounds'])
                msg += f" Real race here was {'one stop' if info['stops'] == 1 else str(info['stops']) + ' stops'}"
                msg += f", {plan}." if plan else "."
                if window:
                    msg += f" Window around lap {window}."
            self.say(msg + " Good start.")

    def lap_done(self, mine, cars):
        mode = self.mode()
        ms = mine[0]
        lap = make_lap(self.trace) if len(self.trace) > 200 else None
        temps = [x / self.n for x in self.inner] if self.n >= 100 else None
        if lap and not self.skip_lap:
            self.last_lap = lap
        if mode in ('practice', 'quali') and ms and not self.skip_lap:
            self.lap_call(ms, lap)
        if temps and mode == 'practice':
            self.setup_tips = self.garage_advice(temps)
        if mode == 'race':
            self.race_call(mine, cars, temps)
        if self.laps_log:
            session_report.write(self)

    def lap_call(self, ms, lap):
        parts = [lap_words(ms) + ("." if self.best is None else
                                  ", best lap." if ms < self.best else f", plus {(ms - self.best) / 1000:.1f}.")]
        if lap and self.ref:
            comp = compare(lap, self.ref)
            self.learn_habits(comp)
            if self.coach and self.track_len:
                self.coach.add_lap(lap, self.ref['zones'], self.track_len, self.car_state())
            for loss, rz, _, _ in comp:
                name = self.corner(rz['apex'])
                self.gains[name] = min(self.gains.get(name, 0.0), loss)
            theo = self.theoretical()
            if theo and self.ref_best - theo[0] >= 300 and (not self.theo_said or self.theo_said - theo[0] >= 100):
                self.theo_said = theo[0]
                parts.append(f"Theoretical best {lap_words(theo[0])}, time left in {' and '.join(theo[1])}.")
            self.laps_log.append({'lap': self.lap, 'ms': ms, 'losses': {self.corner(c[1]['apex']): round(c[0], 2)
                                                                           for c in comp}})
            parts += self.corner_calls(comp, lap)
        elif lap:
            self.laps_log.append({'lap': self.lap, 'ms': ms, 'losses': {}})
            parts += self.mfd_calls(lap)
        if self.setup and self.compare_to:
            d = (self.compare_to - ms) / 1000
            parts.append(f"Setup change {abs(d):.1f} {'faster' if d > 0 else 'slower'}.")
            self.compare_to = None
        if self.setup:
            self.setup_best[self.setup] = min(ms, self.setup_best.get(self.setup, ms))
        if self.best is None or ms < self.best:
            self.best = ms
        if lap and (self.ref_best is None or ms < self.ref_best):
            self.ref, self.ref_best = lap, ms
            self.gains, self.theo_said = {}, None  # gains were measured against the old reference
            mem = self.memory()
            if not mem.get('best') or ms < mem['best']:
                if mem.get('best'):
                    parts.append(f"New personal best here, {(mem['best'] - ms) / 1000:.1f} up.")
                self.remember(best=ms, setup=list(self.setup or ()),
                              ref=[[round(s[0], 1), s[1], s[2], round(s[3], 2), round(s[4], 2)]
                                   for s in lap['trace'][::2]])
        self.last_tip = " ".join(parts[1:]) or self.last_tip
        self.say(" ".join(parts))

    def corner_calls(self, comp, lap):
        out = []
        worst = max(comp, key=lambda c: c[0], default=None)
        gain = min(comp, key=lambda c: c[0], default=None)
        if worst and worst[0] >= 0.08:
            loss, rz, cz, habit = worst
            name = self.corner(rz['apex'])
            why = reason(rz, cz, habit)
            out.append(f"{name.capitalize()}, lost {loss:.1f}" + (f", {why}." if why else "."))
            prev = self.streak.get(name, (None, 0))
            self.streak = {name: (habit, prev[1] + 1 if prev[0] == habit else 1)}
            if habit and self.streak[name][1] >= 3:
                out.append(f"That's {self.streak[name][1]} laps in a row.")
        if gain and gain[0] <= -0.08:
            out.append(f"Gained {abs(gain[0]):.1f} in {self.corner(gain[1]['apex'])}.")
        return out + self.mfd_calls(lap)

    def mfd_calls(self, lap):
        """Things fixable from the cockpit: lock-ups and wheelspin."""
        out = []
        locks = [self.corner(z['apex']) for z in lap['zones'] if z['lock']]
        spins = [self.corner(z['apex']) for z in lap['zones'] if z['spin']]
        if len(locks) >= 2:
            out.append(f"Locking up into {' and '.join(locks[:2])}, bias back one on the MFD.")
        elif locks:
            out.append(f"Lock up into {locks[0]}, brake a touch earlier.")
        if len(spins) >= 2:
            out.append(f"Wheelspin out of {' and '.join(spins[:2])}, diff down a few on the MFD.")
        elif spins:
            out.append(f"Wheelspin out of {spins[0]}, short shift there.")
        return out

    def garage_advice(self, temps):
        """Setup menu advice, held until the garage or pause menu."""
        rear, front = (temps[0] + temps[1]) / 2, (temps[2] + temps[3]) / 2
        s = self.setup
        tips = []
        if front - rear > 6:
            tip = "Fronts hotter than rears, it's understeering. One or two clicks more front wing"
            tips.append(tip + (f", or off throttle diff down from {s[3]:g}." if s and s[3] > 40 else "."))
        elif rear - front > 6:
            tip = "Rears hotter than fronts, that's wheelspin and oversteer. More rear wing"
            tips.append(tip + (f", or on throttle diff down from {s[2]:g}." if s and s[2] > 20 else "."))
        avg = sum(temps) / 4
        if avg > 105:
            tips.append(f"Tyres running hot at {avg:.0f}, tyre pressures down a click.")
        elif avg < 80:
            tips.append(f"Tyres cold at {avg:.0f}, tyre pressures up a click.")
        if max(self.brake_max[2:]) > 1000:
            tips.append("Front brakes overheating, brake pressure down a little.")
        return " ".join(tips[:2]) or None

    def give_setup_tips(self):
        if self.setup_tips and self.mode() == 'practice':
            self.say("Setup for the next run. " + self.setup_tips)
            self.setup_tips = None

    def lift_and_coast(self):
        """Live call before the two biggest stops when fuel, brakes or tyres need saving."""
        if not self.lnc_reason or not self.last_lap:
            return
        heavy = sorted(self.last_lap['zones'], key=lambda z: z['entry'] - z['vmin'], reverse=True)[:2]
        for i, z in enumerate(heavy):
            if i not in self.lnc_done and z['brake'] - 300 <= self.dist <= z['brake'] - 150:
                self.lnc_done.add(i)
                self.say(f"Lift and coast into {self.corner(z['apex'])}, {self.lnc_reason}.")

    def car_state(self):
        """What changes lap time but isn't your driving: fuel 0-1, tyre wear 0-1, compound grip, wet."""
        st = self.status
        compound = st[14] if st else 17
        wet = compound in WET_COMPOUNDS or (self.weather or 0) >= 3
        return (st[5] / 110 if st else 0.5, max(self.wear) / 100 if self.wear else 0.0,
                COMPOUND_GRIP.get(compound, 0.5), 1.0 if wet else 0.0)

    def theoretical(self):
        """(reference lap ms plus your best gain in every corner, the two corners with the most left in them)."""
        if not self.ref_best or not self.gains:
            return None
        gains = sorted(self.gains.items(), key=lambda g: g[1])
        return self.ref_best + sum(g for _, g in gains) * 1000, [n for n, g in gains[:2] if g <= -0.05]

    def live_corner(self):
        """At the apex the coach net predicts your corner exit time, and what you usually do from the
        corner entry in this car state (fuel, tyres, wet). Call it if this corner is going slower."""
        if not self.trace or not self.track_len:
            return
        d, t, speed, thr, brk = self.trace[-1][:5]
        state = self.car_state()
        for i, z in enumerate(self.ref['zones']):
            if i in self.live_done or not z['apex'] <= d < z['apex'] + 30:
                continue
            self.live_done.add(i)
            a, b = z['brake'] - 60, z['power'] + 60
            entry = next((s for s in reversed(self.trace) if s[0] <= a), None)
            left = self.coach.predict(d / self.track_len, b - d, speed, thr, brk, state)
            if left is None or entry is None:
                continue
            usual = self.coach.predict(entry[0] / self.track_len, b - entry[0], *entry[2:5], state)
            loss = (t - entry[1]) / 1000 + left - usual
            if loss >= LIVE_LOSS:
                self.say(f"{self.corner(z['apex']).capitalize()}, losing {loss:.1f}.")

    def tyre_strategy(self, mine, laps_left):
        """Fit your tyre deg from this stint and find the pit lap with the least total time. Returns a call or None."""
        st = self.status
        if not st or not mine[0]:
            return None
        age, fuel = st[15], st[5]
        if self.stint and age < self.stint[-1][0]:
            self.stint, self.deg, self.own_window = [], None, None  # new tyres
        if not self.skip_lap and mine[14] > 2:  # mine[14] is already the next lap; skip the standing start
            self.stint.append((age, mine[0], fuel))
        laps = [(s[0], (s[1] - FUEL_S_PER_KG * 1000 * s[2]) / 1000) for s in self.stint]  # fuel taken out
        median = sorted(y for _, y in laps)[len(laps) // 2] if laps else 0
        laps = [(x, y) for x, y in laps if y - median < MISTAKE]
        if len(laps) < 4:
            return None
        mx, my = sum(x for x, _ in laps) / len(laps), sum(y for _, y in laps) / len(laps)
        var = sum((x - mx) ** 2 for x, _ in laps)
        if not var:
            return None
        self.deg = max(0.0, sum((x - mx) * (y - my) for x, y in laps) / var)
        if laps_left < 3:
            return None

        def stint_cost(start_age, laps):  # time lost to deg over a stint, against brand new tyres
            return self.deg * sum(start_age + k for k in range(1, laps + 1))

        options = {p: stint_cost(age, p) + PIT_LOSS + stint_cost(0, laps_left - p) for p in range(laps_left)}
        if mine[16] or (self.weather or 0) >= 3:  # already stopped, or wet: no mandatory second compound
            options[None] = stint_cost(age, laps_left)
        best = min(options, key=options.get)
        window = 0 if best is None else mine[14] + best
        old, self.own_window = self.own_window, window
        if old is not None and (window == old or (window and old and abs(window - old) < 2)):
            return None
        if not window:
            return f"Your tyres are losing {self.deg:.2f} a lap. No need to stop."
        return f"Your tyres are losing {self.deg:.2f} a lap. Best stop around lap {window}."

    def race_call(self, mine, cars, temps):
        pos, ahead, behind = self.gaps(mine, cars)
        lap = mine[14]
        laps_left = self.total_laps - lap + 1
        parts = [f"P{pos}."]
        if mine[0]:
            self.laps_log.append({'lap': lap - 1, 'ms': mine[0], 'losses': {}})
        strategy = self.tyre_strategy(mine, laps_left)
        if strategy:
            parts.append(strategy)
        if ahead is not None:
            parts.append(f"Gap ahead {ahead:.1f}.")
        if behind is not None:
            parts.append(f"Behind {behind:.1f}.")
        if ahead is not None and ahead < 1.0:
            parts.append("DRS, push now.")
        elif behind is not None and behind < 1.0:
            parts.append("Car behind in DRS, defend. Overtake out of the last corner.")
        st = self.status
        self.lnc_reason = None
        if st:
            fuel = st[7]  # the plus/minus laps number from the MFD
            if fuel < -0.2:
                parts.append(f"Fuel minus {abs(fuel):.1f}.")
                self.lnc_reason = "save fuel"
            elif lap % 5 == 0:
                parts.append(f"Fuel plus {fuel:.1f}." if fuel > 0 else "Fuel on target.")
            battery = st[19] / ERS_MAX * 100
            if battery < 20:
                parts.append(f"Battery {battery:.0f} percent, ERS to medium.")
        if temps:
            rear, front = (temps[0] + temps[1]) / 2, (temps[2] + temps[3]) / 2
            if rear > 105:
                parts.append("Rears hot, diff down a few on the MFD.")
                self.lnc_reason = self.lnc_reason or "cool the tyres"
            if front > 105:
                parts.append("Fronts hot, bias back one on the MFD.")
                self.lnc_reason = self.lnc_reason or "cool the tyres"
        if max(self.brake_max[2:], default=0) > 1000:
            self.lnc_reason = self.lnc_reason or "cool the brakes"
        if self.wear:
            w = max(self.wear)
            for limit, msg in ((70, "Tyres are gone, box box."),
                               (55, "Tyres {w} percent, think about the stop."),
                               (35, "Tyres {w} percent, ok.")):
                if w >= limit and limit not in self.wear_called:
                    self.wear_called.update(l for l in (35, 55, 70) if l <= limit)
                    parts.append(msg.format(w=round(w)))
                    break
        window = self.pit_window()
        if window and (mine[16] == 0 or self.own_window):
            if lap == window - 2:
                parts.append(f"Pit window in 2 laps, around lap {window}.")
            elif lap == window and self.own_window:  # your own deg says this lap: a clear call, first
                parts.insert(0, "Box, box. Pit this lap.")
            elif lap == window:
                parts.append("In the pit window. Box when the tyres drop.")
        if laps_left == 1:
            parts.append("Last lap, bring it home.")
        elif laps_left in (5, 10):
            parts.append(f"{laps_left} to go.")
        self.say(" ".join(parts))

    def gaps(self, mine, cars):
        pos = mine[13]
        ahead = gap(mine) if pos > 1 else None
        behind_car = next((c for c in cars if c[13] == pos + 1 and c[26] == 2), None)
        return pos, ahead, gap(behind_car) if behind_car else None

    def report(self):
        """Radio button: the driver asks for an update."""
        mode = self.mode()
        if mode == 'race' and self.cars:
            mine = self.cars[self.me]
            pos, ahead, behind = self.gaps(mine, self.cars)
            parts = [f"Copy. P{pos}."]
            if ahead is not None:
                parts.append(f"Ahead {ahead:.1f}.")
            if behind is not None:
                parts.append(f"Behind {behind:.1f}.")
            if self.status:
                parts.append(f"Fuel {self.status[7]:+.1f}. Battery {self.status[19] / ERS_MAX * 100:.0f} percent.")
            if self.wear:
                parts.append(f"Tyres {max(self.wear):.0f} percent.")
            parts.append(f"{self.total_laps - mine[14] + 1} to go.")
            self.say(" ".join(parts))
        elif mode in ('practice', 'quali'):
            msg = "Copy."
            if self.best:
                msg += f" Best {lap_words(self.best)}."
            theo = self.theoretical()
            if theo and theo[1]:
                msg += f" Theoretical {lap_words(theo[0])}."
            if self.last_tip:
                msg += " " + self.last_tip
            if self.setup_tips and (self.in_garage or self.paused):
                msg += " Setup: " + self.setup_tips
            self.say(msg if msg != "Copy." else "Copy. Give me a clean lap first.")
        else:
            self.say("Radio check, loud and clear.")

    def answer(self, text):
        """Talk-back: answer a spoken question (see talkback.py) by keywords. Anything else gets the usual update."""
        t = text.lower()
        st, parts = self.status, []
        if any(w in t for w in ('tyre', 'tire', 'wear', 'deg')) and self.wear:
            parts.append(f"Tyres {max(self.wear):.0f} percent" +
                         (f", losing {self.deg:.2f} a lap." if self.deg is not None else "."))
        if any(w in t for w in ('gap', 'ahead', 'behind', 'position')) and self.cars and self.mode() == 'race':
            pos, ahead, behind = self.gaps(self.cars[self.me], self.cars)
            parts.append(f"P{pos}." + (f" Ahead {ahead:.1f}." if ahead is not None else "") +
                         (f" Behind {behind:.1f}." if behind is not None else ""))
        if 'fuel' in t and st:
            parts.append(f"Fuel {st[7]:+.1f} laps.")
        if any(w in t for w in ('battery', 'ers', 'deploy')) and st:
            parts.append(f"Battery {st[19] / ERS_MAX * 100:.0f} percent.")
        if any(w in t for w in ('pit', 'box', 'stop', 'strategy')):
            window = self.pit_window()
            parts.append(f"Box around lap {window}." if window else "No stop planned.")
        if any(w in t for w in ('best', 'lap time', 'theoretical', 'pace')):
            theo = self.theoretical()
            parts.append((f"Best {lap_words(self.best)}." if self.best else "No clean lap yet.") +
                         (f" Theoretical {lap_words(theo[0])}." if theo and theo[1] else ""))
        if any(w in t for w in ('weather', 'rain', 'wet')) and self.weather is not None:
            parts.append(f"Weather {WEATHER[min(self.weather, len(WEATHER) - 1)]}.")
        if parts:
            self.say("Copy. " + " ".join(parts))
        else:
            self.report()

"""The race engineer brain: parses F1 25 UDP telemetry (2025 format) and decides what to say.

Only advises things the driver can change in F1 25: setup menu items in the garage, and
brake bias, differential and ERS mode on the MFD during a session.
"""
import json
import os
import struct
import subprocess
import threading
import time

CARS = 22
HEADER = struct.Struct('<HBBBBBQfIIBB')                 # 29 bytes
SESSION = struct.Struct('<BbbBHBbBHH')                  # first fields of the session packet only
LAP = struct.Struct('<IIHBHBHBHBfff15BHHBfB')            # 57 bytes per car
TELEMETRY = struct.Struct('<HfffBbHBBH4H4B4BH4f4B')      # 60 bytes per car
STATUS = struct.Struct('<5B3f2H2BH3Bb3fB3fB')            # 55 bytes per car
SETUP = struct.Struct('<4B4f9B4fBf')                     # 50 bytes per car
DAMAGE = struct.Struct('<4f4B4B4B18B')                   # 46 bytes per car (F1 25 added tyre blisters)
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
HERE = os.path.dirname(os.path.abspath(__file__))
MEMORY = os.path.join(HERE, 'tracks.json')          # your own bests per track, written while you drive
TRACK_DATA = os.path.join(HERE, 'track_data.json')  # real race data, built by build_track_data.py


def load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_memory(mem):
    with open(MEMORY, 'w', encoding='utf-8') as f:
        json.dump(mem, f, indent=1)


def lap_words(ms):
    m, s = divmod(ms / 1000, 60)
    return f"{int(m)} {s:04.1f}" if m else f"{s:.1f}"


def gap(car):
    return (car[7] * 60000 + car[6]) / 1000  # delta to car in front, minutes part + ms part


class Radio:
    """Prints, speaks, and hands each message to on_say (the overlay).
    One PowerShell process keeps the Windows voice warm so speech is instant."""

    def __init__(self, voice=True, on_say=None):
        self.last = {}
        self.lock = threading.Lock()
        self.on_say = on_say
        self.ps = None
        if voice:
            script = ("Add-Type -AssemblyName System.Speech;"
                      "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;$s.Rate=1;"
                      "while(($l=[Console]::In.ReadLine()) -ne $null){try{$s.Speak($l)}catch{}}")
            try:
                self.ps = subprocess.Popen(['powershell', '-NoProfile', '-Command', script],
                                           stdin=subprocess.PIPE, text=True,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError:
                print('Voice not available, text only.')

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
        if self.ps:
            try:
                self.ps.stdin.write(text + '\n')
                self.ps.stdin.flush()
            except OSError:
                self.ps = None


class Engineer:
    def __init__(self, say):
        self.say = say
        self.track_data = load_json(TRACK_DATA)
        self.reset(None)

    def reset(self, uid):
        self.uid = uid
        self.session_type = None
        self.track = None
        self.total_laps = 0
        self.weather = None
        self.lap = 0
        self.skip_lap = True      # out laps, pit laps and invalid laps get no lap-time/setup verdict
        self.clear_samples()
        self.status = self.setup = self.wear = None
        self.cars = None
        self.me = 0
        self.wing = 0
        self.best = None
        self.setup_best = {}
        self.compare_to = None
        self.wear_called = set()
        self.last_advice = None

    def clear_samples(self):
        self.surf = [0.0] * 4
        self.brake_max = [0] * 4
        self.n = 0

    def mode(self):
        t = self.session_type
        if t is None:
            return None
        if 5 <= t <= 14:
            return 'quali'
        if 15 <= t <= 17:
            return 'race'
        return 'practice'  # practice, time trial (18) and anything unlabelled get setup coaching

    def info(self):
        return self.track_data.get(self.track or '')

    def pit_window(self):
        info = self.info()
        if not info or not info.get('stops') or not self.total_laps:
            return None
        return max(2, round(info['first_stop_frac'] * self.total_laps))

    def handle(self, data):
        if len(data) < HEADER.size:
            return
        h = HEADER.unpack_from(data)
        pid, uid, me = h[5], h[6], h[10]
        if uid != self.uid:
            self.reset(uid)
        fn = {1: self.on_session, 2: self.on_lap, 3: self.on_event, 5: self.on_setup,
              6: self.on_telemetry, 7: self.on_status, 10: self.on_damage}.get(pid)
        if fn and me < CARS:
            self.me = me
            fn(data, me)

    # ---------- packets ----------

    def on_session(self, data, me):
        s = SESSION.unpack_from(data, 29)
        weather, track_temp = s[0], s[1]
        first = self.session_type is None
        self.session_type, self.total_laps, self.track = s[5], s[3], str(s[6])
        if first:
            self.briefing(track_temp)
        if self.weather is not None and weather != self.weather and weather < len(WEATHER):
            msg = f"Weather changing to {WEATHER[weather]}."
            if weather >= 3 > self.weather:
                msg += " Rain coming, think about inters."
            elif weather < 3 <= self.weather:
                msg += " Track drying, slicks soon."
            self.say(msg)
        self.weather = weather

    def on_telemetry(self, data, me):
        t = TELEMETRY.unpack_from(data, 29 + me * TELEMETRY.size)
        if t[0] < 60:  # ignore pit lane and spins
            return
        for i in range(4):
            self.surf[i] += t[14 + i]
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
            self.say("Setup change noted. " + ", ".join(changes[:3]) + ".")
            self.compare_to = self.setup_best.get(self.setup)
        self.setup = s

    def on_damage(self, data, me):
        d = DAMAGE.unpack_from(data, 29 + me * DAMAGE.size)
        self.wear = d[:4]
        wing = max(d[16], d[17])  # front left / front right wing damage
        if wing - self.wing >= 15:
            self.say(f"Front wing damage, {wing} percent." +
                     (" Box this lap for a new nose." if wing >= 40 else " We can live with it."))
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
            self.skip_lap = mine[15] != 0
            self.clear_samples()
        if mine[15] != 0 or mine[18]:  # in pit lane, or lap invalid
            self.skip_lap = True

    def on_event(self, data, me):
        code = data[29:33]
        if code == b'LGOT':
            self.say("Lights out, go go go!")
        elif code == b'DRSE':
            self.say("DRS enabled.")
        elif code == b'DRSD':
            self.say("DRS disabled.")
        elif code == b'SCAR':
            name, ev = SAFETY_CAR.get(data[33], 'Safety car'), data[34]
            if ev == 0:
                self.say(f"{name} deployed. Slow down, keep the delta positive, keep tyre temps up.")
                window = self.pit_window()
                if self.mode() == 'race' and window and abs(self.lap - window) <= 3:
                    self.say("We are near the pit window. Cheap stop under the safety car, box if you want it.")
            elif ev == 1:
                self.say(f"{name} ending. Get ready to go.")
            elif ev == 3:
                self.say("Green flag. Race resumes, push now.")
        elif code == b'FTLP' and data[33] == me:
            self.say("Fastest lap! Purple. Mega.")
        elif code == b'PENA' and data[35] == me:
            kind, secs = data[33], data[37]
            msg = {0: "Drive through penalty.", 1: "Stop go penalty.",
                   4: f"{secs} second time penalty.", 5: "Track limits warning. Careful."}.get(kind)
            if msg:
                self.say(msg)
        elif code == b'CHQF':
            self.say("Chequered flag. Good job mate, cool down lap now.")

    # ---------- the thinking ----------

    def briefing(self, track_temp):
        mode = self.mode()
        info = self.info()
        name = (info or {}).get('name') or TRACKS.get(int(self.track), 'this track')
        if mode == 'practice':
            label = 'Time trial' if self.session_type == 18 else 'Practice'
            msg = (f"Radio check. {label} at {name}. Track is {track_temp} degrees. Give me clean laps "
                   "and I'll tell you what to change on the car.")
            if info:
                msg += (f" {info['downforce'].capitalize()} downforce track, {info['full_throttle_pct']} percent "
                        f"full throttle. Start with wings around {WING_RANGE[info['downforce']]}.")
                if info['heavy_brake_zones'] >= 6:
                    msg += f" {info['heavy_brake_zones']} big stops, watch brake bias."
                if info['deg'] == 'high':
                    msg += " Tyre deg is high here, do a long run."
            old = load_json(MEMORY).get(self.track)
            if old:
                st = old['setup']
                msg += (f" Last time here your best was {lap_words(old['best'])} with wings {st[0]:g} "
                        f"and {st[1]:g}, diff {st[2]:g} on, {st[3]:g} off, brake bias {st[15]:g}.")
            self.say(msg)
        elif mode == 'quali':
            self.say("Radio check. Qualifying. Warm the tyres on the out lap, then let's find the time.")
        elif mode == 'race':
            msg = f"Radio check. {self.total_laps} laps today."
            window = self.pit_window()
            if info:
                plan = ' to '.join(c.lower() for c in info['compounds'])
                stops = 'one stop' if info['stops'] == 1 else f"{info['stops']} stops"
                msg += f" In the real race here most cars did {stops}" + (f", {plan}." if plan else ".")
                msg += f" Tyre deg is {info['deg']}."
                if window:
                    msg += f" Pit window around lap {window}."
            self.say(msg + " Good start now.")

    def lap_done(self, mine, cars):
        mode = self.mode()
        ms = mine[0]
        if mode in ('practice', 'quali') and ms and not self.skip_lap:
            self.lap_time_call(ms)
        if self.n >= 100:  # about 5 seconds of driving data
            surf = [x / self.n for x in self.surf]
            if mode == 'practice':
                self.setup_advice(surf)
            elif mode == 'race':
                self.race_temps(surf)
        if mode == 'race':
            self.race_call(mine, cars)

    def lap_time_call(self, ms):
        msg = lap_words(ms)
        if self.best is None or ms < self.best:
            msg += ", your best lap."
            self.best = ms
            mem = load_json(MEMORY)
            old = mem.get(self.track)
            if self.setup and self.track and (not old or ms < old['best']):
                mem[self.track] = {'best': ms, 'setup': list(self.setup)}
                save_memory(mem)
                if old:
                    msg += f" New record at this track, {(old['best'] - ms) / 1000:.1f} faster than before."
        else:
            msg += f", plus {(ms - self.best) / 1000:.1f} to your best."
        if self.setup:
            key = self.setup
            self.setup_best[key] = min(ms, self.setup_best.get(key, ms))
            if self.compare_to:
                d = (self.compare_to - ms) / 1000
                msg += f" That setup change is {abs(d):.1f} {'faster' if d > 0 else 'slower'}."
                self.compare_to = None
        self.say(msg)

    def setup_advice(self, surf):
        rear, front = (surf[0] + surf[1]) / 2, (surf[2] + surf[3]) / 2
        tips = []
        s = self.setup
        if front - rear > 8:
            tips.append(f"Fronts {front - rear:.0f} degrees hotter than rears, car is understeering. "
                        "Try one or two clicks more front wing, or soften the front anti roll bar.")
            if s and s[3] > 40:
                tips.append(f"Off throttle diff is {s[3]:g}. Drop it 5 to 10 to help the car turn in.")
        elif rear - front > 8:
            tips.append(f"Rears {rear - front:.0f} degrees hotter than fronts, that's wheelspin and oversteer.")
            if s and s[2] > 20:
                tips.append(f"On throttle diff is {s[2]:g}. Drop it 5 to 10 for better traction, "
                            "or add a click of rear wing.")
        avg = sum(surf) / 4
        if avg > 105:
            tips.append(f"Tyres are overheating at {avg:.0f} degrees. Lower tyre pressures a click.")
        elif avg < 80:
            tips.append(f"Tyres are cold at {avg:.0f} degrees. Raise tyre pressures a click.")
        f_brake, r_brake = max(self.brake_max[2:]), max(self.brake_max[:2])
        bias = f" Bias is {s[15]:g}." if s else ""
        if f_brake > 1000 or f_brake - r_brake > 350:
            tips.append(f"Front brakes peaking {f_brake} degrees, they will lock.{bias} "
                        "Move brake bias back one or two percent.")
        elif r_brake > f_brake:
            tips.append(f"Rear brakes hotter than the fronts, rears will lock.{bias} Move bias forward one percent.")
        self.last_advice = " ".join(tips) if tips else "Tyre and brake temps look balanced. Setup is in the window."
        self.say(self.last_advice)

    def race_temps(self, surf):
        rear, front = (surf[0] + surf[1]) / 2, (surf[2] + surf[3]) / 2
        if rear > 105:
            self.say("Rears overheating. Short shift out of slow corners, "
                     "and take the differential down a few on the MFD.", 'rear_hot', 120)
        if front > 105:
            self.say("Fronts overheating. Brake a bit earlier, move brake bias back a click on the MFD.",
                     'front_hot', 120)
        f_brake = max(self.brake_max[2:])
        if f_brake > 1050:
            self.say(f"Front brakes hot, {f_brake} degrees. Bias back a click, lift and coast "
                     "into the big stops.", 'brake_hot', 120)

    def gaps(self, mine, cars):
        pos = mine[13]
        ahead = gap(mine) if pos > 1 else None
        behind_car = next((c for c in cars if c[13] == pos + 1 and c[26] == 2), None)
        return pos, ahead, gap(behind_car) if behind_car else None

    def race_call(self, mine, cars):
        pos, ahead, behind = self.gaps(mine, cars)
        lap = mine[14]
        laps_left = self.total_laps - lap + 1
        parts = [f"P{pos}."]
        if ahead is not None:
            parts.append(f"Gap ahead {ahead:.1f}.")
        if behind is not None:
            parts.append(f"Behind {behind:.1f}.")
        if ahead is not None and ahead < 1.0:
            parts.append("You have DRS, push now.")
        elif behind is not None and behind < 1.0:
            parts.append("Car behind in DRS range. Defend, ERS overtake out of the last corner.")
        st = self.status
        if st:
            fuel = st[7]  # the plus/minus laps number from the MFD
            if fuel < -0.3:
                parts.append(f"Fuel minus {abs(fuel):.1f}. Lift and coast into the big stops.")
            elif lap % 5 == 0:
                parts.append(f"Fuel plus {fuel:.1f}." if fuel > 0 else "Fuel on target.")
            battery = st[19] / ERS_MAX * 100
            if battery < 20:
                parts.append(f"Battery {battery:.0f} percent. ERS to medium for a lap to recharge.")
        if self.wear:
            w = max(self.wear)
            for limit, msg in ((70, "Tyres are gone, box box this lap."),
                               (55, "Tyre wear {w} percent. Start thinking about the stop."),
                               (35, "Tyre wear {w} percent, looking ok.")):
                if w >= limit and limit not in self.wear_called:
                    self.wear_called.update(l for l in (35, 55, 70) if l <= limit)
                    parts.append(msg.format(w=round(w)))
                    break
        window = self.pit_window()
        if window and mine[16] == 0:
            if lap == window - 2:
                parts.append(f"Pit window opens in 2 laps, around lap {window}.")
            elif lap == window:
                parts.append("We are in the pit window. Box when the tyres drop off.")
        if laps_left == 1:
            parts.append("Last lap, bring it home.")
        elif laps_left in (5, 10):
            parts.append(f"{laps_left} laps to go.")
        self.say(" ".join(parts))

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
                parts.append(f"Tyres {max(self.wear):.0f} percent worn.")
            laps_left = self.total_laps - mine[14] + 1
            parts.append(f"{laps_left} laps to go.")
            self.say(" ".join(parts))
        elif mode in ('practice', 'quali'):
            msg = "Copy."
            if self.best:
                msg += f" Best lap {lap_words(self.best)}."
            if mode == 'practice' and self.last_advice:
                msg += " " + self.last_advice
            self.say(msg if msg != "Copy." else "Copy. Give me a clean lap first.")
        else:
            self.say("Radio check, loud and clear.")

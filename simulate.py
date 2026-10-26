"""Race simulator: drives a fake F1 25 race through the engineer in seconds and logs every radio call.

The simulated driver has tyre deg, fuel burn, lap-time noise and the odd mistake. It boxes when the
engineer says "Box, box", takes a cheap stop under the safety car, and otherwise stops 3 laps from
the end. At the end it compares the engineer's stop with the best stop for the true tyre deg.

    python simulate.py                                  18 laps at Monza, safety car on lap 11
    python simulate.py --laps 30 --deg 0.15 --sc 0 --seed 4
    python simulate.py --ask "8:when should we pit" --ask "14:how are the tyres"
    python simulate.py --live --speed 10                send it to the running engineer: you hear it,
                                                        and can hold your radio button to ask questions

Logs go to the screen and reports/sim/; the session report page goes there too.
"""
import argparse
import os
import random
import socket
import tempfile
import time

import engineer
import fakegame as g

RACE = 15
FUEL_PER_LAP = 1.6   # kg
SC_LAP_S = 25        # a safety car lap is this much slower
PIT_IN_S, PIT_OUT_S = 8, 14   # the pit stop's time loss, split over in-lap and out-lap
WEAR_PER_LAP = 3.0   # tyre wear percent


class Udp:
    """Sends the packets to the running engineer at --speed times real time."""

    def __init__(self, speed):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.speed, self.step = speed, 0.0

    def handle(self, data):
        self.sock.sendto(data, ('127.0.0.1', 20777))
        if data[6] == 2:  # one lap packet per 20 m step
            time.sleep(self.step)


def best_stop(deg, laps, pit_loss=PIT_IN_S + PIT_OUT_S):
    """True best single stop lap for this deg, to grade the engineer's call."""
    def cost(p):
        return deg * (sum(range(1, p + 1)) + sum(range(1, laps - p + 1))) + pit_loss
    return min(range(2, laps - 1), key=cost)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--track', type=int, default=11, help='F1 25 track id (11 Monza, 7 Silverstone, ...)')
    ap.add_argument('--laps', type=int, default=18)
    ap.add_argument('--lap-time', type=float, default=82.0, help='seconds, on new tyres with no fuel')
    ap.add_argument('--deg', type=float, default=0.08, help='true tyre deg, seconds per lap')
    ap.add_argument('--sc', type=int, default=11, help='safety car lap, 0 for none')
    ap.add_argument('--mistakes', type=float, default=0.1, help='chance of a 2-4 s mistake per lap')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--ask', action='append', default=[], help='"LAP:question" asked at the start of LAP')
    ap.add_argument('--live', action='store_true', help='send UDP to the running engineer instead')
    ap.add_argument('--speed', type=float, default=10, help='with --live: times real time')
    args = ap.parse_args()
    rnd = random.Random(args.seed)
    g.UID = rnd.randrange(1, 2 ** 63)  # a new session for the engineer every run
    asks = {}
    for a in args.ask:
        lap, q = a.split(':', 1)
        asks.setdefault(int(lap), []).append(q)

    out = os.path.join(engineer.HERE, 'reports', 'sim')
    os.makedirs(out, exist_ok=True)
    log_path = os.path.join(out, time.strftime('%Y-%m-%d_%H%M%S') + f'_seed{args.seed}.log')
    log_file = open(log_path, 'w', encoding='utf-8')
    lap_now = [0]
    calls = []

    def log(who, text):
        line = f"L{lap_now[0]:02d} {who:<9} {text}"
        print(line, flush=True)
        log_file.write(line + '\n')

    def heard(text, key=None, every=0):
        calls.append((lap_now[0], text))
        log('ENGINEER', text)

    if args.live:
        eng = Udp(args.speed)
        log('SIM', f"live: sending to the running engineer at {args.speed:g}x; its calls go to engineer.log")
    else:
        engineer.MEMORY = os.path.join(tempfile.mkdtemp(), 'tracks.json')  # never touch your real tracks.json
        engineer.session_report.REPORTS = out
        eng = engineer.Engineer(heard)

    log('SIM', f"track {args.track}, {args.laps} laps, true deg {args.deg:.2f} s/lap, "
               f"safety car lap {args.sc or 'none'}, seed {args.seed}")
    eng.handle(g.session(RACE, laps=args.laps, track=args.track))
    fuel, age, stops, compound = args.laps * FUEL_PER_LAP + 2, 0, 0, 16
    last_ms, pit_now, pit_reason, total = 0, False, None, 0.0
    for lap in range(1, args.laps + 1):
        lap_now[0] = lap
        sc_lap = args.sc and args.sc <= lap < args.sc + 2
        for q in asks.get(lap, []):
            log('DRIVER', f'"{q}"')
            if args.live:
                log('SIM', 'live: ask it yourself on the radio button')
            else:
                eng.answer(q)
        out_lap = stops and age == 0
        eng.handle(g.status(age=age, fuel=fuel, compound=compound, fuel_delta=0.3))
        eng.handle(g.damage(min(99, age * WEAR_PER_LAP)))
        eng.handle(g.lap_pkt(lap, last_ms, pos=3, pit=1 if out_lap else 0, stops=stops))  # last lap ends: lap calls
        if args.sc and lap == args.sc:
            eng.handle(g.safety_car(1, 0))
        if args.sc and lap == args.sc + 2:
            eng.handle(g.safety_car(1, 3))
        # the driver's decision for this lap, from what the engineer said last lap
        said_now = ' '.join(t for n, t in calls if n == lap)
        if not stops and not pit_now:
            if 'Box, box' in said_now or 'box box' in said_now:
                pit_now, pit_reason = True, 'engineer called it'
            elif sc_lap and 'Cheap stop' in ' '.join(t for _, t in calls):
                pit_now, pit_reason = True, 'cheap stop under the safety car'
            elif lap == args.laps - 3:
                pit_now, pit_reason = True, 'no call came, mandatory stop'
        if pit_now:
            log('DRIVER', f"boxing this lap ({pit_reason})")

        ms = (args.lap_time + args.deg * age + engineer.FUEL_S_PER_KG * fuel + rnd.gauss(0, 0.25)) * 1000
        mistake = rnd.random() < args.mistakes and not sc_lap
        if mistake:
            ms += rnd.uniform(2, 4) * 1000
        if lap == 1:
            ms += 3000
        if sc_lap:
            ms += SC_LAP_S * 1000
        if pit_now:
            ms += PIT_IN_S * 1000
        if out_lap:
            ms += PIT_OUT_S * 1000
        log('SIM', f"lap {lap}: {engineer.lap_words(ms)}, tyres {age} laps old, fuel {fuel:.0f} kg"
                   + (", mistake" if mistake else "") + (", safety car" if sc_lap else "")
                   + (", out lap" if out_lap else ""))

        if args.live:
            eng.step = ms / 1000 / (g.LENGTH / 20) / args.speed
        g.drive(eng, lap, last_ms, pos=3, pit=1 if pit_now or out_lap else 0, stops=stops)
        total += ms / 1000
        last_ms, fuel, age = int(ms), fuel - FUEL_PER_LAP, age + 1
        if pit_now:
            stops, age, compound, pit_now = 1, 0, 17, False
        lap_now[0] = lap + 1
    eng.handle(g.lap_pkt(args.laps + 1, last_ms, pos=3, stops=stops))  # chequered flag: last lap time comes in
    lap_now[0] = args.laps

    log('SIM', f"race time {total:.1f} s")
    if not args.live:
        stop_laps = [n for n, t in calls if 'Box, box' in t]
        best = best_stop(args.deg, args.laps)
        log('SIM', f"engineer measured deg {eng.deg if eng.deg is None else round(eng.deg, 3)} (true {args.deg}), "
                   f"called the stop on lap {stop_laps[0] if stop_laps else 'never'}, best stop for the true deg: lap {best}")
        log('SIM', f"report: {engineer.session_report.write(eng)}")
    log('SIM', f"log: {log_path}")


if __name__ == '__main__':
    main()

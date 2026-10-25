"""Builds track_data.json from real F1 race data (FastF1). Run once; the engineer only reads the JSON.

    pip install fastf1
    python -m fastf1 auth f1tv --authenticate --method browser   (F1 timing data needs an F1 account)
    python build_track_data.py [season]

Per track it stores: real race strategy (stops, compounds, first stop as share of race distance),
tyre degradation, and track character from the race's fastest lap (full throttle share,
heavy braking zones, top speed) which drives the starting wing/brake advice.
Wet races and races without tyre data fall back to the latest usable race at that track
(up to 2 seasons back).
"""
import json
import os
import statistics
import sys
from collections import Counter

import fastf1
import numpy as np

SEASON = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
HERE = os.path.dirname(os.path.abspath(__file__))
FUEL_EFFECT = 0.035  # s/lap a car gains from burning fuel; added back so deg is tyre-only

# FastF1 event Location -> F1 25 m_trackId
GAME_IDS = {'melbourne': 0, 'shanghai': 2, 'sakhir': 3, 'barcelona': 4, 'monaco': 5, 'montréal': 6,
            'montreal': 6, 'silverstone': 7, 'budapest': 9, 'spa-francorchamps': 10, 'monza': 11,
            'marina bay': 12, 'singapore': 12, 'suzuka': 13, 'yas island': 14, 'yas marina': 14,
            'austin': 15, 'são paulo': 16, 'sao paulo': 16, 'spielberg': 17, 'mexico city': 19,
            'baku': 20, 'zandvoort': 26, 'imola': 27, 'jeddah': 29, 'miami': 30, 'miami gardens': 30,
            'las vegas': 31, 'lusail': 32}
REVERSE = {7: 39, 17: 40, 26: 41}  # reverse layouts share the base track's strategy data


def wet(laps):
    return laps['Compound'].isin(['INTERMEDIATE', 'WET']).mean() > 0.05


def strategy(laps, total):
    """A stop is a pit exit where the tyres changed; FastF1's Stint and PitOutTime alone over-count."""
    finishers = [d for d, g in laps.groupby('Driver') if g['LapNumber'].max() >= 0.9 * total]
    plans = []  # (first stop lap or None, compound sequence) per finisher
    for d in finishers:
        g = laps[laps['Driver'] == d].sort_values('LapNumber')
        comp = g['Compound'].astype(str).to_numpy()
        life, out, lap = g['TyreLife'].to_numpy(), g['PitOutTime'].notna().to_numpy(), g['LapNumber'].to_numpy()
        stop_laps = [int(lap[k]) - 1 for k in range(1, len(g))
                     if out[k] and (comp[k] != comp[k - 1] or life[k] < life[k - 1])]
        seq = [comp[0]] + [comp[k] for k in range(1, len(g))
                           if out[k] and (comp[k] != comp[k - 1] or life[k] < life[k - 1])]
        plans.append((stop_laps, tuple(seq)))
    if not plans:
        return {'stops': 1, 'first_stop_frac': 0.5, 'compounds': []}
    stops = Counter(len(s) for s, _ in plans).most_common(1)[0][0]
    typical = [p for p in plans if len(p[0]) == stops]
    first = [s[0] for s, _ in typical if s]
    seqs = [seq for _, seq in typical if 'nan' not in seq]  # some races lack compound data
    return {'stops': stops,
            'first_stop_frac': round(statistics.median(first) / total, 2) if first else 0.5,
            'compounds': list(Counter(seqs).most_common(1)[0][0]) if seqs else []}


def degradation(laps):
    slopes = []
    clean = laps[(laps['IsAccurate'] == True) & (laps['TrackStatus'] == '1')
                 & laps['PitInTime'].isna() & laps['PitOutTime'].isna()]
    for _, s in clean.groupby(['Driver', 'Stint']):
        if len(s) >= 8:
            try:
                slopes.append(np.polyfit(s['TyreLife'], s['LapTime'].dt.total_seconds(), 1)[0] + FUEL_EFFECT)
            except (np.linalg.LinAlgError, ValueError):
                pass  # bad stint data, skip it
    deg = max(0.0, float(statistics.median(slopes))) if slopes else 0.07
    return round(deg, 3), 'low' if deg < 0.05 else 'medium' if deg < 0.1 else 'high'


def corners(session, car):
    """Official corner numbers as a share of lap distance, so the game's own track length can place them."""
    lap_len = float(car['Distance'].max())
    info = session.get_circuit_info()
    return [[f"{int(c.Number)}{c.Letter or ''}", round(float(c.Distance) / lap_len, 4)]
            for c in info.corners.itertuples()]


def character(session):
    car = session.laps.pick_fastest().get_car_data().add_distance()
    speed, brake = car['Speed'].to_numpy(), car['Brake'].to_numpy().astype(bool)
    full = float((car['Throttle'] >= 98).mean() * 100)
    heavy, i = 0, 0
    while i < len(brake):
        if brake[i]:
            j = i
            while j < len(brake) and brake[j]:
                j += 1
            if speed[i] - speed[i:j + 1].min() >= 100:
                heavy += 1
            i = j
        i += 1
    # cut-offs fit the 2025 spread: Monza 75%, Las Vegas 68% low; Monaco 35%, Hungary 52% high
    downforce = 'low' if full >= 66 else 'medium' if full >= 54 else 'high'
    return {'full_throttle_pct': round(full), 'heavy_brake_zones': heavy,
            'top_speed': int(speed.max()), 'downforce': downforce, 'corners': corners(session, car)}


def races(season):
    found = {}
    for _, ev in fastf1.get_event_schedule(season, include_testing=False).iterrows():
        gid = GAME_IDS.get(str(ev['Location']).lower())
        if gid is None:
            print('skip, not in F1 25:', ev['Location'], season)
        else:
            found[gid] = (ev['Location'], ev['RoundNumber'])
    return found


def track(season, rnd):
    s = fastf1.get_session(season, rnd, 'R')
    s.load(laps=True, telemetry=True, weather=False, messages=False)
    if wet(s.laps):
        return None
    total = int(s.laps['LapNumber'].max())
    plan = strategy(s.laps, total)
    if not plan['compounds']:  # no tyre data for this race
        return None
    deg, deg_level = degradation(s.laps)
    return {'race_laps': total, **plan, 'deg_s_per_lap': deg, 'deg': deg_level, **character(s)}


def main():
    cache = os.path.join(HERE, '.fastf1-cache')
    os.makedirs(cache, exist_ok=True)
    fastf1.Cache.configure(cache_dir=cache)
    out = {}
    seasons = {y: races(y) for y in (SEASON, SEASON - 1, SEASON - 2)}  # earlier years cover wet races
    for gid in dict.fromkeys(g for found in seasons.values() for g in found):
        for year, found in seasons.items():
            if gid not in found:
                continue
            name, rnd = found[gid]
            try:
                data = track(year, rnd)
            except Exception as err:  # one bad session must not kill the whole build
                print('fail', name, year, err)
                continue
            if data:
                out[str(gid)] = {'name': name, 'season': year, **data}
                print('ok', name, year, out[str(gid)])
                break
            print('wet race or no tyre data, trying an earlier year:', name, year)
    if not out:  # keep any existing track_data.json rather than overwrite it with nothing
        sys.exit('No track data loaded. Sign in first: python -m fastf1 auth f1tv --authenticate --method browser')
    for base, rev in REVERSE.items():
        if str(base) in out:
            out[str(rev)] = dict(out[str(base)], name=out[str(base)]['name'] + ' reverse')
    with open(os.path.join(HERE, 'track_data.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f'wrote {len(out)} tracks')


if __name__ == '__main__':
    main()

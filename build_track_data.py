"""Builds track_data.json from real F1 race data (FastF1). Run once; the engineer only reads the JSON.

    pip install fastf1
    python -m fastf1 auth f1tv --authenticate --method browser   (F1 timing data needs an F1 account)
    python build_track_data.py [season]

Per track it stores: real race strategy (stops, compounds, first stop as share of race distance),
tyre degradation, and track character from the race's fastest lap (full throttle share,
heavy braking zones, top speed) which drives the starting wing/brake advice.
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
            'baku': 20, 'zandvoort': 26, 'imola': 27, 'jeddah': 29, 'miami': 30, 'las vegas': 31,
            'lusail': 32}
REVERSE = {7: 39, 17: 40, 26: 41}  # reverse layouts share the base track's strategy data


def strategy(laps, total):
    finishers = [d for d, g in laps.groupby('Driver') if g['LapNumber'].max() >= 0.9 * total]
    stops, first, seqs = [], [], []
    for d in finishers:
        g = laps[laps['Driver'] == d].sort_values('LapNumber')
        stints = g.groupby('Stint')
        stops.append(len(stints) - 1)
        seqs.append(tuple(s['Compound'].iloc[0] for _, s in stints))
        if len(stints) > 1:
            first.append(g[g['Stint'] == g['Stint'].min()]['LapNumber'].max())
    return {'stops': Counter(stops).most_common(1)[0][0] if stops else 1,
            'first_stop_frac': round(statistics.median(first) / total, 2) if first else 0.5,
            'compounds': list(Counter(seqs).most_common(1)[0][0]) if seqs else []}


def degradation(laps):
    slopes = []
    clean = laps[(laps['IsAccurate'] == True) & (laps['TrackStatus'] == '1')
                 & laps['PitInTime'].isna() & laps['PitOutTime'].isna()]
    for _, s in clean.groupby(['Driver', 'Stint']):
        if len(s) >= 8:
            slopes.append(np.polyfit(s['TyreLife'], s['LapTime'].dt.total_seconds(), 1)[0] + FUEL_EFFECT)
    deg = statistics.median(slopes) if slopes else 0.07
    return round(deg, 3), 'low' if deg < 0.05 else 'medium' if deg < 0.1 else 'high'


def character(session):
    car = session.laps.pick_fastest().get_car_data()
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
    downforce = 'low' if full >= 72 else 'medium' if full >= 60 else 'high'
    return {'full_throttle_pct': round(full), 'heavy_brake_zones': heavy,
            'top_speed': int(speed.max()), 'downforce': downforce}


def main():
    cache = os.path.join(HERE, '.fastf1-cache')
    os.makedirs(cache, exist_ok=True)
    fastf1.Cache.configure(cache_dir=cache)
    out = {}
    for _, ev in fastf1.get_event_schedule(SEASON, include_testing=False).iterrows():
        gid = GAME_IDS.get(str(ev['Location']).lower())
        if gid is None:
            print('skip, not in F1 25:', ev['Location'])
            continue
        try:
            s = fastf1.get_session(SEASON, ev['RoundNumber'], 'R')
            s.load(laps=True, telemetry=True, weather=False, messages=False)
            total = int(s.laps['LapNumber'].max())
            deg, deg_level = degradation(s.laps)
            out[str(gid)] = {'name': ev['Location'], 'season': SEASON, 'race_laps': total,
                             **strategy(s.laps, total), 'deg_s_per_lap': deg, 'deg': deg_level,
                             **character(s)}
            print('ok', ev['Location'], out[str(gid)])
        except Exception as err:  # one bad session must not kill the whole build
            print('fail', ev['Location'], err)
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

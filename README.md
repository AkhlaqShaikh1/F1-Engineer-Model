# F1 25 Race Engineer

A lightweight race engineer for EA SPORTS F1 25 on PC. It talks to you over the radio like a real
race engineer: setup advice in practice, plus gaps, fuel, tyres, pit window and safety car calls in the race.

- **Voice:** a natural British male voice ([Piper](https://github.com/OHF-Voice/piper1-gpl), offline) with a team-radio effect. Falls back to the Windows voice.
- **Overlay:** a small radio box that pops up over the game while the engineer talks.
- **Auto-start:** starts with Windows and stays idle (no CPU) until F1 25 sends data.
- **Optional radio button:** press a controller combo to ask for an update at any time.
- **Real-world data:** per-track briefings built from real F1 races with [FastF1](https://github.com/theOehrly/Fast-F1).
- **No extra packages:** the engineer itself uses only the Python standard library.

## Is it safe? (anti-cheat)

Yes. The engineer never touches the game. It reads only:
- the game's **official UDP telemetry output**, a feature EA built in for dashboards and sim tools;
- controller buttons through the normal Windows APIs (XInput / winmm), read-only, the same way any launcher does.

No injection, no memory reading, no file changes to the game.

## Setup

1. Run `install.bat`. It installs Python if needed, adds auto-start, and starts the engineer.
2. In F1 25, open **Settings → Telemetry Settings** and set:
   - UDP Telemetry: **On**
   - UDP Broadcast Mode: **Off**
   - UDP IP Address: **127.0.0.1**
   - UDP Port: **20777**
   - UDP Send Rate: **20Hz**
   - UDP Format: **2025**
3. Set display mode to **Borderless** or **Windowed**. The overlay can't draw over exclusive Fullscreen; the voice works in any mode.

Drag the overlay to move it. Right-click it to quit. To remove the engineer, run `uninstall.bat`.

### Voice

`install.bat` installs Piper and two voices: `en_GB-northern_english_male-medium` (default) and
`en_GB-alan-medium`. To switch, set `"voice"` in `config.json` to either name,
or to `"windows"` for the built-in Windows voice. Then restart the engineer.
Preview a voice: `python voice.py "Box box, box box."`

Piper is GPL-3.0 and runs as a separate program; it is not bundled with or linked into this MIT code.

### Optional radio button

Run `Bind Radio Button.bat` and hold the buttons you want for 2 seconds. Pick buttons that do
nothing in F1 25. Works with Xbox pads, and with DualSense either through Steam Input or as a
plain HID controller. Press the combo during a session and the engineer reads out your current state.
Remove it with `python app.py --unbind`.

## What it tells you

It only advises things you can change in F1 25: the **setup menu** in the garage, and **brake bias,
differential and ERS mode** on the MFD. Everything else is driving technique.

**Practice and time trial** (time trial is the quickest way to test it):
- a track briefing: downforce level, starting wing range, big braking zones and tyre deg, plus your own best lap and setup from last time;
- lap time against your best;
- front vs rear tyre temps: understeer means front wing, front anti-roll bar or off-throttle diff; wheelspin means on-throttle diff or rear wing;
- tyre pressures and brake bias, based on tyre and brake temps;
- each setup change, and whether it made you faster.

**Race:**
- the real-race strategy for that track, scaled to your race length (pit window lap);
- position and gaps every lap, plus DRS range;
- fuel plus or minus, battery, and tyre wear;
- diff and bias changes on the MFD when tyres or brakes overheat;
- safety car (including a cheap pit stop call), penalties, front wing damage, weather, and the last lap.

## Real-world track data

`track_data.json` is built from real F1 races. To rebuild it (for example for a new season):

```
pip install fastf1
python -m fastf1 auth f1tv --authenticate --method browser
python build_track_data.py 2025
```

F1's timing data now needs a Formula 1 account sign-in. Without `track_data.json`, the engineer
still works; it just skips the real-world briefing.

## Files

| File | What |
|------|------|
| `app.py` | Entry point: overlay, telemetry listener, radio button |
| `engineer.py` | Packet parsing (F1 25 UDP format 2025) and all the advice |
| `voice.py` | Piper voice + team-radio effect, Windows voice fallback |
| `controller.py` | Optional read-only controller button reader |
| `build_track_data.py` | Builds `track_data.json` from FastF1 |
| `tests/test_engineer.py` | Feeds fake packets through the engineer: `python tests/test_engineer.py` |

`tracks.json` (your own bests) and `config.json` (overlay position, radio button) are created on your PC and are not committed.

## License

[MIT](LICENSE)

## Disclaimer

Unofficial fan project. Not affiliated with or endorsed by Formula 1, the FIA, EA or Codemasters.
F1 is a trademark of Formula One Licensing B.V. Real-world timing data comes through FastF1;
follow F1's terms for that data.

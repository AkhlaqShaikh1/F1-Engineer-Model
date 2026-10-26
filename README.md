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

**Talk-back (optional).** With `pip install openai-whisper sounddevice`, hold the radio button, ask a
question into your headset mic, and let go: "how are the tyres?", "what's the gap?", "when do we box?",
"fuel?", "battery?", "best lap?", "weather?". Speech to text runs locally with Whisper, on the GPU when
PyTorch sees one. A quick tap still gives the usual update.

## What it tells you

It only advises things you can change in F1 25: the **setup menu** in the garage, and **brake bias,
differential and ERS mode** on the MFD. Everything else is driving technique.

On track it only talks about what you can change while driving: brake bias, diff and ERS on
the MFD, and technique. Setup menu advice waits until you're in the garage or pause the game.

**Practice and time trial** (time trial is the quickest way to test it):
- a short briefing: downforce and wing range, your best here, and the corner you keep getting wrong;
- after every clean lap: lap time, then the corner where you lost the most against your best lap and why
  (braked early, slow at the apex, late on the power), plus the corner where you gained;
- lock-ups and wheelspin by corner, with the MFD fix (bias, diff) or the technique fix;
- in the garage or pause menu: setup advice from tyre and brake temps (wings, diff, pressures);
- each setup change, and whether it made you faster.

**It learns you.** Per track, `tracks.json` keeps your all-time best lap corner by corner and how
often you brake early, lock up, spin the rears or get on the power late at each corner. A new
session coaches you against your all-time best from lap 1 and opens with your weakest corner.
Repeated mistakes get called: "that's 3 laps in a row".

**Live corner coach (optional, needs PyTorch).** A small neural net per track learns your clean laps
on your PC. After 5 laps of data it trains in the background (on the GPU when PyTorch sees one) and
retrains after every clean lap. At each apex it predicts your corner exit time from your speed, pedals
and the car's state (fuel, tyre wear, compound, wet), compares it with what you usually do from that corner
entry in that state, and calls a slow corner while you are still in it: "Turn 7, losing 0.3."
So heavy fuel or old tyres don't get blamed on you. Models are kept in `models/`.

**Theoretical best.** Your best lap plus your best time in every corner: "Theoretical best 1 21.9, time
left in turn 1 and turn 11."

**Session report.** After every lap the engineer rewrites a page in `reports/` for that session: lap
times, time lost or gained per corner against your best lap, theoretical best, habits, and tyre deg in a race.
Open it in a browser between stints.

GPU PyTorch for an AMD RX 9060 XT (gfx1200) on Windows, from
[AMD's ROCm PyTorch page](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/frameworks/pytorch/install.html):

```
python -m pip install --index-url https://stable.repo.amd.com/rocm/whl-next/ "torch[device-gfx1200]==2.14.0+rocm10.1.0"
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

NVIDIA cards: the CUDA build from pytorch.org. No GPU: `pip install torch` runs it on the CPU, which is fast enough.
The engineer log prints `coach trained on cuda` (ROCm also reports as cuda) or `on cpu`.

**Race:**
- the real-race strategy for that track, scaled to your race length (pit window lap), replaced by your own once it has 4 laps of your tyre deg: "Your tyres are losing 0.12 a lap. Best stop around lap 23.";
- position and gaps every lap, plus DRS range;
- fuel plus or minus, battery, and tyre wear;
- live "lift and coast into turn X" before the two biggest stops when fuel, brakes or tyres need saving;
- diff and bias changes on the MFD when tyres overheat;
- safety car (including a cheap pit stop call), penalties, front wing damage, weather, and the last lap.

Corner numbers are the official ones, placed using real F1 track data.

## Race simulator

Test the race calls without driving a race. `simulate.py` drives a fake race through the engineer in
seconds: tyre deg, fuel burn, lap-time noise, mistakes, a safety car, and a driver who boxes when told.
Every radio call is logged, and the stop is graded against the best stop for the true deg.

```
python simulate.py                                       18 laps at Monza, safety car on lap 11
python simulate.py --laps 30 --deg 0.15 --sc 0 --seed 4  other race, other luck
python simulate.py --ask "8:when should we pit"          a driver question on lap 8
python simulate.py --live --speed 10                     into the running engineer: hear it, ask on the radio button
```

Logs and report pages go to `reports/sim/`. The running engineer logs its own calls, and what
talk-back heard, to `engineer.log`.

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
| `coach.py` | Optional live corner predictor (PyTorch) |
| `talkback.py` | Optional talk-back: push to talk, Whisper speech to text |
| `session_report.py` | Session report pages in `reports/` |
| `simulate.py` | Race simulator with logging |
| `fakegame.py` | Fake F1 25 packets for the tests and the simulator |
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

r"""Runs the engineer for exactly as long as F1 25 runs. Steam starts it, nothing else does.

    Steam > F1 25 > Properties > Launch Options:
    "C:\Users\AKHLAQ\AppData\Local\Programs\Python\Python312\pythonw.exe" "<this folder>\launch.py" %command%

Steam swaps %command% for the game. This waits on the game process (no polling) and stops the engineer when it exits.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

app = subprocess.Popen([sys.executable, os.path.join(HERE, 'app.py')], cwd=HERE)
try:
    # shortcut: if the game ever relaunches itself (anti-cheat/EA handoff) this returns early; then wait on F1_25.exe by name
    subprocess.run(sys.argv[1:])
finally:
    app.terminate()

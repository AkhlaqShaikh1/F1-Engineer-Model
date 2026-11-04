"""Team-radio voice.

Piper neural TTS runs as its own process (Piper is GPL-3.0; keeping it out of process keeps this
code MIT). Its audio gets a radio filter (narrow band, crunch, hiss, squelch) and plays through
winsound. Without Piper or a voice model, falls back to the built-in Windows voice.

    python voice.py "Box box, box box."            play a sample with the configured voice
    python voice.py --save out.wav "Box box."      save it instead
"""
import array
import io
import json
import math
import os
import queue
import random
import subprocess
import sys
import tempfile
import threading
import wave
import winsound

HERE = os.path.dirname(os.path.abspath(__file__))
VOICES = os.path.join(HERE, 'voices')
DEFAULT_VOICE = 'en_GB-northern_english_male-medium'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
UTTERANCE_GAP = 0.5  # seconds of silence from Piper that ends one radio message


def python_exe():
    exe = sys.executable  # under pythonw this is pythonw.exe, which is fine for a piped child
    return exe if exe else 'python'


def radio_fx(pcm, rate):
    """int16 mono PCM in, int16 mono PCM out: beep, band-passed crunchy speech, squelch tail."""
    hp_a = math.exp(-2 * math.pi * 300 / rate)
    lp_a = 1 - math.exp(-2 * math.pi * 3000 / rate)
    out = array.array('h')
    beep = int(0.06 * rate)
    for i in range(beep):
        out.append(int(5000 * math.sin(2 * math.pi * 1400 * i / rate) * (1 - i / beep)))
    out.extend([0] * int(0.05 * rate))
    px = ph = lp = 0.0
    for x in array.array('h', pcm):
        ph = hp_a * (ph + x - px)
        px = x
        lp += lp_a * (ph - lp)
        y = math.tanh(lp / 32768 * 2.5) * 0.75 + random.uniform(-0.012, 0.012)
        out.append(int(y * 32767))
    tail = int(0.12 * rate)
    for i in range(tail):
        out.append(int(random.uniform(-1, 1) * 9000 * (1 - i / tail)))
    return out.tobytes()


def to_wav(pcm, rate):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class Revoicer:
    """Optional RVC step: re-voices Piper's audio with a trained voice model (Applio), kept loaded in
    rvc_worker.py under Applio's own Python. If anything fails, the line plays in the Piper voice."""

    def __init__(self, model):
        folder = os.path.dirname(os.path.abspath(model))
        applio = os.path.dirname(os.path.dirname(folder))  # models live in <Applio>/logs/<name>/
        index = next((os.path.join(folder, f) for f in os.listdir(folder) if f.endswith('.index')), '')
        self.tmp = os.path.join(tempfile.gettempdir(), f'f1-engineer-rvc-{os.getpid()}')
        self.lock = threading.Lock()
        self.proc = subprocess.Popen(
            [os.path.join(applio, '.venv', 'Scripts', 'python.exe'), os.path.join(HERE, 'rvc_worker.py'), model, index],
            cwd=applio, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, creationflags=NO_WINDOW)
        self.ready = False
        self.lock.acquire()  # held until the model has loaded (~15 s): lines asked for meanwhile wait, then convert
        threading.Thread(target=self.wait_ready, daemon=True).start()

    def wait_ready(self):
        try:
            self.ready = self.proc.stdout.readline().strip() == 'ready'
        finally:
            self.lock.release()

    def convert(self, pcm, rate):
        """int16 mono PCM in; (pcm, rate) in the new voice out, or None if the worker failed."""
        with self.lock:
            if not self.ready:
                return None
            src, dst = self.tmp + '-in.wav', self.tmp + '-out.wav'
            try:
                with open(src, 'wb') as f:
                    f.write(to_wav(pcm, rate))
                self.proc.stdin.write(f'{src}\t{dst}\n')
                self.proc.stdin.flush()
                if self.proc.stdout.readline().strip() != 'ok':
                    return None
                with wave.open(dst, 'rb') as w:
                    return w.readframes(w.getnframes()), w.getframerate()
            except (OSError, wave.Error):  # worker died or wrote nothing: stop trying for this session
                self.ready = False
                return None


def make_revoicer(model):
    if not model or not os.path.exists(model):
        return None
    try:
        return Revoicer(model)
    except OSError:  # Applio or its venv not found
        return None


class PiperVoice:
    def __init__(self, model, revoicer=None):
        self.revoicer = revoicer
        with open(model + '.json', encoding='utf-8') as f:
            self.rate = json.load(f)['audio']['sample_rate']
        self.proc = subprocess.Popen(
            [python_exe(), '-m', 'piper', '-m', model, '--output-raw', '--length-scale', '0.92'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW)
        self.chunks = queue.Queue()
        threading.Thread(target=self.read, daemon=True).start()
        threading.Thread(target=self.play, daemon=True).start()

    def say(self, text):
        self.proc.stdin.write((text.replace('\n', ' ') + '\n').encode('utf-8'))
        self.proc.stdin.flush()

    def read(self):
        while True:
            data = self.proc.stdout.read1(8192)
            if not data:
                return
            self.chunks.put(data)

    def play(self):
        buf = bytearray()
        while True:
            try:
                buf += self.chunks.get(timeout=UTTERANCE_GAP if buf else None)
            except queue.Empty:  # Piper went quiet: the message is complete
                pcm, rate = bytes(buf[:len(buf) // 2 * 2]), self.rate
                buf.clear()
                pcm, rate = (self.revoicer and self.revoicer.convert(pcm, rate)) or (pcm, rate)
                winsound.PlaySound(to_wav(radio_fx(pcm, rate), rate), winsound.SND_MEMORY)


class WindowsVoice:
    """Built-in System.Speech voice, kept warm in one PowerShell process."""

    def __init__(self):
        script = ("Add-Type -AssemblyName System.Speech;"
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;$s.Rate=1;"
                  "while(($l=[Console]::In.ReadLine()) -ne $null){try{$s.Speak($l)}catch{}}")
        self.proc = subprocess.Popen(['powershell', '-NoProfile', '-Command', script],
                                     stdin=subprocess.PIPE, text=True, creationflags=NO_WINDOW)

    def say(self, text):
        self.proc.stdin.write(text.replace('\n', ' ') + '\n')
        self.proc.stdin.flush()


def piper_installed():
    try:
        return subprocess.run([python_exe(), '-c', 'import piper'], capture_output=True,
                              creationflags=NO_WINDOW).returncode == 0
    except OSError:
        return False


def make_voice(name=DEFAULT_VOICE, rvc_model=None):
    """name: a Piper voice in voices/ (e.g. en_GB-alan-medium), or 'windows'.
    rvc_model: optional path to an Applio RVC .pth that re-voices the Piper audio."""
    model = os.path.join(VOICES, f'{name}.onnx')
    if name != 'windows' and os.path.exists(model) and piper_installed():
        return PiperVoice(model, make_revoicer(rvc_model))
    try:
        return WindowsVoice()
    except OSError:
        return None


def render(text, name=DEFAULT_VOICE, rvc_model=None):
    """One-off: radio WAV bytes for text, for samples and tests."""
    model = os.path.join(VOICES, f'{name}.onnx')
    with open(model + '.json', encoding='utf-8') as f:
        rate = json.load(f)['audio']['sample_rate']
    pcm = subprocess.run([python_exe(), '-m', 'piper', '-m', model, '--output-raw', '--length-scale', '0.92'],
                         input=text.encode('utf-8'), capture_output=True, creationflags=NO_WINDOW).stdout
    pcm = pcm[:len(pcm) // 2 * 2]
    revoicer = make_revoicer(rvc_model)
    pcm, rate = (revoicer and revoicer.convert(pcm, rate)) or (pcm, rate)
    return to_wav(radio_fx(pcm, rate), rate)


if __name__ == '__main__':
    args = sys.argv[1:]
    cfg_path = os.path.join(HERE, 'config.json')
    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = json.load(f)
    name, rvc_model = cfg.get('voice', DEFAULT_VOICE), cfg.get('rvc_model')
    if args[:1] == ['--save']:
        with open(args[1], 'wb') as f:
            f.write(render(' '.join(args[2:]), name, rvc_model))
    else:
        winsound.PlaySound(render(' '.join(args) or 'Radio check. How do you read?', name, rvc_model),
                           winsound.SND_MEMORY)

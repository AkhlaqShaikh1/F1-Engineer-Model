"""Keeps an RVC voice model loaded and re-voices WAV files on request, for voice.py.

Runs under Applio's own Python with Applio's folder as the working directory (voice.py starts it):
    <Applio>/.venv/Scripts/python rvc_worker.py <model.pth> <model.index>
stdin: one "<in.wav>\t<out.wav>" per line. stdout: "ready" once loaded, then "ok" or "err <message>" per line.
"""
import os
import sys
import wave

reply = sys.stdout
sys.stdout = sys.stderr  # Applio prints progress; stdout carries only our replies
sys.path.insert(0, os.getcwd())

import torch  # noqa: E402
torch.set_num_threads(4)  # leave the other cores to the game

import core  # noqa: E402  Applio's core.py, found through the working directory
from rvc.infer.infer import VoiceConverter  # noqa: E402

model, index = sys.argv[1:3]
kwargs = core._base_infer_kwargs(pth_path=model, index_path=index)  # same settings as `core.py infer`
vc = VoiceConverter()


def convert(src, dst):
    vc.convert_audio(audio_input_path=src, audio_output_path=dst, **kwargs)


def send(line):
    reply.write(line + '\n')
    reply.flush()


# Warm up: the first conversion loads ContentVec, RMVPE and the model, so do it before the race needs it.
warm = os.path.join(os.path.dirname(os.path.abspath(model)), '_warmup.wav')
with wave.open(warm, 'wb') as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(b'\0\0' * 16000)
convert(warm, warm)
os.remove(warm)
send('ready')

for line in sys.stdin:
    try:
        convert(*line.rstrip('\n').split('\t'))
        send('ok')
    except Exception as e:  # one bad line must not kill the voice for the rest of the race
        send(f'err {e!r}'.replace('\n', ' '))

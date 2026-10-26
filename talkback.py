"""Talk-back: hold the radio button, ask your question, let go. The engineer answers on the radio.

Speech to text is OpenAI Whisper running locally, on the GPU when PyTorch sees one. A quick tap
of the radio button still gives the usual update. Needs: pip install openai-whisper sounddevice
Without them the radio button works as before.
"""
import threading

try:
    import numpy
    import sounddevice
    import torch
    import whisper
except ImportError:
    whisper = None

RATE = 16000        # Whisper's sample rate
TAP = 0.5           # seconds: a shorter press is a tap, not a question
MODEL = 'base.en'   # shortcut: small and quick; 'small.en' hears better through engine noise, upgrade if it mishears


class Ears:
    def __init__(self, on_text, on_tap):
        self.on_text, self.on_tap = on_text, on_tap
        self.model = None
        self.frames, self.stream = [], None
        threading.Thread(target=self.load, daemon=True).start()  # first run downloads the model, ~140 MB

    def load(self):
        model = whisper.load_model(MODEL, device='cuda' if torch.cuda.is_available() else 'cpu')
        model.transcribe(numpy.zeros(RATE, numpy.float32), language='en', fp16=model.device.type == 'cuda')  # GPU warm-up: the first run is ~15 s
        self.model = model
        print(f"Talk-back ready, Whisper {MODEL} on {self.model.device}.", flush=True)

    def press(self):
        self.frames = []
        try:
            self.stream = sounddevice.InputStream(samplerate=RATE, channels=1, dtype='float32',
                                                  callback=lambda data, *_: self.frames.append(data.copy()))
            self.stream.start()
        except sounddevice.PortAudioError as err:
            print('Talk-back: no microphone,', err, flush=True)
            self.stream = None  # the tap still works

    def release(self, held):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        why = ('tap' if held < TAP else 'Whisper still loading' if not self.model
               else 'no audio from the microphone' if not self.frames else None)
        if why:
            print(f'Talk-back: held {held:.1f}s, {why}: usual update.', flush=True)
            return self.on_tap()
        audio = numpy.concatenate(self.frames)[:, 0]
        threading.Thread(target=self.hear, args=(audio,), daemon=True).start()

    def hear(self, audio):
        text = self.model.transcribe(audio, language='en', fp16=self.model.device.type == 'cuda')['text'].strip()
        print(f'Driver ({len(audio) / RATE:.1f}s, level {float(numpy.abs(audio).max()):.2f}): "{text}"', flush=True)
        self.on_text(text)

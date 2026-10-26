"""Optional live corner predictor: a small neural net that learns your clean laps, on your GPU if PyTorch sees one.

From your position, speed and pedals mid-corner it predicts the time left to the corner exit, so the
engineer can call a slow corner while you are still in it. One model per track, kept in models/.
Needs PyTorch (pip install torch). Without it the engineer works exactly as before.
"""
import os
import threading

try:
    import torch
except ImportError:
    torch = None

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, 'models')
MIN_LAPS = 5        # laps of data before it trains and starts making live calls
MAX_ROWS = 50000    # shortcut: keeps only the newest rows, upgrade to sampling across sessions if old laps matter


def features(frac, to_exit, speed, throttle, brake):
    return [frac, to_exit / 1000, speed / 300, throttle, brake]


def build():
    return torch.nn.Sequential(torch.nn.Linear(5, 64), torch.nn.ReLU(), torch.nn.Linear(64, 64),
                               torch.nn.ReLU(), torch.nn.Linear(64, 1))


class Coach:
    def __init__(self, track):
        self.path = os.path.join(MODELS, f'{track}.pt')
        self.x, self.y, self.laps = [], [], 0
        self.net = None      # CPU copy used for live calls; training swaps in a new one
        self.busy = False
        try:
            saved = torch.load(self.path, weights_only=True)
        except Exception:  # no model yet, or a corrupt file: start fresh
            return
        self.x, self.y, self.laps = saved['x'].tolist(), saved['y'].tolist(), saved['laps']
        if saved.get('net'):
            self.net = build()
            self.net.load_state_dict(saved['net'])
            self.net.eval()

    def add_lap(self, lap, zones, track_len):
        """Rows from one clean lap: in each corner, (state now) -> seconds to the corner exit."""
        from engineer import time_at
        for z in zones:
            a, b = z['brake'] - 60, z['power'] + 60
            tb = time_at(lap, b)
            for s in lap['trace']:
                if a <= s[0] < b:
                    self.x.append(features(s[0] / track_len, b - s[0], s[2], s[3], s[4]))
                    self.y.append((tb - s[1]) / 1000)
        self.x, self.y = self.x[-MAX_ROWS:], self.y[-MAX_ROWS:]
        self.laps += 1
        if self.laps >= MIN_LAPS and not self.busy:
            self.busy = True
            threading.Thread(target=self.train, args=(list(self.x), list(self.y)), daemon=True).start()

    def train(self, x, y, steps=600):
        try:
            dev = 'cuda' if torch.cuda.is_available() else 'cpu'  # ROCm builds also report as cuda
            X = torch.tensor(x, device=dev)
            Y = torch.tensor(y, device=dev).unsqueeze(1)
            net = build().to(dev)
            opt = torch.optim.Adam(net.parameters(), lr=3e-3)
            for _ in range(steps):
                opt.zero_grad()
                loss = torch.nn.functional.mse_loss(net(X), Y)
                loss.backward()
                opt.step()
            print(f"coach trained on {dev}: {len(x)} rows, loss {loss.item():.4f}", flush=True)
            self.net = net.cpu().eval()
            os.makedirs(MODELS, exist_ok=True)
            torch.save({'x': X.cpu(), 'y': Y.squeeze(1).cpu(), 'laps': self.laps,
                        'net': self.net.state_dict()}, self.path)
        finally:
            self.busy = False

    def predict(self, frac, to_exit, speed, throttle, brake):
        """Seconds left to the corner exit, or None until the first training run is done."""
        net = self.net
        if net is None:
            return None
        with torch.no_grad():
            return net(torch.tensor([features(frac, to_exit, speed, throttle, brake)])).item()

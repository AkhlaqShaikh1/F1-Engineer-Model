"""F1 25 Race Engineer: voice + on-screen radio overlay.

    pythonw app.py            run with no console (what the auto-start shortcut does)
    python app.py             run with a console log
    python app.py --bind      set the controller radio button
    python app.py --unbind    remove it
    python app.py --mute      overlay only, no voice

The overlay appears only when the engineer talks, so it is invisible when the game is closed.
It shows over the game in Borderless or Windowed mode, not exclusive Fullscreen.
Right-click the overlay to quit. Drag it to move it.
"""
import json
import os
import queue
import socket
import struct
import sys
import threading
import tkinter as tk

import controller
import engineer
import voice

PORT = 20777
CONFIG = os.path.join(engineer.HERE, 'config.json')


def load_config():
    cfg = {'x': None, 'y': 40, 'radio_button': 0, 'voice': voice.DEFAULT_VOICE}
    cfg.update(engineer.load_json(CONFIG))
    return cfg


def save_config(cfg):
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=1)


class Overlay:
    def __init__(self, cfg):
        self.cfg = cfg
        self.inbox = queue.Queue()
        self.hide_job = None
        root = self.root = tk.Tk()
        root.title('F1 25 Race Engineer')
        root.overrideredirect(True)
        root.attributes('-topmost', True, '-alpha', 0.9)
        root.configure(bg='#111418')
        frame = tk.Frame(root, bg='#111418', highlightthickness=2, highlightbackground='#e10600')
        frame.pack(fill='both', expand=True)
        tk.Label(frame, text='● RADIO', fg='#e10600', bg='#111418',
                 font=('Segoe UI', 9, 'bold'), anchor='w').pack(fill='x', padx=12, pady=(8, 0))
        self.text = tk.Label(frame, fg='#f2f2f2', bg='#111418', font=('Segoe UI', 13),
                             wraplength=560, justify='left', anchor='w')
        self.text.pack(fill='both', padx=12, pady=(2, 10))
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label='Quit engineer', command=root.destroy)
        for w in (frame, self.text):
            w.bind('<ButtonPress-1>', self.drag_start)
            w.bind('<B1-Motion>', self.drag)
            w.bind('<ButtonRelease-1>', self.drag_end)
            w.bind('<Button-3>', lambda e: menu.tk_popup(e.x_root, e.y_root))
        root.withdraw()
        root.after(100, self.pump)

    def show(self, text):
        self.text.config(text=text)
        self.root.update_idletasks()
        w = self.root.winfo_reqwidth()
        x = self.cfg['x'] if self.cfg['x'] is not None else (self.root.winfo_screenwidth() - w) // 2
        self.root.geometry(f'+{x}+{self.cfg["y"]}')
        self.root.deiconify()
        self.root.lift()
        self.root.attributes('-topmost', True)
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
        self.hide_job = self.root.after(int(max(6, len(text) / 12) * 1000), self.root.withdraw)

    def pump(self):
        try:
            while True:
                self.show(self.inbox.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def drag_start(self, e):
        self.offset = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def drag(self, e):
        self.root.geometry(f'+{e.x_root - self.offset[0]}+{e.y_root - self.offset[1]}')

    def drag_end(self, e):
        self.cfg['x'], self.cfg['y'] = self.root.winfo_x(), self.root.winfo_y()
        save_config(self.cfg)


def listen(sock, eng):
    while True:
        data, _ = sock.recvfrom(4096)
        try:
            eng.handle(data)
        except (struct.error, IndexError):
            pass  # partial or unknown packet, skip it


def bind(cfg):
    print('Hold the button combo you want as the radio button for 2 seconds.')
    print('Pick buttons that do nothing in F1 25 (example: both stick clicks).')
    combo = controller.capture()
    cfg['radio_button'] = combo
    save_config(cfg)
    print('Radio button set:', controller.describe(combo))


def main():
    args = sys.argv[1:]
    cfg = load_config()
    if '--bind' in args:
        return bind(cfg)
    if '--unbind' in args:
        cfg['radio_button'] = 0
        save_config(cfg)
        return print('Radio button removed.')
    if sys.stdout is None:  # pythonw has no console; print() must not crash
        sys.stdout = sys.stderr = open(os.devnull, 'w')

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(('0.0.0.0', PORT))
    except OSError:
        return print(f'Port {PORT} busy: the engineer is already running.')  # also our single-instance guard

    overlay = Overlay(cfg)
    speaker = None if '--mute' in args else voice.make_voice(cfg['voice'])
    eng = engineer.Engineer(engineer.Radio(speaker, on_say=overlay.inbox.put))
    threading.Thread(target=listen, args=(sock, eng), daemon=True).start()
    if cfg['radio_button']:
        controller.watch(cfg['radio_button'], eng.report)
    print(f'Race engineer listening on UDP {PORT}.')
    overlay.root.mainloop()


if __name__ == '__main__':
    main()

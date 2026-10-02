"""
player_tab.py - an iTunes-library music player tab for the YouTube-to-MP3 GUI.

Drop this file next to yt_to_mp3_gui.py and add it as a tab (see the
integration snippet in the chat). It needs:

    python -m pip install python-vlc
    + VLC media player installed (https://www.videolan.org/vlc/), with the same
      bitness as your Python (64-bit Python -> 64-bit VLC)

If python-vlc or VLC is missing, the tab still loads and shows a message, so
the downloader tab keeps working.

To make the iTunes library readable, turn on in iTunes:
    Edit > Preferences > Advanced > "Share iTunes Library XML with other applications"
"""

import os
import plistlib
import random
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse
from urllib.request import url2pathname

try:
    import vlc  # python-vlc
except Exception:  # module missing, or libvlc itself not found
    vlc = None

AUDIO_EXTS = {
    ".mp3", ".m4a", ".m4b", ".aac", ".wav", ".flac",
    ".ogg", ".aiff", ".aif", ".wma",
}


# --------------------------------------------------------------------------
# Library loading (no GUI code here, so it is easy to test on its own)
# --------------------------------------------------------------------------

def fmt_time(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def default_library_xml():
    """Return the usual iTunes XML location if it exists, else None."""
    music = os.path.join(os.path.expanduser("~"), "Music")
    for folder, name in [
        ("iTunes", "iTunes Music Library.xml"),
        ("iTunes", "iTunes Library.xml"),
        ("", "Library.xml"),
    ]:
        path = os.path.join(music, folder, name) if folder else os.path.join(music, name)
        if os.path.isfile(path):
            return path
    return None


def location_to_path(location):
    """Convert an iTunes 'file://localhost/C:/...' URL into a normal path."""
    parsed = urlparse(location)
    if parsed.scheme != "file":
        return None
    return url2pathname(parsed.path)


def load_itunes_xml(xml_path):
    """
    Parse an iTunes library XML file.

    Returns (playlists, missing) where playlists is a dict of
    {playlist name: [track dicts]} (always including "Library") and
    missing is the number of tracks whose file could not be found.
    """
    with open(xml_path, "rb") as f:
        data = plistlib.load(f)

    tracks = {}
    missing = 0
    for key, t in data.get("Tracks", {}).items():
        location = t.get("Location")
        # Skip cloud-only tracks (no Location) and DRM-protected files
        if not location or t.get("Protected"):
            continue
        path = location_to_path(location)
        if not path or os.path.splitext(path)[1].lower() not in AUDIO_EXTS:
            continue
        if not os.path.isfile(path):
            missing += 1
            continue
        artist = t.get("Artist", "")
        album = t.get("Album", "")
        tracks[int(t.get("Track ID", key))] = {
            "title": t.get("Name", os.path.splitext(os.path.basename(path))[0]),
            "artist": artist,
            "album": album,
            "duration": t.get("Total Time", 0) / 1000,
            "path": path,
            "_sort": (
                artist.lower(), album.lower(),
                t.get("Disc Number", 0), t.get("Track Number", 0),
            ),
        }

    playlists = {"Library": sorted(tracks.values(), key=lambda t: t["_sort"])}

    for pl in data.get("Playlists", []):
        if pl.get("Master"):  # the master list is our "Library"
            continue
        items = [
            tracks[i["Track ID"]]
            for i in pl.get("Playlist Items", [])
            if i.get("Track ID") in tracks
        ]
        if not items:
            continue
        base = pl.get("Name", "Untitled")
        name, n = base, 2
        while name in playlists:
            name = f"{base} ({n})"
            n += 1
        playlists[name] = items

    return playlists, missing


def scan_folder(folder):
    """Fallback: build a track list from audio files in a folder (no tags)."""
    tracks = []
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            stem, ext = os.path.splitext(name)
            if ext.lower() in AUDIO_EXTS:
                tracks.append({
                    "title": stem,
                    "artist": "",
                    "album": os.path.basename(root),
                    "duration": 0,
                    "path": os.path.join(root, name),
                })
    return tracks


# --------------------------------------------------------------------------
# The tab
# --------------------------------------------------------------------------

class PlayerTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.playlists = {}
        self.base_tracks = []      # tracks of the selected playlist
        self.queue = []            # tracks currently shown (after search filter)
        self.current_track = None
        self.seeking = False
        self.controls = []
        self.vlc_instance = None
        self.player = None
        self._alive = True
        self._after_id = None

        self._build_ui()
        self._init_player()

        self.bind("<Destroy>", lambda e: self.shutdown() if e.widget is self else None)
        self.after(100, self._auto_load)
        self._after_id = self.after(500, self._tick)

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Button(top, text="Load iTunes XML…", command=self.choose_xml).pack(side="left")
        ttk.Button(top, text="Open folder…", command=self.choose_folder).pack(side="left", padx=(6, 0))

        self.playlist_var = tk.StringVar()
        self.playlist_box = ttk.Combobox(
            top, textvariable=self.playlist_var, state="readonly", width=28
        )
        self.playlist_box.pack(side="left", padx=8)
        self.playlist_box.bind("<<ComboboxSelected>>", lambda e: self._select_playlist())

        ttk.Label(top, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.search_var, width=22).pack(side="left", padx=4)
        self.search_var.trace_add("write", lambda *a: self._refresh())

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, padx=8)
        cols = ("title", "artist", "album", "time")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for col, text, width in [
            ("title", "Title", 260), ("artist", "Artist", 170),
            ("album", "Album", 170), ("time", "Time", 60),
        ]:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="e" if col == "time" else "w")
        scroll = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Return>", self._on_return)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=8, pady=8)
        self.now_var = tk.StringVar(value="Nothing playing")
        ttk.Label(bottom, textvariable=self.now_var).pack(anchor="w")

        seek_row = ttk.Frame(bottom)
        seek_row.pack(fill="x", pady=4)
        self.seek_var = tk.DoubleVar(value=0)
        self.seek = ttk.Scale(seek_row, from_=0, to=1000, variable=self.seek_var)
        self.seek.pack(side="left", fill="x", expand=True)
        self.seek.bind("<ButtonPress-1>", lambda e: setattr(self, "seeking", True))
        self.seek.bind("<ButtonRelease-1>", self._on_seek_release)
        self.time_var = tk.StringVar(value="0:00 / 0:00")
        ttk.Label(seek_row, textvariable=self.time_var, width=12).pack(side="left", padx=(8, 0))

        btns = ttk.Frame(bottom)
        btns.pack(fill="x")
        self.prev_btn = ttk.Button(btns, text="⏮ Prev", command=self.prev_track)
        self.play_btn = ttk.Button(btns, text="▶ Play", command=self.toggle_pause)
        self.stop_btn = ttk.Button(btns, text="⏹ Stop", command=self.stop)
        self.next_btn = ttk.Button(btns, text="Next ⏭", command=self.next_track)
        for b in (self.prev_btn, self.play_btn, self.stop_btn, self.next_btn):
            b.pack(side="left", padx=(0, 4))
            self.controls.append(b)

        self.shuffle_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns, text="Shuffle", variable=self.shuffle_var).pack(side="left", padx=8)

        self.volume = ttk.Scale(btns, from_=0, to=100, length=110, command=self._set_volume)
        self.volume.set(80)
        self.volume.pack(side="right")
        ttk.Label(btns, text="Volume").pack(side="right", padx=(0, 6))
        self.controls.append(self.volume)

        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var, foreground="gray").pack(anchor="w", pady=(6, 0))

    def _init_player(self):
        if vlc is None:
            self.status_var.set(
                "VLC not available. Run: python -m pip install python-vlc, "
                "and install VLC media player (same 32/64-bit as Python)."
            )
            self._set_controls_enabled(False)
            return
        try:
            self.vlc_instance = vlc.Instance("--no-video")
            self.player = self.vlc_instance.media_player_new()
        except Exception as exc:
            self.status_var.set(f"Could not start VLC: {exc}")
            self._set_controls_enabled(False)

    def _set_controls_enabled(self, enabled):
        for c in self.controls:
            try:
                c.state(["!disabled"] if enabled else ["disabled"])
            except tk.TclError:
                pass

    # ---- Loading ----------------------------------------------------------

    def _auto_load(self):
        if not self._alive:
            return
        path = default_library_xml()
        if path:
            self.load_xml(path)
        else:
            self.status_var.set(
                self.status_var.get()
                or "No iTunes library XML found. Use 'Load iTunes XML…' or 'Open folder…'."
            )

    def choose_xml(self):
        path = filedialog.askopenfilename(
            title="Choose iTunes library XML",
            initialdir=os.path.join(os.path.expanduser("~"), "Music"),
            filetypes=[("iTunes library XML", "*.xml"), ("All files", "*.*")],
        )
        if path:
            self.load_xml(path)

    def load_xml(self, path):
        try:
            playlists, missing = load_itunes_xml(path)
        except Exception as exc:
            messagebox.showerror("Could not read library", f"{path}\n\n{exc}")
            return
        self._set_playlists(playlists)
        total = len(playlists["Library"])
        note = f" ({missing} files not found on disk)" if missing else ""
        self.status_var.set(f"Loaded {total} tracks from iTunes library{note}.")

    def choose_folder(self):
        folder = filedialog.askdirectory(
            title="Choose a music folder",
            initialdir=os.path.join(os.path.expanduser("~"), "Downloads"),
        )
        if not folder:
            return
        tracks = scan_folder(folder)
        if not tracks:
            messagebox.showinfo("No music found", "No audio files were found in that folder.")
            return
        self._set_playlists({os.path.basename(folder) or folder: tracks})
        self.status_var.set(f"Loaded {len(tracks)} tracks from folder.")

    def _set_playlists(self, playlists):
        self.playlists = playlists
        names = list(playlists.keys())
        self.playlist_box["values"] = names
        self.playlist_var.set(names[0])
        self._select_playlist()

    def _select_playlist(self):
        self.base_tracks = self.playlists.get(self.playlist_var.get(), [])
        self._refresh()

    def _refresh(self):
        query = self.search_var.get().strip().lower()
        if query:
            self.queue = [
                t for t in self.base_tracks
                if query in f"{t['title']} {t['artist']} {t['album']}".lower()
            ]
        else:
            self.queue = list(self.base_tracks)

        self.tree.delete(*self.tree.get_children())
        for i, t in enumerate(self.queue):
            self.tree.insert(
                "", "end", iid=str(i),
                values=(t["title"], t["artist"], t["album"],
                        fmt_time(t["duration"]) if t["duration"] else ""),
            )
        self._highlight_current()

    def _highlight_current(self):
        idx = self._index_of(self.current_track)
        if idx is not None:
            self.tree.selection_set(str(idx))
            self.tree.see(str(idx))

    def _index_of(self, track):
        if track is None:
            return None
        for i, t in enumerate(self.queue):
            if t is track:
                return i
        return None

    # ---- Playback ---------------------------------------------------------

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.play_track(self.queue[int(iid)])

    def _on_return(self, _event):
        sel = self.tree.selection()
        if sel:
            self.play_track(self.queue[int(sel[0])])

    def play_track(self, track):
        if not self.player:
            return
        if not os.path.isfile(track["path"]):
            self.status_var.set(f"File not found: {track['path']}")
            return
        media = self.vlc_instance.media_new(track["path"])
        self.player.set_media(media)
        self.player.play()
        self.player.audio_set_volume(int(float(self.volume.get())))
        self.current_track = track
        self.play_btn.config(text="⏸ Pause")
        who = f" — {track['artist']}" if track["artist"] else ""
        self.now_var.set(f"{track['title']}{who}")
        self._highlight_current()

    def toggle_pause(self):
        if not self.player:
            return
        if self.current_track is None:
            sel = self.tree.selection()
            if sel:
                self.play_track(self.queue[int(sel[0])])
            elif self.queue:
                self.play_track(self.queue[0])
            return
        state = self.player.get_state()
        if state == vlc.State.Playing:
            self.player.set_pause(1)
            self.play_btn.config(text="▶ Play")
        elif state == vlc.State.Paused:
            self.player.set_pause(0)
            self.play_btn.config(text="⏸ Pause")
        else:  # stopped or ended: start the current track again
            self.play_track(self.current_track)

    def stop(self):
        if self.player:
            self.player.stop()
        self.play_btn.config(text="▶ Play")
        self.seek_var.set(0)
        self.time_var.set("0:00 / 0:00")

    def next_track(self):
        if not self.queue:
            return
        if self.shuffle_var.get():
            self.play_track(random.choice(self.queue))
            return
        idx = self._index_of(self.current_track)
        nxt = 0 if idx is None else (idx + 1) % len(self.queue)
        self.play_track(self.queue[nxt])

    def prev_track(self):
        if not self.queue:
            return
        idx = self._index_of(self.current_track)
        prv = 0 if idx is None else (idx - 1) % len(self.queue)
        self.play_track(self.queue[prv])

    def _set_volume(self, value):
        if self.player:
            self.player.audio_set_volume(int(float(value)))

    def _on_seek_release(self, _event):
        if self.player and self.current_track is not None:
            self.player.set_position(self.seek_var.get() / 1000)
        self.seeking = False

    def _tick(self):
        """Runs twice a second: moves the seek bar and advances at track end."""
        if not self._alive:
            return
        try:
            if self.player and self.current_track is not None:
                state = self.player.get_state()
                if state == vlc.State.Ended:
                    self.next_track()
                elif state in (vlc.State.Playing, vlc.State.Paused) and not self.seeking:
                    length = self.player.get_length()
                    pos = self.player.get_time()
                    if length > 0 and pos >= 0:
                        self.seek_var.set(pos / length * 1000)
                        self.time_var.set(f"{fmt_time(pos / 1000)} / {fmt_time(length / 1000)}")
        except Exception as exc:  # never let a hiccup kill the timer
            self.status_var.set(f"Player error: {exc}")
        finally:
            if self._alive:
                try:
                    self._after_id = self.after(500, self._tick)
                except tk.TclError:
                    pass

    # ---- Cleanup ----------------------------------------------------------

    def shutdown(self):
        """Stop playback; called automatically when the tab is destroyed."""
        self._alive = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        if self.player:
            try:
                self.player.stop()
                self.player.release()
            except Exception:
                pass
            self.player = None

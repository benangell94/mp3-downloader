"""
iTunes Play Count Editor
------------------------
Bulk-edit the play count of multiple songs in your iTunes library.

Requirements (Windows only):
    pip install pywin32
    iTunes for Windows installed (it will be launched automatically)

Usage:
    python itunes_playcount_editor.py
"""

import tkinter as tk
from tkinter import ttk, messagebox

try:
    import win32com.client
except ImportError:
    win32com = None


class PlayCountEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("iTunes Play Count Editor")
        self.geometry("900x600")
        self.minsize(720, 480)

        self.itunes = None
        self.data = []            # list of dicts: obj, name, artist, album, plays
        self.sort_state = {}      # column -> reverse?

        self._build_ui()
        self.after(200, self.load_library)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # Top bar: search + reload
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_tree())
        ttk.Entry(top, textvariable=self.search_var, width=35).pack(side="left", padx=6)

        ttk.Button(top, text="Reload library", command=self.load_library).pack(side="right")

        # Track list
        mid = ttk.Frame(self, padding=(8, 0))
        mid.pack(fill="both", expand=True)

        cols = ("name", "artist", "album", "plays")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        widths = {"name": 300, "artist": 200, "album": 240, "plays": 70}
        for c in cols:
            self.tree.heading(c, text=c.title(), command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=widths[c], anchor="e" if c == "plays" else "w")

        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_selection_label())
        self.tree.bind("<Control-a>", self.select_all)

        # Selection helpers
        sel = ttk.Frame(self, padding=(8, 4))
        sel.pack(fill="x")
        ttk.Button(sel, text="Select all shown", command=self.select_all).pack(side="left")
        ttk.Button(sel, text="Clear selection", command=lambda: self.tree.selection_remove(self.tree.selection())).pack(side="left", padx=6)
        self.sel_label = ttk.Label(sel, text="0 selected")
        self.sel_label.pack(side="right")

        # Edit controls
        box = ttk.LabelFrame(self, text="Change play count of selected songs", padding=8)
        box.pack(fill="x", padx=8, pady=6)

        self.mode = tk.StringVar(value="set")
        ttk.Radiobutton(box, text="Set to", variable=self.mode, value="set").pack(side="left")
        ttk.Radiobutton(box, text="Add", variable=self.mode, value="add").pack(side="left", padx=(10, 0))
        ttk.Radiobutton(box, text="Subtract", variable=self.mode, value="sub").pack(side="left", padx=(10, 0))

        self.value_var = tk.IntVar(value=0)
        ttk.Spinbox(box, from_=0, to=999999, textvariable=self.value_var, width=8).pack(side="left", padx=12)

        ttk.Button(box, text="Apply to selected", command=self.apply).pack(side="left")

        # Status bar
        self.status = tk.StringVar(value="Starting…")
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=4).pack(fill="x", side="bottom")

    # ------------------------------------------------------------- Library
    def load_library(self):
        if win32com is None:
            messagebox.showerror("Missing dependency",
                                 "pywin32 is not installed.\n\nRun:  pip install pywin32")
            self.status.set("pywin32 missing")
            return
        try:
            self.status.set("Connecting to iTunes…")
            self.update_idletasks()
            self.itunes = win32com.client.Dispatch("iTunes.Application")
            tracks = self.itunes.LibraryPlaylist.Tracks
            total = tracks.Count
        except Exception as e:
            messagebox.showerror("iTunes error", f"Couldn't connect to iTunes:\n{e}")
            self.status.set("Not connected")
            return

        self.data = []
        for i in range(1, total + 1):
            try:
                t = tracks.Item(i)
                self.data.append({
                    "obj": t,
                    "name": t.Name or "",
                    "artist": t.Artist or "",
                    "album": t.Album or "",
                    "plays": int(t.PlayedCount),
                })
            except Exception:
                continue
            if i % 100 == 0:
                self.status.set(f"Loading tracks… {i}/{total}")
                self.update_idletasks()

        self.refresh_tree()
        self.status.set(f"Loaded {len(self.data)} tracks")

    def refresh_tree(self):
        q = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for idx, d in enumerate(self.data):
            if q and q not in f"{d['name']} {d['artist']} {d['album']}".lower():
                continue
            self.tree.insert("", "end", iid=str(idx),
                             values=(d["name"], d["artist"], d["album"], d["plays"]))
        self.update_selection_label()

    def sort_by(self, col):
        reverse = self.sort_state.get(col, False)
        rows = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        if col == "plays":
            rows.sort(key=lambda r: int(r[0]), reverse=reverse)
        else:
            rows.sort(key=lambda r: r[0].lower(), reverse=reverse)
        for pos, (_, k) in enumerate(rows):
            self.tree.move(k, "", pos)
        self.sort_state[col] = not reverse

    def select_all(self, event=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    def update_selection_label(self):
        self.sel_label.config(text=f"{len(self.tree.selection())} selected")

    # --------------------------------------------------------------- Apply
    def apply(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Select one or more songs first.")
            return
        try:
            val = int(self.value_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Invalid value", "Enter a whole number.")
            return
        if val < 0:
            messagebox.showerror("Invalid value", "Value must be 0 or higher.")
            return

        mode = self.mode.get()
        verb = {"set": f"set to {val}", "add": f"increase by {val}", "sub": f"decrease by {val}"}[mode]
        if not messagebox.askyesno("Confirm", f"Play count will {verb} for {len(sel)} song(s). Continue?"):
            return

        ok, failed = 0, []
        for iid in sel:
            d = self.data[int(iid)]
            if mode == "set":
                new = val
            elif mode == "add":
                new = d["plays"] + val
            else:
                new = max(0, d["plays"] - val)
            try:
                d["obj"].PlayedCount = new
                d["plays"] = new
                self.tree.set(iid, "plays", new)
                ok += 1
            except Exception as e:
                failed.append(f"{d['name']}: {e}")

        msg = f"Updated {ok} song(s)."
        if failed:
            msg += f"\n\n{len(failed)} failed (e.g. streaming/Apple Music tracks can't be edited):\n" + "\n".join(failed[:5])
            messagebox.showwarning("Done with errors", msg)
        else:
            messagebox.showinfo("Done", msg)
        self.status.set(msg.split("\n")[0])


if __name__ == "__main__":
    PlayCountEditor().mainloop()

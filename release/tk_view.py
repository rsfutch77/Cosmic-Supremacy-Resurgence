"""
tk_view.py , the launcher's tkinter window
==========================================
Every widget the launcher draws, and none of its decisions. `launcher.Launcher`
decides what is shown and calls the methods here to show it; the widgets hand
what the player presses back to the Launcher's own methods.

Everything here runs on the Tk thread. The prompts are modal and return the
player's answer, and Tk keeps running its timers while one is open, so the
Launcher's queue and its once-a-second watch carry on underneath a dialog.
"""
import tkinter as tk
from tkinter import filedialog, messagebox

import launcher as L
from launcher import (ACCENT, BAD, BG, BTN, BTN_HI, DIM, EDGE, FAINT, PAD_X,
                      PAD_Y, PAGE_PACK, PAGE_WIDTH, PANEL, PANEL_PAD, TEXT, WARN)


class TkView:
    def __init__(self, root):
        self.root = root
        self.app = None
        self.log_visible = False
        # The playable mode buttons, which fail() disables.
        self.buttons = []
        # The table's cells by (row, column), thrown away on every repaint.
        self._cells = {}
        self._cell_widgets = []

    # ── lifecycle ──
    def build(self, app):
        """Make the window for `app`, whose methods the widgets call."""
        self.app = app
        root, cfg = self.root, app.cfg
        root.title(cfg["product"])
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", app.on_close)
        pad = {"padx": PAD_X}

        # One line, from the manifest, so the header, the title bar and every
        # dialog title are the same string rather than three things to keep in
        # step. 20pt rather than 22: the full name is 28 characters and the
        # window sizes itself to its widest child.
        tk.Label(root, text=cfg["product"], bg=BG, fg=ACCENT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(22, 0), **pad)
        # The build, not the manifest's version: a build made with build.ps1's
        # -Version switch carries a name the manifest does not hold, and this
        # line is where a player reads back what a galaxy is about to refuse.
        #
        # The player code goes on the same line once this install has a
        # Firebase sign-in, because this is the line a player is already
        # asked to read back: see `player_code`.
        self.build_label = tk.Label(root, text=f"v{L.build_id()}",
                                    bg=BG, fg=DIM, font=("Segoe UI", 10))
        self.build_label.pack(anchor="w", **pad)

        # The window's body, which is one page at a time. The home page holds
        # the mode cards; Multiplayer swaps in the Galaxies page and Back swaps
        # it out again.
        self.pages = tk.Frame(root, bg=BG)
        self.pages.pack(**PAGE_PACK)
        # One width for both pages, so that navigating does not resize a window
        # that cannot be resized by hand. Without it the window would widen
        # when the table appears and narrow again on the way back.
        tk.Frame(self.pages, bg=BG, width=PAGE_WIDTH, height=1).pack()
        self.home_page = tk.Frame(self.pages, bg=BG)
        self.galaxies_page = tk.Frame(self.pages, bg=BG)
        self.home_page.pack(fill="both", expand=True)

        for mode in app.modes:
            card = tk.Frame(self.home_page, bg=PANEL, highlightbackground=EDGE,
                            highlightthickness=1)
            card.pack(fill="x", pady=5)
            ready = L.is_playable(mode)
            btn = tk.Button(card, text=mode["title"], width=18,
                            bg=BTN if ready else PANEL,
                            fg="#ffffff" if ready else FAINT,
                            activebackground=BTN_HI, activeforeground="#ffffff",
                            relief="flat", bd=0,
                            font=("Segoe UI", 10, "bold"),
                            cursor="hand2" if ready else "",
                            state="normal" if ready else "disabled",
                            disabledforeground=FAINT,
                            command=lambda m=mode: app.on_play(m))
            btn.pack(side="left", padx=14, pady=14)
            if ready:
                btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=BTN_HI))
                btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=BTN))
                # Only playable buttons go in the list fail() disables , a
                # coming-soon button is already disabled and must stay that way.
                self.buttons.append(btn)
            else:
                # Packed before the blurb so the right-hand side claims its
                # width first and the blurb wraps around it.
                tk.Label(card, text=" COMING SOON ", bg=EDGE, fg=TEXT,
                         font=("Segoe UI", 7, "bold")).pack(side="right",
                                                            padx=(0, 14))
            tk.Label(card, text=mode["blurb"], bg=PANEL,
                     fg=DIM if ready else FAINT, justify="left",
                     wraplength=340, font=("Segoe UI", 9)).pack(
                         side="left", padx=(0, 14))

        self._build_galaxies(self.galaxies_page)

        self.ctl_frame = tk.Frame(root, bg=PANEL,
                                  highlightbackground=EDGE, highlightthickness=1)
        inner = tk.Frame(self.ctl_frame, bg=PANEL)
        inner.pack(fill="x", padx=12, pady=10)
        self.ctl_buttons = {}
        actions = {"load": app.on_load, "save": app.on_save,
                   "turn": app.on_next_turn}
        for key, text in L.CONTROLS:
            # Packed right-to-left, so they READ "Next Turn  Save  Load".
            b = tk.Button(inner, text=text, bg=BTN, fg="#ffffff",
                          activebackground=BTN_HI, activeforeground="#ffffff",
                          relief="flat", bd=0, cursor="hand2", width=10,
                          font=("Segoe UI", 9, "bold"), command=actions[key])
            b.pack(side="right", padx=(6, 0))
            self.ctl_buttons[key] = b
        self.turn_label = tk.Label(inner, text="turn ,", bg=PANEL, fg=TEXT,
                                   font=("Segoe UI", 9, "bold"), width=34,
                                   anchor="w")
        self.turn_label.pack(side="left", fill="x")

        tk.Label(root,
                 text="Keep this window open while you play , it is the game server.",
                 bg=BG, fg=FAINT, font=("Segoe UI", 8)).pack(anchor="w",
                                                             pady=(6, 0), **pad)

        ident = tk.Frame(root, bg=BG)
        ident.pack(fill="x", pady=(10, 0), **pad)
        tk.Label(ident, text="Playing as", bg=BG, fg=FAINT,
                 font=("Segoe UI", 8)).pack(side="left")
        self.name_label = tk.Label(ident, text="", bg=BG, fg=TEXT,
                                   font=("Segoe UI", 9, "bold"))
        self.name_label.pack(side="left", padx=(6, 0))
        self.name_btn = tk.Label(ident, text="set name", bg=BG, fg=FAINT,
                                 font=("Segoe UI", 8, "underline"),
                                 cursor="hand2")
        self.name_btn.pack(side="left", padx=(8, 0))
        self.name_btn.bind("<Button-1>", lambda e: app.on_change_name())

        status = tk.Frame(root, bg=BG)
        status.pack(fill="x", pady=(12, 0), **pad)
        self.dot = tk.Label(status, text="●", bg=BG, fg=WARN,
                            font=("Segoe UI", 11))
        self.dot.pack(side="left")
        self.status = tk.Label(status, text="starting…", bg=BG, fg=DIM,
                               font=("Segoe UI", 9))
        self.status.pack(side="left", padx=(6, 0))
        # The capture indicator, beside the server's dot and built the same
        # way. Packed only while a turn loop is running: outside multiplayer
        # there is nothing being captured and an idle second dot reads as a
        # second thing that could be wrong.
        self.cap_dot = tk.Label(status, text="●", bg=BG, fg=FAINT,
                                font=("Segoe UI", 11))
        self.cap_status = tk.Label(status, text="", bg=BG, fg=DIM,
                                   font=("Segoe UI", 9))
        self.log_btn = tk.Label(status, text="show log", bg=BG, fg=FAINT,
                                font=("Segoe UI", 8, "underline"), cursor="hand2")
        self.log_btn.pack(side="right")
        self.log_btn.bind("<Button-1>", lambda e: self.toggle_log())
        self.send_log_btn = tk.Label(status, text="send log", bg=BG, fg=FAINT,
                                     font=("Segoe UI", 8, "underline"),
                                     cursor="hand2")
        self.send_log_btn.pack(side="right", padx=(0, 10))
        self.send_log_btn.bind("<Button-1>", lambda e: app.offer_log())
        # Packed once this install has sent a log: see `show_log_refs_link`.
        self.log_refs_btn = tk.Label(status, text="log history", bg=BG,
                                     fg=FAINT, font=("Segoe UI", 8, "underline"),
                                     cursor="hand2")
        self.log_refs_btn.bind("<Button-1>", lambda e: app.show_log_refs())

        self.log_frame = tk.Frame(root, bg=BG)
        self.log = tk.Text(self.log_frame, height=11, bg="#02060f", fg=DIM,
                           insertbackground=TEXT, relief="flat",
                           font=("Consolas", 8), wrap="none",
                           highlightbackground=EDGE, highlightthickness=1)
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        tk.Frame(root, bg=BG, height=PAD_Y).pack()

    def _build_galaxies(self, parent):
        """The page the Multiplayer button opens: a sortable table of galaxies.

        The header, the status line and the column headings are built once and
        never thrown away, so Back is on screen while a listing is in flight
        and after one has failed. A repaint replaces the cells under the
        headings and touches nothing else.
        """
        app = self.app
        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x")
        back = tk.Button(head, text="‹ Back", bg=BTN, fg="#ffffff",
                         activebackground=BTN_HI, activeforeground="#ffffff",
                         relief="flat", bd=0, cursor="hand2", width=8,
                         font=("Segoe UI", 9, "bold"), command=app.show_home)
        back.pack(side="left")
        back.bind("<Enter>", lambda e: back.configure(bg=BTN_HI))
        back.bind("<Leave>", lambda e: back.configure(bg=BTN))
        tk.Label(head, text="Galaxies", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=(12, 0))
        self.games_refresh = tk.Label(head, text="refresh", bg=BG, fg=FAINT,
                                      font=("Segoe UI", 8, "underline"),
                                      cursor="hand2")
        self.games_refresh.pack(side="right")
        self.games_refresh.bind("<Button-1>", lambda e: app.refresh_games())

        self.games_status = tk.Label(parent, text="", bg=BG, fg=DIM,
                                     anchor="w", justify="left",
                                     wraplength=PAGE_WIDTH,
                                     font=("Segoe UI", 8))
        self.games_status.pack(fill="x", pady=(8, 0))

        panel = tk.Frame(parent, bg=PANEL, highlightbackground=EDGE,
                         highlightthickness=1)
        panel.pack(fill="both", expand=True, pady=(6, 0))
        self.games_table = tk.Frame(panel, bg=PANEL)
        self.games_table.pack(fill="both", expand=True, padx=PANEL_PAD,
                              pady=PANEL_PAD)
        for i, col in enumerate(L.GALAXY_COLUMNS):
            # Only the name stretches. The rest are as wide as their heading
            # needs, so the numbers stay in a column the eye can run down.
            self.games_table.grid_columnconfigure(
                i, minsize=col.width, weight=1 if col.key == "name" else 0)
        self.games_table.grid_columnconfigure(len(L.GALAXY_COLUMNS),
                                              minsize=L.ACTION_WIDTH)
        self.games_heads = {}
        for i, col in enumerate(L.GALAXY_COLUMNS):
            lab = tk.Label(self.games_table, bg=PANEL, fg=DIM, anchor="w",
                           cursor="hand2", font=("Segoe UI", 8, "bold"))
            lab.grid(row=0, column=i, sticky="w", padx=(0, 8), pady=(0, 6))
            lab.bind("<Button-1>", lambda e, k=col.key: app.sort_by(k))
            self.games_heads[col.key] = lab
        self.show_sort(app.sort_key, app.sort_desc)

        self.games_hint = tk.Label(parent, bg=BG, fg=WARN, anchor="w",
                                   justify="left", wraplength=PAGE_WIDTH,
                                   font=("Segoe UI", 8))

    def after(self, ms: int, fn) -> None:
        self.root.after(ms, fn)

    def close(self) -> None:
        self.root.destroy()

    def set_icon(self, path: str) -> None:
        # default= applies to this window and every dialog it spawns, so the
        # message boxes carry the icon too.
        self.root.iconbitmap(default=path)

    # ── who you are, and the build ──
    def show_identity(self, player) -> None:
        self.name_label.configure(text=player or "nobody yet",
                                  fg=TEXT if player else FAINT)
        self.name_btn.configure(text="change" if player else "set name")

    def show_build(self, text: str) -> None:
        if self.build_label.cget("text") != text:
            self.build_label.configure(text=text)

    # ── status line ──
    def set_status(self, text: str, colour: str) -> None:
        self.status.configure(text=text)
        self.dot.configure(fg=colour)

    def show_capture(self, text: str, colour: str) -> None:
        """Paint the capture indicator, or take it away for no text.

        Packed and unpacked rather than blanked, so that a launcher with no
        turn loop running shows one dot and one status, which is what it had
        before any of this.
        """
        if not text:
            if self.cap_dot.winfo_ismapped():
                self.cap_status.pack_forget()
                self.cap_dot.pack_forget()
            return
        if not self.cap_dot.winfo_ismapped():
            self.cap_dot.pack(side="left", padx=(16, 0))
            self.cap_status.pack(side="left", padx=(6, 0))
        if self.cap_status.cget("text") != text:
            self.cap_status.configure(text=text)
        if self.cap_dot.cget("fg") != colour:
            self.cap_dot.configure(fg=colour)

    def disable_modes(self) -> None:
        for btn in self.buttons:
            btn.configure(state="disabled", bg=FAINT, cursor="")
            btn.unbind("<Enter>")
            btn.unbind("<Leave>")

    # ── game controls ──
    def show_controls(self, show: bool) -> None:
        if show and not self.ctl_frame.winfo_ismapped():
            self.ctl_frame.pack(fill="x", padx=PAD_X, pady=(0, PAD_Y))
        elif not show and self.ctl_frame.winfo_ismapped():
            self.ctl_frame.pack_forget()

    def set_control(self, key: str, enabled=None, text=None) -> None:
        """Enable or disable one control, or rename it. Repaints only a change."""
        btn = self.ctl_buttons.get(key)
        if btn is None:
            return
        if enabled is not None:
            want = "normal" if enabled else "disabled"
            if btn.cget("state") != want:
                btn.configure(state=want, bg=BTN if enabled else FAINT,
                              cursor="hand2" if enabled else "")
        if text is not None and btn.cget("text") != text:
            btn.configure(text=text)

    def set_turn_label(self, text: str) -> None:
        if self.turn_label.cget("text") != text:
            self.turn_label.configure(text=text)

    # ── pages ──
    def show_page(self, name: str) -> None:
        showing = self.home_page if name == "home" else self.galaxies_page
        hidden = self.galaxies_page if name == "home" else self.home_page
        hidden.pack_forget()
        showing.pack(fill="both", expand=True)

    # ── the galaxy table ──
    def show_sort(self, key: str, desc: bool) -> None:
        """Mark the column being sorted on, and which way round it is."""
        for col in L.GALAXY_COLUMNS:
            lab = self.games_heads.get(col.key)
            if lab is None:
                continue
            here = col.key == key
            mark = (" ▾" if desc else " ▴") if here else ""
            lab.configure(text=col.heading + mark, fg=ACCENT if here else DIM)

    def set_games_status(self, text: str, colour: str) -> None:
        self.games_status.configure(text=text, fg=colour)

    def draw_galaxies(self, rows, note: str = "") -> None:
        """Replace the table's cells. `rows` is a list of `launcher.GalaxyRow`,
        or None while the first listing is in flight, when `note` is shown in
        the table where the rows will be."""
        for widget in self._cell_widgets:
            widget.destroy()
        self._cells, self._cell_widgets = {}, []
        if rows is None:
            waiting = tk.Label(self.games_table, text=note, bg=PANEL, fg=DIM,
                               anchor="w", font=("Segoe UI", 9))
            waiting.grid(row=1, column=0, columnspan=len(L.GALAXY_COLUMNS) + 1,
                         sticky="w", pady=6)
            self._cell_widgets.append(waiting)
            return
        for r, row in enumerate(rows, start=1):
            for i, (text, colour) in enumerate(row.cells):
                cell = tk.Label(self.games_table, text=text, bg=PANEL,
                                fg=colour, anchor="w", font=("Segoe UI", 9))
                cell.grid(row=r, column=i, sticky="w", padx=(0, 8), pady=3)
                self._cells[r - 1, i] = cell
                self._cell_widgets.append(cell)
            if row.action is None:
                continue
            btn = tk.Button(self.games_table, text=row.action, bg=BTN,
                            fg="#ffffff", activebackground=BTN_HI,
                            activeforeground="#ffffff", relief="flat", bd=0,
                            cursor="hand2", width=6,
                            font=("Segoe UI", 9, "bold"), command=row.on_action)
            btn.grid(row=r, column=len(row.cells), sticky="e", pady=3)
            self._cell_widgets.append(btn)

    def set_cell(self, row: int, column: int, text: str, colour: str) -> None:
        """Rewrite one cell in place, leaving the order alone."""
        cell = self._cells.get((row, column))
        if cell is None or not cell.winfo_exists():
            return
        if cell.cget("text") != text:
            cell.configure(text=text)
        if cell.cget("fg") != colour:
            cell.configure(fg=colour)

    def set_hint(self, text: str) -> None:
        """The line under the table, or none. Repaints only a change."""
        if text:
            if self.games_hint.cget("text") != text:
                self.games_hint.configure(text=text)
            if not self.games_hint.winfo_ismapped():
                self.games_hint.pack(fill="x", pady=(8, 0))
        elif self.games_hint.winfo_ismapped():
            self.games_hint.pack_forget()

    # ── the log ──
    def append_log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill="both", expand=True, padx=PAD_X,
                                pady=(8, 0))
            self.log_btn.configure(text="hide log")
        else:
            self.log_frame.pack_forget()
            self.log_btn.configure(text="show log")

    def show_log_refs_link(self) -> None:
        """Pack the log history link, once."""
        if self.log_refs_btn.winfo_manager():
            return
        self.log_refs_btn.pack(side="right", padx=(0, 10),
                               before=self.send_log_btn)

    # ── prompts ──
    def warn(self, msg: str) -> None:
        messagebox.showwarning(self.app.cfg["product"], msg)

    def error(self, msg: str) -> None:
        messagebox.showerror(self.app.cfg["product"], msg)

    def ask_yes_no(self, msg: str) -> bool:
        return bool(messagebox.askyesno(self.app.cfg["product"], msg))

    def ask_ok_cancel(self, title: str, msg: str) -> bool:
        return bool(messagebox.askokcancel(title, msg))

    def ask_open_file(self, title: str, initialdir: str, filetypes) -> str:
        return filedialog.askopenfilename(title=title, initialdir=initialdir,
                                          filetypes=filetypes) or ""

    def _modal(self, win) -> None:
        win.update_idletasks()
        # grab_set after the window is mapped: grabbing a window that is not yet
        # on screen fails on Windows and leaves the dialog non-modal.
        try:
            win.grab_set()
        except tk.TclError:
            pass
        self.root.wait_window(win)

    def ask_name(self, text: str, current: str, rules: str, validate):
        """Ask for a name until `validate` takes one or the player cancels.

        `validate(raw)` answers (name, None) or (None, why not), and a refusal
        is shown beside the field the player is typing in. Returns the name, or
        None if cancelled.
        """
        win = tk.Toplevel(self.root)
        win.title("Your name")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)

        tk.Label(win, text=text, bg=BG, fg=DIM, justify="left", wraplength=360,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=(18, 10))

        var = tk.StringVar(value=current)
        entry = tk.Entry(win, textvariable=var, bg=PANEL, fg=TEXT, width=22,
                         insertbackground=TEXT, relief="flat",
                         highlightbackground=EDGE, highlightthickness=1,
                         font=("Segoe UI", 12))
        entry.pack(anchor="w", padx=20, ipady=3)
        tk.Label(win, text=rules, bg=BG, fg=FAINT, justify="left",
                 wraplength=360, font=("Segoe UI", 8)).pack(anchor="w", padx=20,
                                                            pady=(6, 0))
        why = tk.Label(win, text="", bg=BG, fg=BAD, justify="left",
                       wraplength=360, font=("Segoe UI", 8))
        why.pack(anchor="w", padx=20, pady=(4, 0))

        picked = {}

        def accept(_evt=None):
            name, problem = validate(var.get())
            if problem:
                why.configure(text=problem)
                return
            picked["name"] = name
            win.destroy()

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=20, pady=(14, 18))
        for label, cmd in (("Cancel", win.destroy), ("Save", accept)):
            tk.Button(row, text=label, bg=BTN, fg="#ffffff",
                      activebackground=BTN_HI, activeforeground="#ffffff",
                      relief="flat", bd=0, cursor="hand2", width=9,
                      font=("Segoe UI", 9, "bold"), command=cmd).pack(
                          side="right", padx=(6, 0))
        entry.bind("<Return>", accept)
        win.bind("<Escape>", lambda e: win.destroy())
        entry.focus_set()
        self._modal(win)
        return picked.get("name")

    def choose(self, title: str, body: str, choices):
        """A modal box with a title, a body and a row of buttons.

        `choices` is (label, value, primary) per button, left to right, and
        the value of the one pressed comes back. Escape and the window's own
        close answer None.
        """
        win = tk.Toplevel(self.root)
        win.title(self.app.cfg["product"])
        win.configure(bg=BG)
        win.transient(self.root)
        tk.Label(win, text=title, bg=BG, fg=WARN,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=20,
                                                     pady=(18, 6))
        tk.Label(win, text=body, bg=BG, fg=TEXT,
                 justify="left", anchor="w", wraplength=460,
                 font=("Segoe UI", 9)).pack(anchor="w", fill="x", padx=20)

        chosen = {}

        def choose(what):
            chosen["what"] = what
            win.destroy()

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=20, pady=(14, 18))
        for label, what, primary in choices:
            bg, hi = (BTN, BTN_HI) if primary else (PANEL, EDGE)
            tk.Button(row, text=label, bg=bg, fg="#ffffff" if primary
                      else TEXT, activebackground=hi,
                      activeforeground="#ffffff", relief="flat", bd=0,
                      cursor="hand2", width=12, font=("Segoe UI", 9, "bold"),
                      command=lambda w=what: choose(w)).pack(side="left",
                                                             padx=(0, 6))
        win.bind("<Escape>", lambda e: win.destroy())
        self._modal(win)
        return chosen.get("what")

    def beta_notice(self, heading: str, lead: str, body: str) -> bool:
        """The notice with Join under it. True only for Join.

        The text is scrolled rather than shortened, and read-only, so what a
        player agrees to is the whole of it and exactly it.
        """
        win = tk.Toplevel(self.root)
        win.title(self.app.cfg["product"])
        win.configure(bg=BG)
        win.transient(self.root)

        tk.Label(win, text=heading, bg=BG, fg=ACCENT,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=20,
                                                     pady=(18, 0))
        tk.Label(win, text=lead, bg=BG, fg=DIM, font=("Segoe UI", 9),
                 justify="left", wraplength=560).pack(anchor="w", padx=20,
                                                      pady=(2, 10))

        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, padx=20)
        bar = tk.Scrollbar(frame, orient="vertical")
        text = tk.Text(frame, width=78, height=22, bg=PANEL, fg=TEXT,
                       relief="flat", wrap="word", padx=10, pady=8,
                       highlightbackground=EDGE, highlightthickness=1,
                       font=("Consolas", 9), yscrollcommand=bar.set)
        bar.configure(command=text.yview)
        bar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", body)
        # After the insert, or the player can edit the rules they are agreeing
        # to before agreeing to them.
        text.configure(state="disabled")

        joined = {}

        def accept():
            joined["yes"] = True
            win.destroy()

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=20, pady=(14, 18))
        tk.Button(row, text="Join", bg=BTN, fg="#ffffff",
                  activebackground=BTN_HI, activeforeground="#ffffff",
                  relief="flat", bd=0, cursor="hand2", width=12,
                  font=("Segoe UI", 9, "bold"), command=accept).pack(
                      side="right", padx=(6, 0))
        tk.Button(row, text="Not yet", bg=PANEL, fg=TEXT,
                  activebackground=EDGE, activeforeground="#ffffff",
                  relief="flat", bd=0, cursor="hand2", width=12,
                  font=("Segoe UI", 9, "bold"), command=win.destroy).pack(
                      side="right", padx=(6, 0))

        win.bind("<Escape>", lambda e: win.destroy())
        self._modal(win)
        return bool(joined.get("yes"))

    def log_refs(self, title: str, body: str, refs) -> None:
        """A modal box like `choose` with one row per log reference.

        `refs` is (reference, when sent) per row. Each reference is in a
        read-only entry, so it can also be selected by hand, with a Copy button
        beside it. Close, Escape and the window's own close dismiss it.
        """
        win = tk.Toplevel(self.root)
        win.title(self.app.cfg["product"])
        win.configure(bg=BG)
        win.transient(self.root)
        tk.Label(win, text=title, bg=BG, fg=WARN,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=20,
                                                     pady=(18, 6))
        tk.Label(win, text=body, bg=BG, fg=TEXT,
                 justify="left", anchor="w", wraplength=460,
                 font=("Segoe UI", 9)).pack(anchor="w", fill="x", padx=20)
        if refs:
            table = tk.Frame(win, bg=BG)
            table.pack(anchor="w", fill="x", padx=20, pady=(12, 0))
        for i, (ref_id, when) in enumerate(refs):
            ref = tk.StringVar(win, value=ref_id)
            tk.Entry(table, textvariable=ref, state="readonly", width=34,
                     readonlybackground=PANEL, fg=TEXT, relief="flat",
                     highlightbackground=EDGE, highlightthickness=1,
                     font=("Consolas", 9)).grid(row=i, column=0, sticky="w",
                                                pady=2, ipady=3)
            tk.Label(table, text=when, bg=BG,
                     fg=DIM, font=("Segoe UI", 8)).grid(row=i, column=1,
                                                        sticky="w", padx=8)
            btn = tk.Button(table, text=L.LOG_COPY, bg=PANEL, fg=TEXT,
                            activebackground=EDGE, activeforeground="#ffffff",
                            relief="flat", bd=0, cursor="hand2", width=8,
                            font=("Segoe UI", 9, "bold"))
            btn.configure(command=lambda b=btn, r=ref_id:
                          L.copy_pressed(self.root, b, r))
            btn.grid(row=i, column=2, sticky="w")

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=20, pady=(14, 18))
        tk.Button(row, text="Close", bg=BTN, fg="#ffffff",
                  activebackground=BTN_HI, activeforeground="#ffffff",
                  relief="flat", bd=0, cursor="hand2", width=12,
                  font=("Segoe UI", 9, "bold"),
                  command=win.destroy).pack(side="left")
        win.bind("<Escape>", lambda e: win.destroy())
        self._modal(win)

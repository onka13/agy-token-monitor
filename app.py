"""Floating macOS corner HUD & history viewer for Antigravity token usage."""

from __future__ import annotations

import json
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from tracker import SessionSummary, TokenHistoryStore, TurnSummary


POLL_INTERVAL_MS = 2000
STATE_PATH = Path(__file__).resolve().parent / "data" / "window_state.json"

BG = "#14161b"
BG_CARD = "#1d2028"
BG_HOVER = "#272b36"
FG = "#e8eaf0"
FG_MUTED = "#8b92a5"
ACCENT = "#6aa9ff"
GREEN = "#5fd38d"
AMBER = "#f5b851"
PURPLE = "#b68cff"
RED = "#ff6b6b"
BORDER = "#2c313d"


def fmt_num(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def fmt_short(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}K"
    return fmt_num(n)


def fmt_time(iso_ts: str) -> str:
    if not iso_ts:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%H:%M:%S")
    except ValueError:
        return iso_ts[:19]


class DarkButton(tk.Label):
    """macOS-safe dark flat button (avoids Aqua white native button background)."""

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        command: object,
        bg: str = BG_HOVER,
        hover_bg: str = "#333846",
        fg: str = FG,
        padx: int = 8,
        pady: int = 3,
        font: tuple[object, ...] = ("SF Pro Text", 10),
    ) -> None:
        super().__init__(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            padx=padx,
            pady=pady,
            font=font,
            cursor="pointinghand",
        )
        self._bg = bg
        self._hover_bg = hover_bg
        self._command = command
        self.bind("<Enter>", lambda _e: self.configure(bg=self._hover_bg))
        self.bind("<Leave>", lambda _e: self.configure(bg=self._bg))
        self.bind("<Button-1>", self._on_click)

    def _on_click(self, _event: tk.Event) -> None:
        if callable(self._command):
            self._command()


class HistoryWindow(tk.Toplevel):
    """Detailed per-turn and per-session history window."""

    def __init__(self, parent: TokenMonitorApp, store: TokenHistoryStore) -> None:
        super().__init__(parent)
        self.app = parent
        self.store = store
        self.title("Antigravity Token Geçmişi & İstatistikler")
        self.geometry("980x560")
        self.configure(bg=BG)

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=BG_CARD,
            foreground=FG,
            fieldbackground=BG_CARD,
            borderwidth=0,
            rowheight=24,
        )
        style.configure(
            "Treeview.Heading",
            background=BG_HOVER,
            foreground=FG,
            borderwidth=0,
            font=("SF Pro Text", 11, "bold"),
        )
        style.map("Treeview", background=[("selected", "#2e4066")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG_CARD, foreground=FG, padding=(12, 6))
        style.map("TNotebook.Tab", background=[("selected", BG_HOVER)])

        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 6))

        self.global_lbl = tk.Label(
            top,
            text="",
            bg=BG,
            fg=FG_MUTED,
            font=("SF Mono", 11),
            anchor="w",
        )
        self.global_lbl.pack(side="left", fill="x", expand=True)

        DarkButton(
            top,
            text="CSV Dışa Aktar",
            command=self._export_csv,
            bg=BG_HOVER,
            fg=FG,
            padx=10,
            pady=4,
            font=("SF Pro Text", 11),
        ).pack(side="right", padx=(6, 0))

        DarkButton(
            top,
            text="Yenile",
            command=self.refresh,
            bg=BG_HOVER,
            fg=FG,
            padx=10,
            pady=4,
            font=("SF Pro Text", 11),
        ).pack(side="right")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # Tab 1: Turns of selected session
        turns_frame = tk.Frame(nb, bg=BG)
        nb.add(turns_frame, text="Seçili Oturum Turları")

        turn_cols = (
            "turn",
            "time",
            "duration",
            "model",
            "steps",
            "tools",
            "errors",
            "input",
            "cache",
            "output",
            "total",
            "prompt",
        )
        self.turns_tree = ttk.Treeview(turns_frame, columns=turn_cols, show="headings")
        headers = {
            "turn": ("Tur", 45, "center"),
            "time": ("Saat", 72, "center"),
            "duration": ("Süre", 58, "e"),
            "model": ("Model", 145, "w"),
            "steps": ("Adım", 48, "e"),
            "tools": ("Tool", 48, "e"),
            "errors": ("Hata", 45, "e"),
            "input": ("Input", 80, "e"),
            "cache": ("Cache", 85, "e"),
            "output": ("Output", 75, "e"),
            "total": ("Toplam", 90, "e"),
            "prompt": ("İstek Özeti", 240, "w"),
        }
        for col, (title, width, anchor) in headers.items():
            self.turns_tree.heading(col, text=title)
            self.turns_tree.column(col, width=width, anchor=anchor)

        t_scroll = ttk.Scrollbar(turns_frame, orient="vertical", command=self.turns_tree.yview)
        self.turns_tree.configure(yscrollcommand=t_scroll.set)
        self.turns_tree.pack(side="left", fill="both", expand=True)
        t_scroll.pack(side="right", fill="y")

        # Tab 2: All sessions
        sess_frame = tk.Frame(nb, bg=BG)
        nb.add(sess_frame, text="Tüm Oturumlar")

        sess_cols = (
            "id",
            "name",
            "last_seen",
            "model",
            "turns",
            "steps",
            "tools",
            "input",
            "cache",
            "output",
            "total",
        )
        self.sess_tree = ttk.Treeview(sess_frame, columns=sess_cols, show="headings")
        s_headers = {
            "id": ("Conv ID", 95, "w"),
            "name": ("Ad / Son İstek", 220, "w"),
            "last_seen": ("Son Aktivite", 80, "center"),
            "model": ("Son Model", 140, "w"),
            "turns": ("Tur", 45, "e"),
            "steps": ("Adım", 50, "e"),
            "tools": ("Tool", 50, "e"),
            "input": ("Input", 80, "e"),
            "cache": ("Cache", 85, "e"),
            "output": ("Output", 75, "e"),
            "total": ("Toplam", 95, "e"),
        }
        for col, (title, width, anchor) in s_headers.items():
            self.sess_tree.heading(col, text=title)
            self.sess_tree.column(col, width=width, anchor=anchor)

        s_scroll = ttk.Scrollbar(sess_frame, orient="vertical", command=self.sess_tree.yview)
        self.sess_tree.configure(yscrollcommand=s_scroll.set)
        self.sess_tree.pack(side="left", fill="both", expand=True)
        s_scroll.pack(side="right", fill="y")

        self.sess_tree.bind("<Double-1>", self._on_session_double_click)

    def _on_session_double_click(self, _event: tk.Event) -> None:
        sel = self.sess_tree.selection()
        if not sel:
            return
        conv_id = self.sess_tree.item(sel[0], "tags")[0]
        self.app.select_session(conv_id, auto_follow=False)
        self.refresh()

    def _export_csv(self) -> None:
        default_name = f"agy_turns_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        out = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV", "*.csv")],
        )
        if not out:
            return
        count = self.store.export_turns_csv(Path(out))
        messagebox.showinfo("Dışa Aktarıldı", f"{count} tur kaydı aktarıldı:\n{out}", parent=self)

    def refresh(self) -> None:
        totals = self.store.get_global_totals()
        self.global_lbl.configure(
            text=(
                f"Tüm Oturumlar: {totals.get('session_count', 0)}  |  "
                f"Tur: {fmt_num(totals.get('turns', 0))}  |  "
                f"In: {fmt_short(totals.get('input_tokens', 0))}  "
                f"Cache: {fmt_short(totals.get('cache_read_tokens', 0))}  "
                f"Out: {fmt_short(totals.get('output_tokens', 0))}  |  "
                f"Toplam: {fmt_num(totals.get('total_tokens', 0))}"
            )
        )

        # Populate turns for active session
        for item in self.turns_tree.get_children():
            self.turns_tree.delete(item)

        if self.app.selected_conv_id:
            sess = self.store.get_session(self.app.selected_conv_id)
            if sess:
                for t in reversed(sess.turns):
                    self.turns_tree.insert(
                        "",
                        "end",
                        values=(
                            f"#{t.turn_index}",
                            fmt_time(t.started_at),
                            f"{t.duration_seconds:.1f}s",
                            t.model or "-",
                            t.step_count,
                            t.tool_call_count,
                            t.error_count,
                            fmt_num(t.input_tokens),
                            fmt_num(t.cache_read_tokens),
                            fmt_num(t.output_tokens),
                            fmt_num(t.total_tokens),
                            t.prompt_preview,
                        ),
                    )

        # Populate all sessions
        for item in self.sess_tree.get_children():
            self.sess_tree.delete(item)

        for s in self.store.list_sessions():
            label = s.alias or s.last_prompt or s.first_prompt or "(İsimsiz)"
            self.sess_tree.insert(
                "",
                "end",
                tags=(s.conversation_id,),
                values=(
                    s.conversation_id[:8],
                    label,
                    fmt_time(s.last_seen_at),
                    s.last_model or "-",
                    s.total_turns,
                    s.total_steps,
                    s.total_tool_calls,
                    fmt_short(s.total_input_tokens),
                    fmt_short(s.total_cache_read_tokens),
                    fmt_short(s.total_output_tokens),
                    fmt_num(s.total_tokens),
                ),
            )


class TokenMonitorApp(tk.Tk):
    """Compact always-on-top corner HUD for Antigravity Desktop."""

    def __init__(self) -> None:
        super().__init__()
        self.store = TokenHistoryStore()
        self.selected_conv_id: str | None = None
        self.auto_follow = tk.BooleanVar(value=True)
        self.always_on_top = tk.BooleanVar(value=True)
        self.compact_mode = False
        self.history_win: HistoryWindow | None = None
        self._drag_offset = (0, 0)
        self._sessions_cache: list[SessionSummary] = []

        self.title("AGY Token HUD")
        self.configure(bg=BG)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.94)
        self.resizable(True, True)
        self.minsize(300, 175)
        self._save_job: str | None = None

        self._configure_dark_ttk()
        self._build_ui()
        self._restore_or_place_geometry()
        self.bind("<Configure>", self._on_configure)
        self._poll()

    def _configure_dark_ttk(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Dark.TCombobox",
            fieldbackground=BG_CARD,
            background=BG_HOVER,
            foreground=FG,
            arrowcolor=FG,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            selectbackground=BG_HOVER,
            selectforeground=FG,
            padding=4,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", BG_CARD), ("disabled", BG_CARD)],
            foreground=[("readonly", FG), ("disabled", FG_MUTED)],
            background=[("readonly", BG_HOVER), ("active", "#333846")],
            selectbackground=[("readonly", BG_CARD)],
            selectforeground=[("readonly", FG)],
        )
        self.option_add("*TCombobox*Listbox.background", BG_CARD)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", "#2e4066")
        self.option_add("*TCombobox*Listbox.selectForeground", FG)

    def _restore_or_place_geometry(self) -> None:
        if STATE_PATH.exists():
            try:
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                geom = data.get("geometry")
                if isinstance(geom, str) and "x" in geom and "+" in geom:
                    self.geometry(geom)
                    self.update_idletasks()
                    return
            except (OSError, json.JSONDecodeError):
                pass
        self._place_bottom_right()

    def _save_geometry(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(
                json.dumps({"geometry": self.geometry()}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(400, self._save_geometry)

    def _place_bottom_right(self) -> None:
        self.update_idletasks()
        w = 340
        h = self.winfo_reqheight() or 290
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(12, sw - w - 18)
        y = max(40, sh - h - 56)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _build_ui(self) -> None:
        # Header bar (draggable)
        header = tk.Frame(self, bg=BG_HOVER, padx=8, pady=5)
        header.pack(fill="x")
        header.bind("<ButtonPress-1>", self._start_drag)
        header.bind("<B1-Motion>", self._on_drag)

        title_lbl = tk.Label(
            header,
            text="AGY Token Monitor",
            bg=BG_HOVER,
            fg=ACCENT,
            font=("SF Pro Text", 11, "bold"),
        )
        title_lbl.pack(side="left")
        title_lbl.bind("<ButtonPress-1>", self._start_drag)
        title_lbl.bind("<B1-Motion>", self._on_drag)

        self.pin_btn = DarkButton(
            header,
            text="📌",
            command=self._toggle_topmost,
            bg=BG_HOVER,
            fg=GREEN,
            padx=5,
            pady=1,
            font=("SF Pro Text", 11),
        )
        self.pin_btn.pack(side="right")

        self.compact_btn = DarkButton(
            header,
            text="▾",
            command=self._toggle_compact,
            bg=BG_HOVER,
            fg=FG_MUTED,
            padx=6,
            pady=1,
            font=("SF Pro Text", 11),
        )
        self.compact_btn.pack(side="right")

        # Session selector row
        sess_row = tk.Frame(self, bg=BG, padx=8, pady=6)
        sess_row.pack(fill="x")

        self.session_var = tk.StringVar()
        self.session_combo = ttk.Combobox(
            sess_row,
            textvariable=self.session_var,
            state="readonly",
            style="Dark.TCombobox",
            width=24,
            font=("SF Mono", 10),
        )
        self.session_combo.pack(side="left", fill="x", expand=True)
        self.session_combo.bind("<<ComboboxSelected>>", self._on_combo_select)

        DarkButton(
            sess_row,
            text="✎",
            command=self._rename_session,
            bg=BG_CARD,
            fg=FG,
            padx=6,
            pady=2,
            font=("SF Pro Text", 11),
        ).pack(side="left", padx=(4, 0))

        self.auto_btn = tk.Checkbutton(
            sess_row,
            text="Oto",
            variable=self.auto_follow,
            bg=BG,
            fg=FG_MUTED,
            selectcolor=BG_CARD,
            activebackground=BG,
            activeforeground=FG,
            font=("SF Pro Text", 10),
        )
        self.auto_btn.pack(side="left", padx=(4, 0))

        # Body container (collapsible in compact mode)
        self.body = tk.Frame(self, bg=BG, padx=8, pady=2)
        self.body.pack(fill="both", expand=True)

        self.meta_lbl = tk.Label(
            self.body,
            text="Oturum bekleniyor...",
            bg=BG,
            fg=FG_MUTED,
            font=("SF Mono", 10),
            anchor="w",
        )
        self.meta_lbl.pack(fill="x", pady=(0, 6))

        # Last Turn Card
        self.turn_card = tk.Frame(self.body, bg=BG_CARD, padx=8, pady=6, highlightbackground=BORDER, highlightthickness=1)
        self.turn_card.pack(fill="x", pady=(0, 6))

        self.turn_title = tk.Label(
            self.turn_card,
            text="SON TUR",
            bg=BG_CARD,
            fg=AMBER,
            font=("SF Pro Text", 10, "bold"),
            anchor="w",
        )
        self.turn_title.pack(fill="x")

        self.turn_metrics = tk.Label(
            self.turn_card,
            text="In: -  |  Cache: -  |  Out: -",
            bg=BG_CARD,
            fg=FG,
            font=("SF Mono", 11),
            anchor="w",
        )
        self.turn_metrics.pack(fill="x", pady=(2, 0))

        self.turn_sub = tk.Label(
            self.turn_card,
            text="Toplam: -",
            bg=BG_CARD,
            fg=FG_MUTED,
            font=("SF Mono", 10),
            anchor="w",
        )
        self.turn_sub.pack(fill="x", pady=(2, 0))

        # Session Total Card
        self.sess_card = tk.Frame(self.body, bg=BG_CARD, padx=8, pady=6, highlightbackground=BORDER, highlightthickness=1)
        self.sess_card.pack(fill="x", pady=(0, 6))

        self.sess_title = tk.Label(
            self.sess_card,
            text="OTURUM TOPLAMI",
            bg=BG_CARD,
            fg=PURPLE,
            font=("SF Pro Text", 10, "bold"),
            anchor="w",
        )
        self.sess_title.pack(fill="x")

        self.sess_metrics = tk.Label(
            self.sess_card,
            text="In: -  |  Cache: -  |  Out: -",
            bg=BG_CARD,
            fg=FG,
            font=("SF Mono", 11),
            anchor="w",
        )
        self.sess_metrics.pack(fill="x", pady=(2, 0))

        self.sess_sub = tk.Label(
            self.sess_card,
            text="Toplam: -",
            bg=BG_CARD,
            fg=FG_MUTED,
            font=("SF Mono", 10),
            anchor="w",
        )
        self.sess_sub.pack(fill="x", pady=(2, 0))

        # Footer buttons
        footer = tk.Frame(self.body, bg=BG, pady=4)
        footer.pack(fill="x")

        DarkButton(
            footer,
            text="ID Gir",
            command=self._prompt_conv_id,
            bg=BG_CARD,
            fg=FG,
            padx=8,
            pady=3,
            font=("SF Pro Text", 10),
        ).pack(side="left")

        DarkButton(
            footer,
            text="Geçmiş & Tüm İstatistikler",
            command=self._open_history,
            bg=BG_HOVER,
            fg=ACCENT,
            padx=8,
            pady=3,
            font=("SF Pro Text", 10, "bold"),
        ).pack(side="right")

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_offset = (event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:
        x = self.winfo_x() + event.x - self._drag_offset[0]
        y = self.winfo_y() + event.y - self._drag_offset[1]
        self.geometry(f"+{x}+{y}")

    def _toggle_topmost(self) -> None:
        val = not self.always_on_top.get()
        self.always_on_top.set(val)
        self.attributes("-topmost", val)
        self.pin_btn.configure(fg=GREEN if val else FG_MUTED)

    def _toggle_compact(self) -> None:
        self.compact_mode = not self.compact_mode
        if self.compact_mode:
            self.sess_card.pack_forget()
            self.meta_lbl.pack_forget()
            self.compact_btn.configure(text="▸")
        else:
            self.meta_lbl.pack(fill="x", pady=(0, 6), before=self.turn_card)
            self.sess_card.pack(fill="x", pady=(0, 6), after=self.turn_card)
            self.compact_btn.configure(text="▾")
        self.update_idletasks()
        cur_w = max(300, self.winfo_width())
        self.geometry(f"{cur_w}x{self.winfo_reqheight()}")

    def _on_combo_select(self, _event: tk.Event) -> None:
        idx = self.session_combo.current()
        if 0 <= idx < len(self._sessions_cache):
            self.auto_follow.set(False)
            self.selected_conv_id = self._sessions_cache[idx].conversation_id
            self._render_selected()

    def _rename_session(self) -> None:
        if not self.selected_conv_id:
            return
        sess = self.store.get_session(self.selected_conv_id)
        current = sess.alias if sess else ""
        new_name = simpledialog.askstring(
            "Oturum Adı",
            f"Oturum ({self.selected_conv_id[:8]}) için ad girin:",
            initialvalue=current,
            parent=self,
        )
        if new_name is not None:
            self.store.set_alias(self.selected_conv_id, new_name)
            self._refresh_data()

    def _prompt_conv_id(self) -> None:
        cid = simpledialog.askstring(
            "Conversation ID",
            "İzlemek istediğiniz Conversation ID'yi yapıştırın:",
            parent=self,
        )
        if not cid:
            return
        cid = cid.strip()
        sess = self.store.get_session(cid)
        if not sess:
            # Try prefix match
            for s in self._sessions_cache:
                if s.conversation_id.startswith(cid):
                    cid = s.conversation_id
                    sess = s
                    break
        if sess:
            self.select_session(cid, auto_follow=False)
        else:
            messagebox.showwarning("Bulunamadı", f"Bu ID için kayıt bulunamadı:\n{cid}", parent=self)

    def select_session(self, conv_id: str, auto_follow: bool = False) -> None:
        self.auto_follow.set(auto_follow)
        self.selected_conv_id = conv_id
        self._render_selected()

    def _open_history(self) -> None:
        if self.history_win and self.history_win.winfo_exists():
            self.history_win.lift()
            self.history_win.refresh()
            return
        self.history_win = HistoryWindow(self, self.store)

    def _poll(self) -> None:
        self._refresh_data()
        self.after(POLL_INTERVAL_MS, self._poll)

    def _refresh_data(self) -> None:
        updated = self.store.sync_all_transcripts()
        self._sessions_cache = self.store.list_sessions()

        labels = []
        for s in self._sessions_cache:
            name = (s.alias or s.last_prompt or s.first_prompt or "İsimsiz")[:20]
            labels.append(f"{name} · {s.conversation_id[:8]}")
        self.session_combo["values"] = labels

        if self.auto_follow.get() and self._sessions_cache:
            self.selected_conv_id = self._sessions_cache[0].conversation_id
        elif not self.selected_conv_id and self._sessions_cache:
            self.selected_conv_id = self._sessions_cache[0].conversation_id

        if self.selected_conv_id:
            for i, s in enumerate(self._sessions_cache):
                if s.conversation_id == self.selected_conv_id:
                    self.session_combo.current(i)
                    break

        self._render_selected()
        if updated and self.history_win and self.history_win.winfo_exists():
            self.history_win.refresh()

    def _render_selected(self) -> None:
        if not self.selected_conv_id:
            return
        sess = self.store.get_session(self.selected_conv_id)
        if not sess:
            return

        model_name = sess.last_model or "-"
        self.meta_lbl.configure(
            text=f"ID: {sess.conversation_id[:8]}  |  Model: {model_name}"
        )

        last_turn: TurnSummary | None = sess.turns[-1] if sess.turns else None
        if last_turn:
            self.turn_title.configure(
                text=f"SON TUR (#{last_turn.turn_index} · {last_turn.step_count} adım · {last_turn.tool_call_count} tool · {last_turn.duration_seconds:.1f}s)"
            )
            self.turn_metrics.configure(
                text=(
                    f"In: {fmt_short(last_turn.input_tokens)}  |  "
                    f"Cache: {fmt_short(last_turn.cache_read_tokens)}  |  "
                    f"Out: {fmt_short(last_turn.output_tokens)}"
                )
            )
            self.turn_sub.configure(
                text=f"Tur Toplamı: {fmt_num(last_turn.total_tokens)} tok  ·  {fmt_time(last_turn.ended_at)}"
            )
        else:
            self.turn_title.configure(text="SON TUR")
            self.turn_metrics.configure(text="Veri yok")
            self.turn_sub.configure(text="-")

        self.sess_title.configure(
            text=f"OTURUM TOPLAMI ({sess.total_turns} tur · {sess.total_steps} adım · %{sess.cache_hit_ratio:.0f} cache)"
        )
        self.sess_metrics.configure(
            text=(
                f"In: {fmt_short(sess.total_input_tokens)}  |  "
                f"Cache: {fmt_short(sess.total_cache_read_tokens)}  |  "
                f"Out: {fmt_short(sess.total_output_tokens)}"
            )
        )
        self.sess_sub.configure(
            text=f"Genel Toplam: {fmt_num(sess.total_tokens)} tok  ·  Hata: {sess.total_errors}"
        )

    def destroy(self) -> None:
        self._save_geometry()
        self.store.close()
        super().destroy()


if __name__ == "__main__":
    app = TokenMonitorApp()
    app.mainloop()

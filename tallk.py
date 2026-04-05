import base64
from pathlib import Path
import queue
import tempfile
import threading
import time
import urllib.request
import uuid
import wave
import tkinter as tk
from tkinter.scrolledtext import ScrolledText

import paho.mqtt.client as mqtt

try:
    import sounddevice as sd
except ImportError:
    sd = None

BASE_DIR = Path(__file__).resolve().parent
SOUNDS_DIR = BASE_DIR / "sounds"
RINGTONE_CACHE_DIR = Path(tempfile.gettempdir()) / "tallk"
DEFAULT_BROKER = "test.mosquitto.org"
DEFAULT_PORT = 1883
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_BLOCKSIZE = 2048
CALL_SOUND_URL = "https://raw.githubusercontent.com/leothepicoder2026/Tallk/main/sounds/call_V2.wav"
FIXED_ROOM = "Tallk Servers"
APP_BG = "#0b1220"
SHELL_BG = "#111827"
CARD_BG = "#f8fafc"
PANEL_BG = "#ffffff"
ACCENT = "#34d399"
ACCENT_DARK = "#10b981"
TEXT = "#0f172a"
MUTED = "#64748b"
BORDER = "#cbd5e1"
TITLE_FONT = ("Roboto Condensed", 12, "bold")
HEADER_FONT = ("Roboto Condensed", 24, "bold")
SECTION_FONT = ("Roboto Condensed", 12, "bold")
BODY_FONT = ("Roboto Condensed", 11)
SMALL_FONT = ("Roboto Condensed", 10)


def _ensure_runtime_dir():
    RINGTONE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class ChatApp:
    def __init__(self):
        _ensure_runtime_dir()
        self.root = tk.Tk()
        self.root.title("Tallk Chat")
        self.root.overrideredirect(True)
        self.root.configure(bg=APP_BG)
        self.root.geometry("1180x780")
        self.root.resizable(True, True)

        self.username_var = tk.StringVar(value="")
        self.session_id = uuid.uuid4().hex[:8]
        self.mqtt_client = None
        self.connected = False
        self.chat_room = None
        self.username = None
        self.participant_roles = {}
        self.receive_queue = queue.Queue()
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._active_dialog = None
        self._duplicate_kick_handled = False
        self.active_call_id = None
        self.active_call_peer = None
        self.active_call_peer_session = None
        self.pending_call_id = None
        self.pending_call_after_id = None
        self.ringtone_after_id = None
        self.ringtone_active = False
        self.ringtone_thread = None
        self.ringtone_error = None
        self.audio_topic = None
        self.audio_streaming = False
        self.audio_input_stream = None
        self.audio_output_stream = None
        self.audio_thread = None
        self._active_call_popup = None

        self._build_interface()
        self._center_on_screen(self.root, min_width=960)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._process_receive_queue)
        self.root.after(150, self._show_login_popup)
        self.root.mainloop()

    def _set_participant_role(self, username, role, present=True):
        if not username:
            return
        roles = self.participant_roles.setdefault(username, set())
        if present:
            roles.add(role)
        else:
            roles.discard(role)
        if not roles:
            self.participant_roles.pop(username, None)

    def _display_name_for(self, username):
        return username

    def _build_interface(self):
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        titlebar = tk.Frame(self.root, bg=SHELL_BG, height=42)
        titlebar.grid(row=0, column=0, sticky="ew")
        titlebar.grid_columnconfigure(1, weight=1)
        titlebar.bind("<Button-1>", self._start_window_drag)
        titlebar.bind("<B1-Motion>", self._drag_window)

        tk.Label(titlebar, text="Tallk", bg=SHELL_BG, fg="#e2e8f0", font=TITLE_FONT, padx=14).grid(row=0, column=0, sticky="w")

        title_controls = tk.Frame(titlebar, bg=SHELL_BG)
        title_controls.grid(row=0, column=2, sticky="e", padx=6)
        self.connect_button = tk.Button(title_controls, text="Log In", width=12, command=self._show_login_popup, bg=ACCENT, fg="white", activebackground=ACCENT_DARK, activeforeground="white", bd=0, padx=12, pady=6, font=SMALL_FONT)
        self.connect_button.pack(side="left", padx=(0, 10), pady=4)
        tk.Button(title_controls, text="_", command=self.root.iconify, width=4, bg=SHELL_BG, fg="#cbd5e1", activebackground="#1f2937", activeforeground="white", bd=0, font=TITLE_FONT).pack(side="left", padx=(0, 4), pady=4)
        tk.Button(title_controls, text="X", command=self.close, width=4, bg="#7f1d1d", fg="white", activebackground="#991b1b", activeforeground="white", bd=0, font=TITLE_FONT).pack(side="left", pady=4)

        app_shell = tk.Frame(self.root, bg=APP_BG, padx=18, pady=18)
        app_shell.grid(row=1, column=0, sticky="nsew")
        app_shell.grid_rowconfigure(0, weight=1)
        app_shell.grid_columnconfigure(0, weight=1)

        app_card = tk.Frame(app_shell, bg=CARD_BG, bd=0, highlightthickness=1, highlightbackground="#1e293b")
        app_card.grid(row=0, column=0, sticky="nsew")
        app_card.grid_rowconfigure(1, weight=1)
        app_card.grid_columnconfigure(0, weight=3)
        app_card.grid_columnconfigure(1, weight=1)

        status_strip = tk.Frame(app_card, bg="#e2e8f0", padx=18, pady=10)
        status_strip.grid(row=0, column=0, columnspan=2, sticky="ew")
        status_strip.grid_columnconfigure(0, weight=1)
        status_strip.grid_columnconfigure(1, weight=1)
        self.status_label = tk.Label(status_strip, text="Ready", anchor="w", bg="#e2e8f0", fg="#0f172a", font=BODY_FONT)
        self.status_label.grid(row=0, column=0, sticky="ew")
        self.call_status_label = tk.Label(status_strip, text="Click someone in the room to start a voice call.", anchor="e", bg="#e2e8f0", fg=MUTED, font=BODY_FONT)
        self.call_status_label.grid(row=0, column=1, sticky="ew")

        chat_panel = tk.Frame(app_card, bg=CARD_BG, padx=18, pady=18)
        chat_panel.grid(row=1, column=0, sticky="nsew")
        chat_panel.grid_rowconfigure(1, weight=1)
        chat_panel.grid_columnconfigure(0, weight=1)

        tk.Label(chat_panel, text="Conversation", anchor="w", bg=CARD_BG, fg=TEXT, font=SECTION_FONT).grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.chat_area = ScrolledText(chat_panel, wrap="word", state="disabled", font=BODY_FONT, bg=PANEL_BG, fg=TEXT, relief="flat", bd=0, insertbackground=TEXT, padx=12, pady=12)
        self.chat_area.grid(row=1, column=0, sticky="nsew")

        input_frame = tk.Frame(chat_panel, bg=CARD_BG, pady=14)
        input_frame.grid(row=2, column=0, sticky="ew")
        input_frame.grid_columnconfigure(0, weight=1)
        self.message_var = tk.StringVar()
        self.message_entry = tk.Entry(input_frame, textvariable=self.message_var, font=BODY_FONT, relief="flat", bg=PANEL_BG, fg=TEXT, insertbackground=TEXT)
        self.message_entry.grid(row=0, column=0, sticky="ew", ipady=10, padx=(0, 10))
        self.message_entry.bind("<Return>", lambda event: self.send_message())
        tk.Button(input_frame, text="Send", width=10, command=self.send_message, bg=ACCENT, fg="white", activebackground=ACCENT_DARK, activeforeground="white", bd=0, padx=14, pady=10, font=SECTION_FONT).grid(row=0, column=1, sticky="e")

        side_panel = tk.Frame(app_card, bg="#e2e8f0", padx=18, pady=18)
        side_panel.grid(row=1, column=1, sticky="nsew")
        side_panel.grid_rowconfigure(2, weight=1)
        side_panel.grid_columnconfigure(0, weight=1)

        tk.Label(side_panel, text="People Online", anchor="w", bg="#e2e8f0", fg=TEXT, font=SECTION_FONT).grid(row=0, column=0, sticky="ew")
        tk.Label(side_panel, text="Click a name to start a voice call.", anchor="w", justify="left", bg="#e2e8f0", fg=MUTED, font=SMALL_FONT).grid(row=1, column=0, sticky="ew", pady=(4, 12))
        self.participants_text = ScrolledText(side_panel, state="disabled", font=BODY_FONT, width=22, relief="flat", bd=0, bg=PANEL_BG, fg=TEXT, padx=12, pady=12)
        self.participants_text.grid(row=2, column=0, sticky="nsew")
        self.participants_text.bind("<Motion>", self._update_participant_hover)
        self.participants_text.bind("<Leave>", lambda event: self.participants_text.configure(cursor=""))


    def _get_color(self, username):
        colors = ["#1e90ff", "#32cd32", "#dc143c", "#9370db", "#ff8c00", "#8b4513", "#00ced1", "#ff69b4"]
        return colors[hash(username) % len(colors)]

    def _start_window_drag(self, event):
        self._drag_offset_x = event.x_root - self.root.winfo_x()
        self._drag_offset_y = event.y_root - self.root.winfo_y()

    def _drag_window(self, event):
        x = event.x_root - self._drag_offset_x
        y = event.y_root - self._drag_offset_y
        self.root.geometry(f"+{x}+{y}")

    def _start_dialog_drag(self, event, dialog):
        dialog._drag_offset_x = event.x_root - dialog.winfo_x()
        dialog._drag_offset_y = event.y_root - dialog.winfo_y()

    def _drag_dialog(self, event, dialog):
        x = event.x_root - dialog._drag_offset_x
        y = event.y_root - dialog._drag_offset_y
        dialog.geometry(f"+{x}+{y}")

    def _center_on_screen(self, window, min_width=0):
        window.update_idletasks()
        width = max(window.winfo_width(), min_width)
        height = window.winfo_height()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max((screen_w - width) // 2, 0)
        y = max((screen_h - height) // 2, 0)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _make_fullscreen_popup(self, dialog, card_bg, min_width=340, title_text="Tallk"):
        dialog.overrideredirect(True)
        dialog.transient(self.root)
        dialog.configure(bg=APP_BG)
        shell = tk.Frame(dialog, bg=APP_BG, padx=12, pady=12)
        shell.pack(fill="both", expand=True)
        card = tk.Frame(shell, bg=card_bg, bd=0, highlightthickness=1, highlightbackground=BORDER, padx=24, pady=24)
        card.pack(fill="both", expand=True)
        if min_width:
            card.configure(width=min_width)
        card.bind("<Button-1>", lambda event: self._start_dialog_drag(event, dialog))
        card.bind("<B1-Motion>", lambda event: self._drag_dialog(event, dialog))
        dialog.lift()
        dialog.attributes("-topmost", True)
        dialog.after_idle(lambda: self._center_on_screen(dialog, min_width=min_width))
        return card

    def _show_dialog(self, title, message):
        self._stop_ringtone()
        if self._active_dialog is not None and self._active_dialog.winfo_exists():
            self._active_dialog.destroy()

        dialog = tk.Toplevel(self.root)
        self._active_dialog = dialog

        body = self._make_fullscreen_popup(dialog, "#f8fafc", min_width=360, title_text="System")

        banner = tk.Frame(body, bg="#dcfce7", padx=16, pady=14)
        banner.pack(fill="x", pady=(0, 18))
        icon = tk.Canvas(banner, width=44, height=44, bg="#dcfce7", highlightthickness=0)
        icon.pack(side="left")
        icon.create_oval(4, 4, 40, 40, fill=ACCENT, outline="#86efac", width=2)
        icon.create_text(22, 22, text="i", fill="white", font=("Roboto Condensed", 20, "bold"))

        banner_text = tk.Frame(banner, bg="#dcfce7")
        banner_text.pack(side="left", fill="both", expand=True, padx=(12, 0))
        tk.Label(banner_text, text=title, anchor="w", bg="#dcfce7", fg=TEXT, font=("Roboto Condensed", 20, "bold")).pack(fill="x")
        tk.Label(banner_text, text="Tallk notice", anchor="w", bg="#dcfce7", fg=ACCENT_DARK, font=SMALL_FONT).pack(fill="x", pady=(3, 0))

        message_card = tk.Frame(body, bg=PANEL_BG, padx=18, pady=18, highlightthickness=1, highlightbackground="#bbf7d0")
        message_card.pack(fill="x")
        tk.Label(message_card, text=message, bg=PANEL_BG, fg=MUTED, justify="left", wraplength=340, font=BODY_FONT).pack(fill="x")

        ok_button = tk.Button(
            body,
            text="OK",
            width=12,
            command=dialog.destroy,
            bg=ACCENT,
            fg="white",
            activebackground=ACCENT_DARK,
            activeforeground="white",
            bd=0,
            padx=18,
            pady=10,
            font=SECTION_FONT,
        )
        ok_button.pack(fill="x", pady=(18, 0))

        focus_target = self.message_entry if self.connected else self.connect_button

        def close_dialog(event=None):
            if self._active_dialog is dialog:
                self._active_dialog = None
            if dialog.winfo_exists():
                try:
                    dialog.grab_release()
                except tk.TclError:
                    pass
                dialog.destroy()
            self.root.after_idle(self.root.focus_force)
            if focus_target.winfo_exists():
                self.root.after_idle(focus_target.focus_set)

        def clear_active_dialog(event):
            if event.widget is dialog and self._active_dialog is dialog:
                self._active_dialog = None

        dialog.bind("<Escape>", close_dialog)
        dialog.bind("<Return>", close_dialog)
        dialog.bind("<Destroy>", clear_active_dialog)
        ok_button.configure(command=close_dialog)

        dialog.grab_set()
        dialog.focus_force()
        ok_button.focus_set()

    def _show_choice_dialog(self, title, message, confirm_text, on_confirm, on_cancel=None, stop_ringtone=True):
        if stop_ringtone:
            self._stop_ringtone()
        if self._active_dialog is not None and self._active_dialog.winfo_exists():
            self._active_dialog.destroy()

        dialog = tk.Toplevel(self.root)
        self._active_dialog = dialog

        body = self._make_fullscreen_popup(dialog, "#f8fafc", min_width=380, title_text="Confirm")

        banner = tk.Frame(body, bg="#f5f3ff", padx=16, pady=14)
        banner.pack(fill="x", pady=(0, 18))
        icon = tk.Canvas(banner, width=44, height=44, bg="#f5f3ff", highlightthickness=0)
        icon.pack(side="left")
        icon.create_oval(4, 4, 40, 40, fill="#7c3aed", outline="#c4b5fd", width=2)
        icon.create_text(22, 22, text="?", fill="white", font=("Roboto Condensed", 20, "bold"))

        banner_text = tk.Frame(banner, bg="#f5f3ff")
        banner_text.pack(side="left", fill="both", expand=True, padx=(12, 0))
        tk.Label(banner_text, text=title, anchor="w", bg="#f5f3ff", fg=TEXT, font=("Roboto Condensed", 20, "bold")).pack(fill="x")
        tk.Label(banner_text, text="Choose what Tallk should do next.", anchor="w", bg="#f5f3ff", fg="#7c3aed", font=SMALL_FONT).pack(fill="x", pady=(3, 0))

        message_card = tk.Frame(body, bg=PANEL_BG, padx=18, pady=18, highlightthickness=1, highlightbackground="#ddd6fe")
        message_card.pack(fill="x")
        tk.Label(message_card, text=message, bg=PANEL_BG, fg=MUTED, justify="left", wraplength=360, font=BODY_FONT).pack(fill="x")

        button_row = tk.Frame(body, bg="#f8fafc")
        button_row.pack(fill="x", pady=(18, 0))
        confirmed = {"value": False}

        def close_dialog(event=None):
            if self._active_dialog is dialog:
                self._active_dialog = None
            if dialog.winfo_exists():
                try:
                    dialog.grab_release()
                except tk.TclError:
                    pass
                dialog.destroy()
            self.root.after_idle(self.root.focus_force)
            if on_cancel is not None and not confirmed["value"]:
                on_cancel()

        def confirm(event=None):
            confirmed["value"] = True
            close_dialog()
            on_confirm()

        cancel_button = tk.Button(
            button_row,
            text="Cancel",
            command=close_dialog,
            bg="#e2e8f0",
            fg="#0f172a",
            activebackground="#cbd5e1",
            activeforeground="#0f172a",
            bd=0,
            padx=18,
            pady=10,
            font=SECTION_FONT,
        )
        cancel_button.pack(side="left", fill="x", expand=True, padx=(0, 8))

        confirm_button = tk.Button(
            button_row,
            text=confirm_text,
            command=confirm,
            bg=ACCENT,
            fg="white",
            activebackground=ACCENT_DARK,
            activeforeground="white",
            bd=0,
            padx=18,
            pady=10,
            font=SECTION_FONT,
        )
        confirm_button.pack(side="left", fill="x", expand=True, padx=(8, 0))

        dialog.bind("<Escape>", close_dialog)
        dialog.bind("<Return>", confirm)
        dialog.grab_set()
        dialog.focus_force()
        confirm_button.focus_set()

    def _show_login_popup(self):
        if self.connected:
            self._show_dialog("Already online", "You are already online.")
            return
        if self._active_dialog is not None and self._active_dialog.winfo_exists():
            self._active_dialog.destroy()

        dialog = tk.Toplevel(self.root)
        self._active_dialog = dialog

        body = self._make_fullscreen_popup(dialog, "#f8fbff", min_width=430, title_text="Log In")

        hero = tk.Frame(body, bg=SHELL_BG, padx=18, pady=18)
        hero.pack(fill="x", pady=(0, 18))

        hero_badge = tk.Canvas(hero, width=64, height=64, bg=SHELL_BG, highlightthickness=0)
        hero_badge.pack(side="left")
        hero_badge.create_oval(6, 6, 58, 58, fill=ACCENT, outline="#86efac", width=2)
        hero_badge.create_text(32, 32, text="T", fill="white", font=("Roboto Condensed", 24, "bold"))

        hero_text = tk.Frame(hero, bg=SHELL_BG)
        hero_text.pack(side="left", fill="both", expand=True, padx=(14, 0))
        tk.Label(hero_text, text="Welcome Back", anchor="w", bg=SHELL_BG, fg="white", font=("Roboto Condensed", 24, "bold")).pack(fill="x")
        tk.Label(hero_text, text="Jump straight into the Tallk room.", anchor="w", bg=SHELL_BG, fg="#94a3b8", font=BODY_FONT).pack(fill="x", pady=(4, 0))

        form_card = tk.Frame(body, bg=PANEL_BG, padx=18, pady=18, highlightthickness=1, highlightbackground="#bbf7d0")
        form_card.pack(fill="x")
        tk.Label(form_card, text="Display Name", anchor="w", bg=PANEL_BG, fg=TEXT, font=SECTION_FONT).pack(fill="x")
        tk.Label(form_card, text="Use the name other Tallk users will see.", anchor="w", bg=PANEL_BG, fg=MUTED, font=SMALL_FONT).pack(fill="x", pady=(4, 10))
        name_entry = tk.Entry(form_card, textvariable=self.username_var, font=("Roboto Condensed", 16), relief="flat", bg="#ecfdf5", fg=TEXT, insertbackground=TEXT)
        name_entry.pack(fill="x", ipady=10)

        button_row = tk.Frame(body, bg="#f8fbff")
        button_row.pack(fill="x", pady=(18, 0))

        def close_dialog(event=None):
            if self._active_dialog is dialog:
                self._active_dialog = None
            if dialog.winfo_exists():
                try:
                    dialog.grab_release()
                except tk.TclError:
                    pass
                dialog.destroy()
            self.root.after_idle(self.root.focus_force)

        def submit(event=None):
            close_dialog()
            self.connect()

        tk.Button(
            button_row,
            text="Cancel",
            command=close_dialog,
            bg="#e2e8f0",
            fg=TEXT,
            activebackground="#cbd5e1",
            activeforeground=TEXT,
            bd=0,
            padx=18,
            pady=10,
            font=SECTION_FONT,
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        tk.Button(
            button_row,
            text="Log In",
            command=submit,
            bg=ACCENT,
            fg="white",
            activebackground=ACCENT_DARK,
            activeforeground="white",
            bd=0,
            padx=18,
            pady=10,
            font=SECTION_FONT,
        ).pack(side="left", fill="x", expand=True, padx=(8, 0))

        dialog.bind("<Escape>", close_dialog)
        dialog.bind("<Return>", submit)
        dialog.grab_set()
        dialog.focus_force()
        name_entry.focus_set()

    def _show_incoming_call_dialog(self, caller_name, on_accept, on_decline):
        if self._active_dialog is not None and self._active_dialog.winfo_exists():
            self._active_dialog.destroy()

        dialog = tk.Toplevel(self.root)
        self._active_dialog = dialog

        body = self._make_fullscreen_popup(dialog, "#f8fafc", min_width=400, title_text="Incoming Call")

        hero = tk.Frame(body, bg="#052e2b", padx=18, pady=18)
        hero.pack(fill="x", pady=(0, 18))

        avatar = tk.Canvas(hero, width=82, height=82, bg="#052e2b", highlightthickness=0)
        avatar.pack()
        avatar.create_oval(6, 6, 76, 76, fill=ACCENT, outline="#a7f3d0", width=2)
        avatar.create_text(41, 41, text=(caller_name[:1] or "?").upper(), fill="white", font=("Roboto Condensed", 30, "bold"))

        tk.Label(hero, text="Incoming voice call", bg="#052e2b", fg="#6ee7b7", font=SECTION_FONT).pack(pady=(12, 2))
        tk.Label(hero, text=caller_name, bg="#052e2b", fg="white", font=("Roboto Condensed", 28, "bold")).pack()
        tk.Label(hero, text="Answer now or decline the call.", bg="#052e2b", fg="#d1fae5", font=BODY_FONT).pack(pady=(4, 0))

        button_row = tk.Frame(body, bg="#f8fafc")
        button_row.pack(fill="x")
        confirmed = {"value": False}

        def close_dialog(event=None):
            if self._active_dialog is dialog:
                self._active_dialog = None
            if dialog.winfo_exists():
                try:
                    dialog.grab_release()
                except tk.TclError:
                    pass
                dialog.destroy()
            self.root.after_idle(self.root.focus_force)
            if not confirmed["value"]:
                on_decline()

        def accept(event=None):
            confirmed["value"] = True
            close_dialog()
            on_accept()

        decline_button = tk.Button(
            button_row,
            text="Decline",
            command=close_dialog,
            bg="#ef4444",
            fg="white",
            activebackground="#dc2626",
            activeforeground="white",
            bd=0,
            padx=18,
            pady=10,
            font=SECTION_FONT,
        )
        decline_button.pack(side="left", fill="x", expand=True, padx=(0, 8))

        accept_button = tk.Button(
            button_row,
            text="Accept",
            command=accept,
            bg="#22c55e",
            fg="white",
            activebackground="#16a34a",
            activeforeground="white",
            bd=0,
            padx=18,
            pady=10,
            font=SECTION_FONT,
        )
        accept_button.pack(side="left", fill="x", expand=True, padx=(8, 0))

        dialog.bind("<Escape>", close_dialog)
        dialog.bind("<Return>", accept)

        dialog.grab_set()
        dialog.focus_force()
        accept_button.focus_set()

    def _show_active_call_popup(self, peer_name):
        self._close_active_call_popup()

        popup = tk.Toplevel(self.root)
        self._active_call_popup = popup

        body = self._make_fullscreen_popup(popup, "#ecfdf5", min_width=380, title_text="Active Call")

        hero = tk.Frame(body, bg="#064e3b", padx=18, pady=18)
        hero.pack(fill="x", pady=(0, 18))

        avatar = tk.Canvas(hero, width=82, height=82, bg="#064e3b", highlightthickness=0)
        avatar.pack()
        avatar.create_oval(6, 6, 76, 76, fill=ACCENT, outline="#a7f3d0", width=2)
        avatar.create_text(41, 41, text=(peer_name[:1] or "?").upper(), fill="white", font=("Roboto Condensed", 30, "bold"))

        tk.Label(hero, text="Live Voice Call", bg="#064e3b", fg="#6ee7b7", font=SECTION_FONT).pack(pady=(12, 2))
        tk.Label(hero, text=peer_name, bg="#064e3b", fg="white", font=("Roboto Condensed", 28, "bold")).pack()
        tk.Label(hero, text="Connection is active", bg="#064e3b", fg="#d1fae5", font=BODY_FONT).pack(pady=(4, 0))

        tk.Button(
            body,
            text="Hang Up",
            command=self._end_active_call,
            bg="#ef4444",
            fg="white",
            activebackground="#dc2626",
            activeforeground="white",
            bd=0,
            padx=18,
            pady=10,
            font=SECTION_FONT,
        ).pack(fill="x")

        popup.bind("<Escape>", lambda event: self._end_active_call())
        popup.focus_force()

    def _close_active_call_popup(self):
        if self._active_call_popup is not None and self._active_call_popup.winfo_exists():
            try:
                self._active_call_popup.destroy()
            except tk.TclError:
                pass
        self._active_call_popup = None

    def disconnect(self):
        if not self.connected:
            return
        self._end_active_call(notify_peer=True)
        if self.mqtt_client:
            try:
                presence_topic = f"tallk/{self.chat_room}/presence"
                self.mqtt_client.publish(presence_topic, f"LEAVE|{self.username}|{self.session_id}|app")
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception:
                pass
        self._on_disconnect(None, None, 0)

    def connect(self):
        if self.connected:
            self._show_dialog("Already online", "You are already online.")
            return

        self.username = self.username_var.get().strip()
        if not self.username:
            self._show_dialog("Missing name", "Please enter a display name before connecting.")
            return

        self.chat_room = FIXED_ROOM
        self._duplicate_kick_handled = False

        broker = DEFAULT_BROKER
        port = DEFAULT_PORT

        self.append_message(f"Connecting to Tallk servers...")
        self.status_label.configure(text=f"Connecting to {broker}:{port}...")
        self.connect_button.configure(state="disabled")

        client_id = f"tallk-{self.session_id}"
        self.mqtt_client = mqtt.Client(client_id=client_id)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_disconnect = self._on_disconnect

        presence_topic = f"tallk/{self.chat_room}/presence"
        self.mqtt_client.will_set(presence_topic, f"LEAVE|{self.username}|{self.session_id}|app", qos=0, retain=False)
        self.participant_roles = {}
        self._update_participants()

        try:
            self.mqtt_client.connect(broker, port, keepalive=60)
            self.mqtt_client.loop_start()
        except Exception as exc:
            self.receive_queue.put((f"[ERROR] Could not connect to broker: {exc}", True, None))
            self.connect_button.configure(text="Log In", command=self._show_login_popup, state="normal")
            self.status_label.configure(text="Ready")
            self.mqtt_client = None

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            self.receive_queue.put((f"[ERROR] MQTT connect failed with code {rc}", True, None))
            self.root.after(0, lambda: self.connect_button.configure(text="Log In", command=self._show_login_popup, state="normal"))
            return

        chat_topic = f"tallk/{self.chat_room}/chat"
        presence_topic = f"tallk/{self.chat_room}/presence"
        call_topic = f"tallk/{self.chat_room}/call"
        client.subscribe([(chat_topic, 0), (presence_topic, 0), (call_topic, 0)])
        self.connected = True
        self.participant_roles = {}
        self._set_participant_role(self.username, "app", present=True)
        self._update_participants()
        self.receive_queue.put((f"Connected to Tallk servers.", True, None))
        self.root.after(0, lambda: self.status_label.configure(text=f"Connected to {DEFAULT_BROKER}:{DEFAULT_PORT}"))
        self.root.after(0, lambda: self.connect_button.configure(state="disabled"))
        client.publish(presence_topic, f"JOIN|{self.username}|{self.session_id}|app")
        self.connect_button.configure(text="Disconnect", command=self.disconnect, state="normal")

    def _on_disconnect(self, client, userdata, rc):
        self._stop_audio_streams()
        self.active_call_id = None
        self.active_call_peer = None
        self.active_call_peer_session = None
        self.pending_call_id = None
        self.pending_call_after_id = None
        self._stop_ringtone()
        self.audio_topic = None
        self.connected = False
        self.participant_roles = {}
        self._update_participants()
        self.receive_queue.put((f"Disconnected from Tallk servers.", True, None))
        self.root.after(0, lambda: self.connect_button.configure(text="Log In", command=self._show_login_popup, state="normal"))
        self.root.after(0, lambda: self.status_label.configure(text="Ready"))
        self.root.after(0, lambda: self._set_call_status("Click someone in the room to start a voice call."))

    def _on_message(self, client, userdata, message):
        try:
            payload = message.payload.decode("utf-8", errors="replace")
        except Exception as exc:
            payload = f"[ERROR] Failed to decode message: {exc}"

        topic = message.topic
        if topic.endswith("/presence"):
            parts = payload.split("|", 2)
            action = parts[0]
            username = parts[1] if len(parts) > 1 else ""
            sender_session = parts[2] if len(parts) > 2 else None

            if action == "JOIN":
                if sender_session == self.session_id:
                    return
                if username == self.username and sender_session:
                    presence_topic = f"tallk/{self.chat_room}/presence"
                    client.publish(presence_topic, f"KICK|{username}|{sender_session}|app")
                    return
                if username != self.username:
                    presence_topic = f"tallk/{self.chat_room}/presence"
                    client.publish(presence_topic, f"HERE|{self.username}|{self.session_id}|app")
                self._set_participant_role(username, "app", present=True)
                self._update_participants()
                self.receive_queue.put((f"{username} is available.", True, None))
            elif action == "HERE":
                if sender_session == self.session_id:
                    return
                if username == self.username and sender_session:
                    self.root.after(0, self._handle_duplicate_username)
                    return
                self._set_participant_role(username, "app", present=True)
                self._update_participants()
            elif action == "LEAVE":
                if sender_session == self.session_id:
                    return
                self._set_participant_role(username, "app", present=False)
                self._update_participants()
                if username == self.active_call_peer:
                    self.root.after(0, lambda: self._end_active_call(notify_peer=False, reason=f"{username} left the room."))
                if username not in self.participant_roles:
                    self.receive_queue.put((f"{username} went offline.", True, None))
            elif action == "KICK" and username == self.username and sender_session == self.session_id:
                self.root.after(0, self._handle_duplicate_username)
            return

        if topic.endswith("/call"):
            self._handle_call_message(payload)
            return

        if self.audio_topic and topic == self.audio_topic:
            self._handle_audio_message(payload)
            return
        if "/call-audio/" in topic:
            return

        self.receive_queue.put((payload, False, "receive"))

    def _update_participants(self):
        self.participants_text.configure(state="normal")
        self.participants_text.delete(1.0, "end")
        for username in sorted(self.participant_roles):
            display_name = self._display_name_for(username)
            color = self._get_color(username)
            self.participants_text.insert("end", display_name + "\n", (username,))
            self.participants_text.tag_configure(username, foreground=color)
            self.participants_text.tag_bind(username, "<Button-1>", lambda event, name=username: self._call_participant(name))
        self.participants_text.configure(state="disabled")

    def _update_participant_hover(self, event):
        index = self.participants_text.index(f"@{event.x},{event.y}")
        tags = [tag for tag in self.participants_text.tag_names(index) if tag in self.participant_roles]
        clickable = any(tag != self.username for tag in tags)
        self.participants_text.configure(cursor="hand2" if clickable else "")

    def _set_call_status(self, text):
        self.call_status_label.configure(text=text)

    def _publish_call_control(self, action, call_id, target_name="", target_session=""):
        if not self.connected or self.mqtt_client is None:
            return
        call_topic = f"tallk/{self.chat_room}/call"
        payload = "|".join([action, call_id, self.username or "", self.session_id, target_name or "", target_session or ""])
        self.mqtt_client.publish(call_topic, payload)

    def _call_participant(self, username):
        if username == self.username:
            return
        if not self.connected or self.mqtt_client is None:
            self._show_dialog("Not connected", "Connect to a chatroom before starting a voice call.")
            return
        if self.active_call_id or self.pending_call_id:
            self._show_dialog("Call in progress", "Finish the current call before starting another one.")
            return
        if sd is None:
            self._show_dialog("Audio support missing", "Install the Python package 'sounddevice' on both computers to use voice calling.")
            return

        call_id = uuid.uuid4().hex[:10]
        self.pending_call_id = call_id
        self._set_call_status(f"Calling {username}...")
        self._publish_call_control("REQUEST", call_id, username)
        self._schedule_pending_call_timeout(call_id, username)

    def _handle_call_message(self, payload):
        parts = payload.split("|", 5)
        action = parts[0] if len(parts) > 0 else ""
        call_id = parts[1] if len(parts) > 1 else ""
        sender_name = parts[2] if len(parts) > 2 else ""
        sender_session = parts[3] if len(parts) > 3 else ""
        target_name = parts[4] if len(parts) > 4 else ""
        target_session = parts[5] if len(parts) > 5 else ""

        if sender_session == self.session_id:
            return

        if action == "REQUEST" and target_name == self.username:
            self._handle_incoming_call(call_id, sender_name, sender_session)
        elif action == "ACCEPT" and target_name == self.username and call_id == self.pending_call_id:
            self._clear_pending_call_timeout()
            self.pending_call_id = None
            self.root.after(0, lambda: self._begin_call(call_id, sender_name, sender_session))
        elif action == "DECLINE" and target_name == self.username and call_id == self.pending_call_id:
            self._clear_pending_call_timeout()
            self.pending_call_id = None
            self.root.after(0, lambda: self._set_call_status(f"{sender_name} declined your call."))
        elif action == "END" and target_name == self.username and call_id == self.active_call_id:
            self.root.after(0, lambda: self._end_active_call(notify_peer=False, reason=f"{sender_name} ended the call."))

    def _handle_incoming_call(self, call_id, caller_name, caller_session):
        if self.active_call_id or self.pending_call_id:
            self._publish_call_control("DECLINE", call_id, caller_name, caller_session)
            return
        if sd is None:
            self._publish_call_control("DECLINE", call_id, caller_name, caller_session)
            self.root.after(0, lambda: self._show_dialog("Missed call", f"{caller_name} tried to voice call you, but audio support is not installed here."))
            return

        def accept_call():
            self._stop_ringtone()
            self._publish_call_control("ACCEPT", call_id, caller_name, caller_session)
            self._begin_call(call_id, caller_name, caller_session)

        def decline_call():
            self._stop_ringtone()
            self._publish_call_control("DECLINE", call_id, caller_name, caller_session)

        self.root.after(0, self._start_ringtone)
        self.root.after(0, lambda: self._show_incoming_call_dialog(caller_name, accept_call, decline_call))

    def _begin_call(self, call_id, peer_name, peer_session):
        self.active_call_id = call_id
        self.active_call_peer = peer_name
        self.active_call_peer_session = peer_session
        self.audio_topic = f"tallk/{self.chat_room}/call-audio/{call_id}"
        if self.mqtt_client is not None:
            self.mqtt_client.subscribe([(self.audio_topic, 0)])
        self._set_call_status(f"In voice call with {peer_name}.")
        self._show_active_call_popup(peer_name)
        self._start_audio_streams()

    def _end_active_call(self, notify_peer=True, reason="Call ended."):
        ended_call_id = self.active_call_id
        peer_name = self.active_call_peer
        peer_session = self.active_call_peer_session

        if notify_peer and ended_call_id and peer_name:
            self._publish_call_control("END", ended_call_id, peer_name, peer_session)

        self._clear_pending_call_timeout()
        self._stop_ringtone()
        self._close_active_call_popup()
        self._stop_audio_streams()
        if self.mqtt_client is not None and self.audio_topic:
            try:
                self.mqtt_client.unsubscribe(self.audio_topic)
            except Exception:
                pass

        self.active_call_id = None
        self.active_call_peer = None
        self.active_call_peer_session = None
        self.pending_call_id = None
        self.audio_topic = None
        self._set_call_status(reason)

    def _start_audio_streams(self):
        if sd is None or not self.audio_topic or self.audio_streaming:
            return
        self.audio_streaming = True

        try:
            self.audio_output_stream = sd.RawOutputStream(
                samplerate=AUDIO_SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
                dtype="int16",
                blocksize=AUDIO_BLOCKSIZE,
            )
            self.audio_output_stream.start()
            self.audio_input_stream = sd.RawInputStream(
                samplerate=AUDIO_SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
                dtype="int16",
                blocksize=AUDIO_BLOCKSIZE,
            )
            self.audio_input_stream.start()
        except Exception as exc:
            self.audio_streaming = False
            self.audio_input_stream = None
            self.audio_output_stream = None
            self._show_dialog("Audio error", f"Voice call setup failed: {exc}")
            self._end_active_call(notify_peer=True, reason="Voice call failed to start.")
            return

        self.audio_thread = threading.Thread(target=self._audio_capture_loop, daemon=True)
        self.audio_thread.start()

    def _audio_capture_loop(self):
        while self.audio_streaming and self.mqtt_client is not None and self.audio_input_stream is not None:
            try:
                data, overflowed = self.audio_input_stream.read(AUDIO_BLOCKSIZE)
                if overflowed or not data:
                    continue
                encoded = base64.b64encode(data).decode("ascii")
                payload = "|".join(["AUDIO", self.active_call_id or "", self.username or "", self.session_id, encoded])
                self.mqtt_client.publish(self.audio_topic, payload)
            except Exception:
                break
            time.sleep(0.01)

    def _handle_audio_message(self, payload):
        if not self.active_call_id or self.audio_output_stream is None:
            return
        parts = payload.split("|", 4)
        if len(parts) != 5 or parts[0] != "AUDIO":
            return
        _, call_id, sender_name, sender_session, encoded_audio = parts
        if call_id != self.active_call_id or sender_session == self.session_id:
            return
        if sender_name != self.active_call_peer:
            return
        try:
            audio_bytes = base64.b64decode(encoded_audio.encode("ascii"))
            self.audio_output_stream.write(audio_bytes)
        except Exception:
            pass

    def _stop_audio_streams(self):
        self.audio_streaming = False
        if self.audio_input_stream is not None:
            try:
                self.audio_input_stream.stop()
                self.audio_input_stream.close()
            except Exception:
                pass
        if self.audio_output_stream is not None:
            try:
                self.audio_output_stream.stop()
                self.audio_output_stream.close()
            except Exception:
                pass
        self.audio_input_stream = None
        self.audio_output_stream = None
        self.audio_thread = None

    def _schedule_pending_call_timeout(self, call_id, username):
        self._clear_pending_call_timeout()

        def expire_call():
            if self.pending_call_id == call_id:
                self.pending_call_id = None
                self._set_call_status(f"{username} did not answer.")

        self.pending_call_after_id = self.root.after(20000, expire_call)

    def _clear_pending_call_timeout(self):
        if self.pending_call_after_id is not None:
            try:
                self.root.after_cancel(self.pending_call_after_id)
            except Exception:
                pass
        self.pending_call_after_id = None

    def _start_ringtone(self):
        if self.ringtone_active:
            return
        self.ringtone_active = True
        self.ringtone_error = None
        if self._start_call_wav_ringtone():
            return

        self.ringtone_thread = threading.Thread(target=self._ringtone_fallback_loop, daemon=True)
        self.ringtone_thread.start()

    def _start_call_wav_ringtone(self):
        call_sound = self._resolve_call_sound_path()
        if call_sound is None:
            return False

        try:
            import winsound
            with wave.open(str(call_sound), "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                frame_count = wav_file.getnframes()
            duration = max(frame_count / frame_rate, 0.5) if frame_rate else 2.0
            self.ringtone_thread = threading.Thread(
                target=self._call_wav_ringtone_loop,
                args=(str(call_sound), duration),
                daemon=True,
            )
            self.ringtone_thread.start()
            return True
        except Exception as exc:
            self.ringtone_error = str(exc)
            return False

    def _resolve_call_sound_path(self):
        cached_sound = self._download_call_sound()
        if cached_sound is not None:
            return cached_sound

        local_sound = (SOUNDS_DIR / "call_V2.wav").resolve()
        if local_sound.exists():
            return local_sound

        if not self.ringtone_error:
            self.ringtone_error = f"Missing ringtone file: {local_sound}"
        return None

    def _download_call_sound(self):
        try:
            RINGTONE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cached_sound = RINGTONE_CACHE_DIR / "call_V2.wav"
            urllib.request.urlretrieve(CALL_SOUND_URL, cached_sound)
            return cached_sound
        except Exception as exc:
            self.ringtone_error = f"GitHub ringtone download failed: {exc}"
            return None

    def _call_wav_ringtone_loop(self, sound_path, duration):
        try:
            import winsound
        except Exception as exc:
            self.ringtone_error = str(exc)
            self.ringtone_active = False
            return

        while self.ringtone_active:
            try:
                winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception as exc:
                self.ringtone_error = str(exc)
                self.ringtone_active = False
                break

            steps = max(int(duration / 0.1), 1)
            for _ in range(steps):
                if not self.ringtone_active:
                    break
                time.sleep(0.1)

    def _ringtone_fallback_loop(self):
        if self.ringtone_error:
            self.root.after(0, lambda: self._set_call_status(f"Incoming call. Ringtone fallback in use: {self.ringtone_error}"))
        while self.ringtone_active:
            try:
                import winsound

                winsound.Beep(880, 220)
                if not self.ringtone_active:
                    break
                time.sleep(0.08)
                winsound.Beep(660, 320)
            except Exception:
                try:
                    self.root.after(0, self.root.bell)
                except Exception:
                    pass
            for _ in range(9):
                if not self.ringtone_active:
                    break
                time.sleep(0.12)

    def _stop_ringtone(self):
        self.ringtone_active = False
        try:
            import winsound

            winsound.PlaySound(None, 0)
        except Exception:
            pass
        if self.ringtone_after_id is not None:
            try:
                self.root.after_cancel(self.ringtone_after_id)
            except Exception:
                pass
        self.ringtone_after_id = None
        self.ringtone_thread = None
        self.ringtone_error = None

    def _handle_duplicate_username(self):
        if self._duplicate_kick_handled:
            return

        self._duplicate_kick_handled = True
        if self.connected:
            self.disconnect()
        elif self.mqtt_client is not None:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception:
                pass
            self.mqtt_client = None

        self._show_dialog("Username in use", "That username is already online. You were disconnected.")
        self._duplicate_kick_handled = False

    def send_message(self):
        if not self.connected or self.mqtt_client is None:
            self._show_dialog("Not connected", "Connect to a chatroom before sending messages.")
            return

        text = self.message_var.get().strip()
        if not text:
            return

        full_text = f"{self.username}: {text}"
        topic = f"tallk/{self.chat_room}/chat"

        try:
            self.mqtt_client.publish(topic, full_text)
            self.append_message(full_text)
        except Exception as exc:
            self.receive_queue.put((f"[ERROR] Failed to send message: {exc}", True, None))
            self.connected = False
            self.connect_button.configure(state="normal")
            self.status_label.configure(text="Ready")
        self.message_var.set("")

    def append_message(self, message):
        self.chat_area.configure(state="normal")
        if message.startswith("[SYSTEM]"):
            self.chat_area.insert("end", message + "\n", "system")
        elif ": " in message:
            username, msg = message.split(": ", 1)
            color = self._get_color(username)
            self.chat_area.tag_configure(username, foreground=color)
            self.chat_area.insert("end", username, username)
            self.chat_area.insert("end", ": " + msg + "\n")
        else:
            self.chat_area.insert("end", message + "\n")
        self.chat_area.tag_configure("system", foreground="gray")
        self.chat_area.configure(state="disabled")
        self.chat_area.see("end")

    def _play_sound(self, kind):
        try:
            import winsound
            sound_files = {
                "call": (SOUNDS_DIR / "call_V2.wav").resolve(),
            }
            sound_path = sound_files.get(kind)
            if sound_path and sound_path.exists():
                winsound.PlaySound(str(sound_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            elif kind == "call":
                self.root.bell()
            else:
                self.root.bell()
        except Exception:
            try:
                self.root.bell()
            except Exception:
                pass

    def _process_receive_queue(self):
        while not self.receive_queue.empty():
            text, is_system, sound = self.receive_queue.get()
            self.append_message(text)
            if is_system:
                self.status_label.configure(text=text)
        self.root.after(100, self._process_receive_queue)

    def close(self):
        self.connected = False
        if self.mqtt_client is not None:
            try:
                if self.username and self.chat_room:
                    self.mqtt_client.publish(f"tallk/{self.chat_room}/presence", f"LEAVE|{self.username}|{self.session_id}|app")
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception:
                pass
        self.root.destroy()


if __name__ == "__main__":
    ChatApp()

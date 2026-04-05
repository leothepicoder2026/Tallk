import base64
from pathlib import Path
import queue
import threading
import time
import uuid
import tkinter as tk
from tkinter.scrolledtext import ScrolledText

import paho.mqtt.client as mqtt

try:
    import sounddevice as sd
except ImportError:
    sd = None

BASE_DIR = Path(__file__).resolve().parent
SOUNDS_DIR = BASE_DIR / "sounds"
DEFAULT_BROKER = "test.mosquitto.org"
DEFAULT_PORT = 1883
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_BLOCKSIZE = 2048


class ChatApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Tallk Chat")
        self.root.overrideredirect(True)
        self.root.geometry("1000x700")
        self.root.resizable(True, True)

        self.username_var = tk.StringVar(value="User")
        self.room_var = tk.StringVar(value="Tallk Servers")
        self.session_id = uuid.uuid4().hex[:8]
        self.mqtt_client = None
        self.connected = False
        self.chat_room = None
        self.username = None
        self.participants = set()
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
        self.audio_topic = None
        self.audio_streaming = False
        self.audio_input_stream = None
        self.audio_output_stream = None
        self.audio_thread = None

        self._build_interface()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._process_receive_queue)
        self.root.mainloop()

    def _build_interface(self):
        title_bar = tk.Frame(self.root, bg="#1f2937", height=34)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)

        title_label = tk.Label(title_bar, text="Tallk Chat", bg="#1f2937", fg="white", padx=10, font=("Segoe UI", 10, "bold"))
        title_label.pack(side="left")

        close_button = tk.Button(
            title_bar,
            text="X",
            command=self.close,
            bg="#1f2937",
            fg="white",
            activebackground="#dc2626",
            activeforeground="white",
            bd=0,
            padx=12,
            pady=4,
            font=("Segoe UI", 10, "bold"),
        )
        close_button.pack(side="right")

        for widget in (title_bar, title_label):
            widget.bind("<ButtonPress-1>", self._start_window_drag)
            widget.bind("<B1-Motion>", self._drag_window)

        top_frame = tk.Frame(self.root, padx=10, pady=10)
        top_frame.pack(fill="x")

        self.name_label = tk.Label(top_frame, text="Name:", width=8, anchor="w")
        self.name_label.grid(row=0, column=0)
        self.name_entry = tk.Entry(top_frame, textvariable=self.username_var, width=18)
        self.name_entry.grid(row=0, column=1, sticky="w")

        self.connect_button = tk.Button(top_frame, text="Log In", width=16, command=self.connect)
        self.connect_button.grid(row=0, column=2, sticky="e")

        self.status_label = tk.Label(self.root, text="Ready", anchor="w", padx=10)
        self.status_label.pack(fill="x")
        self.call_status_label = tk.Label(self.root, text="Click someone in the room to start a voice call.", anchor="w", padx=10, fg="#374151")
        self.call_status_label.pack(fill="x")

        main_frame = tk.Frame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=(4, 0))

        left_frame = tk.Frame(main_frame)
        left_frame.pack(side="left", fill="both", expand=True)

        chat_label = tk.Label(left_frame, text="Chat Room", anchor="w", padx=4, font=("Segoe UI", 10, "bold"))
        chat_label.pack(fill="x")

        self.chat_area = ScrolledText(left_frame, wrap="word", state="disabled", font=("Segoe UI", 10), bg="white")
        self.chat_area.pack(fill="both", expand=True, padx=(0, 6))

        input_frame = tk.Frame(left_frame)
        input_frame.pack(fill="x", pady=(6, 0))

        tk.Label(input_frame, text="Message:", width=8, anchor="w").pack(side="left")
        self.message_var = tk.StringVar()
        self.message_entry = tk.Entry(input_frame, textvariable=self.message_var)
        self.message_entry.pack(side="left", fill="x", expand=True)
        self.message_entry.bind("<Return>", lambda event: self.send_message())

        tk.Button(input_frame, text="Send", width=12, command=self.send_message).pack(side="left", padx=(6, 0))

        right_frame = tk.Frame(main_frame, width=140)
        right_frame.pack(side="right", fill="y")
        right_frame.pack_propagate(False)

        tk.Label(right_frame, text="People in room", anchor="w", padx=4, font=("Segoe UI", 10, "bold")).pack(fill="x")
        self.participants_text = ScrolledText(right_frame, state="disabled", font=("Segoe UI", 9), height=20, width=18)
        self.participants_text.pack(fill="both", expand=True, padx=(0, 0), pady=(4, 0))
        self.participants_text.bind("<Motion>", self._update_participant_hover)
        self.participants_text.bind("<Leave>", lambda event: self.participants_text.configure(cursor=""))

        self.hangup_button = tk.Button(right_frame, text="Hang Up", state="disabled", command=self._end_active_call)
        self.hangup_button.pack(fill="x", pady=(6, 0))

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

    def _show_dialog(self, title, message):
        self._stop_ringtone()
        if self._active_dialog is not None and self._active_dialog.winfo_exists():
            self._active_dialog.destroy()

        dialog = tk.Toplevel(self.root)
        dialog.overrideredirect(True)
        dialog.transient(self.root)
        dialog.configure(bg="#d1d5db", bd=1, relief="solid")
        dialog._drag_offset_x = 0
        dialog._drag_offset_y = 0
        self._active_dialog = dialog

        title_bar = tk.Frame(dialog, bg="#1f2937", height=34)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)

        title_label = tk.Label(title_bar, text=title, bg="#1f2937", fg="white", padx=10, font=("Segoe UI", 10, "bold"))
        title_label.pack(side="left")

        close_button = tk.Button(
            title_bar,
            text="X",
            command=dialog.destroy,
            bg="#1f2937",
            fg="white",
            activebackground="#dc2626",
            activeforeground="white",
            bd=0,
            padx=12,
            pady=4,
            font=("Segoe UI", 10, "bold"),
        )
        close_button.pack(side="right")

        body = tk.Frame(dialog, bg="white", padx=18, pady=16)
        body.pack(fill="both", expand=True)

        message_label = tk.Label(body, text=message, bg="white", justify="left", wraplength=300, font=("Segoe UI", 10))
        message_label.pack(anchor="w")

        ok_button = tk.Button(body, text="OK", width=10, command=dialog.destroy)
        ok_button.pack(anchor="e", pady=(14, 0))

        for widget in (title_bar, title_label):
            widget.bind("<ButtonPress-1>", lambda event, win=dialog: self._start_dialog_drag(event, win))
            widget.bind("<B1-Motion>", lambda event, win=dialog: self._drag_dialog(event, win))

        dialog.update_idletasks()
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        dialog_w = dialog.winfo_width()
        dialog_h = dialog.winfo_height()
        x = root_x + max((root_w - dialog_w) // 2, 0)
        y = root_y + max((root_h - dialog_h) // 2, 0)
        dialog.geometry(f"+{x}+{y}")
        dialog.lift()
        dialog.attributes("-topmost", True)

        focus_target = self.message_entry if self.connected else self.name_entry

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
        close_button.configure(command=close_dialog)
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
        dialog.overrideredirect(True)
        dialog.transient(self.root)
        dialog.configure(bg="#d1d5db", bd=1, relief="solid")
        dialog._drag_offset_x = 0
        dialog._drag_offset_y = 0
        self._active_dialog = dialog

        title_bar = tk.Frame(dialog, bg="#1f2937", height=34)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)

        title_label = tk.Label(title_bar, text=title, bg="#1f2937", fg="white", padx=10, font=("Segoe UI", 10, "bold"))
        title_label.pack(side="left")

        close_button = tk.Button(
            title_bar,
            text="X",
            command=dialog.destroy,
            bg="#1f2937",
            fg="white",
            activebackground="#dc2626",
            activeforeground="white",
            bd=0,
            padx=12,
            pady=4,
            font=("Segoe UI", 10, "bold"),
        )
        close_button.pack(side="right")

        body = tk.Frame(dialog, bg="white", padx=18, pady=16)
        body.pack(fill="both", expand=True)

        tk.Label(body, text=message, bg="white", justify="left", wraplength=300, font=("Segoe UI", 10)).pack(anchor="w")

        button_row = tk.Frame(body, bg="white")
        button_row.pack(anchor="e", pady=(14, 0))
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

        cancel_button = tk.Button(button_row, text="Decline", width=10, command=close_dialog)
        cancel_button.pack(side="right")

        confirm_button = tk.Button(button_row, text=confirm_text, width=10, command=confirm)
        confirm_button.pack(side="right", padx=(0, 8))

        for widget in (title_bar, title_label):
            widget.bind("<ButtonPress-1>", lambda event, win=dialog: self._start_dialog_drag(event, win))
            widget.bind("<B1-Motion>", lambda event, win=dialog: self._drag_dialog(event, win))

        dialog.update_idletasks()
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        dialog_w = dialog.winfo_width()
        dialog_h = dialog.winfo_height()
        x = root_x + max((root_w - dialog_w) // 2, 0)
        y = root_y + max((root_h - dialog_h) // 2, 0)
        dialog.geometry(f"+{x}+{y}")
        dialog.lift()
        dialog.attributes("-topmost", True)
        dialog.bind("<Escape>", close_dialog)
        dialog.bind("<Return>", confirm)
        close_button.configure(command=close_dialog)
        dialog.grab_set()
        dialog.focus_force()
        confirm_button.focus_set()

    def disconnect(self):
        if not self.connected:
            return
        self._end_active_call(notify_peer=True)
        if self.mqtt_client:
            try:
                presence_topic = f"tallk/{self.chat_room}/presence"
                self.mqtt_client.publish(presence_topic, f"LEAVE|{self.username}|{self.session_id}")
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

        self.chat_room = self.room_var.get().strip() or "Tallk Servers"
        self.room_var.set(self.chat_room)
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
        self.mqtt_client.will_set(presence_topic, f"LEAVE|{self.username}|{self.session_id}", qos=0, retain=False)
        self.participants = set()
        self._update_participants()

        try:
            self.mqtt_client.connect(broker, port, keepalive=60)
            self.mqtt_client.loop_start()
        except Exception as exc:
            self.receive_queue.put((f"[ERROR] Could not connect to broker: {exc}", True, None))
            self.connect_button.configure(state="normal")
            self.status_label.configure(text="Ready")
            self.mqtt_client = None

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            self.receive_queue.put((f"[ERROR] MQTT connect failed with code {rc}", True, None))
            self.root.after(0, lambda: self.connect_button.configure(state="normal"))
            return

        chat_topic = f"tallk/{self.chat_room}/chat"
        presence_topic = f"tallk/{self.chat_room}/presence"
        call_topic = f"tallk/{self.chat_room}/call"
        client.subscribe([(chat_topic, 0), (presence_topic, 0), (call_topic, 0)])
        self.connected = True
        self.participants = {self.username}
        self._update_participants()
        self.receive_queue.put((f"Connected to Tallk servers.", True, None))
        self.root.after(0, lambda: self.status_label.configure(text=f"Connected to {DEFAULT_BROKER}:{DEFAULT_PORT}"))
        self.root.after(0, lambda: self.connect_button.configure(state="disabled"))
        client.publish(presence_topic, f"JOIN|{self.username}|{self.session_id}")
        # Hide inputs and show disconnect
        self.name_label.grid_forget()
        self.name_entry.grid_forget()
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
        self.participants = set()
        self._update_participants()
        self.receive_queue.put((f"Disconnected from Tallk servers.", True, None))
        self.root.after(0, lambda: self.connect_button.configure(text="Log In", command=self.connect, state="normal"))
        self.root.after(0, lambda: self.status_label.configure(text="Ready"))
        self.root.after(0, lambda: self._set_call_status("Click someone in the room to start a voice call."))
        # Restore inputs
        self.name_label.grid(row=0, column=0)
        self.name_entry.grid(row=0, column=1, sticky="w")
        self.connect_button.grid(row=0, column=2, sticky="e")

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
                    client.publish(presence_topic, f"KICK|{username}|{sender_session}")
                    return
                if username != self.username:
                    presence_topic = f"tallk/{self.chat_room}/presence"
                    client.publish(presence_topic, f"HERE|{self.username}|{self.session_id}")
                self.participants.add(username)
                self._update_participants()
                self.receive_queue.put((f"{username} went online.", True, None))
            elif action == "HERE":
                if sender_session == self.session_id:
                    return
                if username == self.username and sender_session:
                    self.root.after(0, self._handle_duplicate_username)
                    return
                self.participants.add(username)
                self._update_participants()
            elif action == "LEAVE":
                if sender_session == self.session_id:
                    return
                self.participants.discard(username)
                self._update_participants()
                if username == self.active_call_peer:
                    self.root.after(0, lambda: self._end_active_call(notify_peer=False, reason=f"{username} left the room."))
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
        for username in sorted(self.participants):
            color = self._get_color(username)
            self.participants_text.insert("end", username + "\n", (username,))
            self.participants_text.tag_configure(username, foreground=color)
            self.participants_text.tag_bind(username, "<Button-1>", lambda event, name=username: self._call_participant(name))
        self.participants_text.configure(state="disabled")

    def _update_participant_hover(self, event):
        index = self.participants_text.index(f"@{event.x},{event.y}")
        tags = [tag for tag in self.participants_text.tag_names(index) if tag in self.participants]
        clickable = any(tag != self.username for tag in tags)
        self.participants_text.configure(cursor="hand2" if clickable else "")

    def _set_call_status(self, text):
        self.call_status_label.configure(text=text)
        self.hangup_button.configure(state="normal" if self.active_call_id else "disabled")

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
        self.root.after(0, lambda: self._show_choice_dialog("Incoming call", f"{caller_name} wants to voice call you.", "Accept", accept_call, on_cancel=decline_call, stop_ringtone=False))

    def _begin_call(self, call_id, peer_name, peer_session):
        self.active_call_id = call_id
        self.active_call_peer = peer_name
        self.active_call_peer_session = peer_session
        self.audio_topic = f"tallk/{self.chat_room}/call-audio/{call_id}"
        if self.mqtt_client is not None:
            self.mqtt_client.subscribe([(self.audio_topic, 0)])
        self._set_call_status(f"In voice call with {peer_name}.")
        self._start_audio_streams()

    def _end_active_call(self, notify_peer=True, reason="Call ended."):
        ended_call_id = self.active_call_id
        peer_name = self.active_call_peer
        peer_session = self.active_call_peer_session

        if notify_peer and ended_call_id and peer_name:
            self._publish_call_control("END", ended_call_id, peer_name, peer_session)

        self._clear_pending_call_timeout()
        self._stop_ringtone()
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
        try:
            import winsound

            call_sound = (SOUNDS_DIR / "call.wav").resolve()
            if call_sound.exists():
                winsound.PlaySound(str(call_sound), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
                return
        except Exception:
            pass

        self.ringtone_thread = threading.Thread(target=self._ringtone_fallback_loop, daemon=True)
        self.ringtone_thread.start()

    def _ringtone_fallback_loop(self):
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

    def _join_selected_room(self):
        selection = self.rooms_listbox.curselection()
        if selection:
            self.room_var.set(self.rooms_listbox.get(selection[0]))
            if not self.connected:
                self.connect()

    def _refresh_active_rooms(self):
        self._update_active_rooms()
        self.root.after(1000, self._refresh_active_rooms)

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
                "call": (SOUNDS_DIR / "call.wav").resolve(),
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
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception:
                pass
        self.root.destroy()


if __name__ == "__main__":
    ChatApp()

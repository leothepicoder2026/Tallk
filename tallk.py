import queue
import uuid
import tkinter as tk
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText

import paho.mqtt.client as mqtt


class ChatApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Tallk Chat")
        self.root.geometry("1000x700")
        self.root.resizable(True, True)

        self.username_var = tk.StringVar(value="User")
        self.room_var = tk.StringVar(value="Tallk MainChatroom")
        self.broker_host_var = tk.StringVar(value="test.mosquitto.org")
        self.broker_port_var = tk.IntVar(value=1883)
        self.advanced_shown = False

        self.mqtt_client = None
        self.connected = False
        self.chat_room = None
        self.username = None
        self.participants = set()
        self.receive_queue = queue.Queue()

        self._build_interface()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._process_receive_queue)
        self.root.mainloop()

    def _build_interface(self):
        top_frame = tk.Frame(self.root, padx=10, pady=10)
        top_frame.pack(fill="x")

        self.name_label = tk.Label(top_frame, text="Name:", width=8, anchor="w")
        self.name_label.grid(row=0, column=0)
        self.name_entry = tk.Entry(top_frame, textvariable=self.username_var, width=18)
        self.name_entry.grid(row=0, column=1, sticky="w")

        self.advanced_button = tk.Button(top_frame, text="Show Advanced Options", command=self._toggle_advanced)
        self.advanced_button.grid(row=0, column=2, padx=(10, 0))

        self.connect_button = tk.Button(top_frame, text="Connect", width=16, command=self.connect)
        self.connect_button.grid(row=0, column=3, sticky="e")

        self.advanced_frame = tk.Frame(top_frame)
        tk.Label(self.advanced_frame, text="Broker:", width=8, anchor="w").grid(row=0, column=0)
        tk.Entry(self.advanced_frame, textvariable=self.broker_host_var, width=24).grid(row=0, column=1, sticky="w")

        tk.Label(self.advanced_frame, text="Port:", width=8, anchor="w").grid(row=1, column=0)
        tk.Entry(self.advanced_frame, textvariable=self.broker_port_var, width=24).grid(row=1, column=1, sticky="w")

        # Initially hide advanced frame
        self.advanced_frame.grid_forget()

        self.status_label = tk.Label(self.root, text="Ready", anchor="w", padx=10)
        self.status_label.pack(fill="x")

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

    def _get_color(self, username):
        colors = ["#1e90ff", "#32cd32", "#dc143c", "#9370db", "#ff8c00", "#8b4513", "#00ced1", "#ff69b4"]
        return colors[hash(username) % len(colors)]

    def _toggle_advanced(self):
        if self.advanced_shown:
            self.advanced_frame.grid_forget()
            self.advanced_button.configure(text="Show Advanced Options")
            self.advanced_shown = False
        else:
            self.advanced_frame.grid(row=2, column=0, columnspan=4, pady=(10, 0))
            self.advanced_button.configure(text="Hide Advanced Options")
            self.advanced_shown = True

    def disconnect(self):
        if not self.connected:
            return
        if self.mqtt_client:
            try:
                presence_topic = f"tallk/{self.chat_room}/presence"
                self.mqtt_client.publish(presence_topic, f"LEAVE|{self.username}")
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception:
                pass
        self._on_disconnect(None, None, 0)

    def connect(self):
        if self.connected:
            messagebox.showinfo("Already connected", "You are already connected to a chatroom.")
            return

        self.username = self.username_var.get().strip()
        if not self.username:
            messagebox.showwarning("Missing name", "Please enter a display name before connecting.")
            return

        self.chat_room = self.room_var.get().strip() or "Tallk MainChatroom"
        self.room_var.set(self.chat_room)

        broker = self.broker_host_var.get().strip() or "test.mosquitto.org"
        port = self.broker_port_var.get()
        if port <= 0 or port > 65535:
            messagebox.showwarning("Invalid port", "Please choose a port number between 1 and 65535.")
            return

        self.append_message(f"[SYSTEM] Connecting to broker {broker}:{port} and joining Tallk servers...")
        self.status_label.configure(text=f"Connecting to {broker}:{port}...")
        self.connect_button.configure(state="disabled")

        client_id = f"tallk-{uuid.uuid4().hex[:8]}"
        self.mqtt_client = mqtt.Client(client_id=client_id)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_disconnect = self._on_disconnect

        presence_topic = f"tallk/{self.chat_room}/presence"
        self.mqtt_client.will_set(presence_topic, f"LEAVE|{self.username}", qos=0, retain=False)
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
        client.subscribe([(chat_topic, 0), (presence_topic, 0)])
        self.connected = True
        self.participants = {self.username}
        self._update_participants()
        self.receive_queue.put((f"[SYSTEM] Connected to Tallk servers.", True, None))
        self.root.after(0, lambda: self.status_label.configure(text=f"Connected to {self.broker_host_var.get()}:{self.broker_port_var.get()}"))
        self.root.after(0, lambda: self.connect_button.configure(state="disabled"))
        client.publish(presence_topic, f"JOIN|{self.username}")
        # Hide inputs and show disconnect
        self.name_label.grid_forget()
        self.name_entry.grid_forget()
        self.advanced_button.grid_forget()
        self.advanced_frame.grid_forget()
        self.connect_button.configure(text="Disconnect", command=self.disconnect, state="normal")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        self.participants = set()
        self._update_participants()
        self.receive_queue.put(("[SYSTEM] Disconnected from broker.", True, None))
        self.root.after(0, lambda: self.connect_button.configure(text="Connect", command=self.connect, state="normal"))
        self.root.after(0, lambda: self.status_label.configure(text="Ready"))
        # Restore inputs
        self.name_label.grid(row=0, column=0)
        self.name_entry.grid(row=0, column=1, sticky="w")
        self.advanced_button.grid(row=0, column=2, padx=(10, 0))
        self.connect_button.grid(row=0, column=3, sticky="e")

    def _on_message(self, client, userdata, message):
        try:
            payload = message.payload.decode("utf-8", errors="replace")
        except Exception as exc:
            payload = f"[ERROR] Failed to decode message: {exc}"

        topic = message.topic
        if topic.endswith("/presence"):
            if payload.startswith("JOIN|"):
                username = payload.split("|", 1)[1]
                if username != self.username:
                    presence_topic = f"tallk/{self.chat_room}/presence"
                    client.publish(presence_topic, f"HERE|{self.username}")
                self.participants.add(username)
                self._update_participants()
                self.receive_queue.put((f"[SYSTEM] {username} went online.", True, None))
            elif payload.startswith("HERE|"):
                username = payload.split("|", 1)[1]
                self.participants.add(username)
                self._update_participants()
            elif payload.startswith("LEAVE|"):
                username = payload.split("|", 1)[1]
                self.participants.discard(username)
                self._update_participants()
                self.receive_queue.put((f"[SYSTEM] {username} went offline.", True, None))
            return

        self.receive_queue.put((payload, False, "receive"))

    def _update_participants(self):
        self.participants_text.configure(state="normal")
        self.participants_text.delete(1.0, "end")
        for username in sorted(self.participants):
            color = self._get_color(username)
            self.participants_text.insert("end", username + "\n", (username,))
            self.participants_text.tag_configure(username, foreground=color)
        self.participants_text.configure(state="disabled")

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
            messagebox.showwarning("Not connected", "Connect to a chatroom before sending messages.")
            return

        text = self.message_var.get().strip()
        if not text:
            return

        full_text = f"{self.username}: {text}"
        topic = f"tallk/{self.chat_room}/chat"

        try:
            self.mqtt_client.publish(topic, full_text)
            self.append_message(full_text)
            self._play_sound("send")
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
            if kind == "send":
                winsound.Beep(1000, 80)
            elif kind == "receive":
                winsound.Beep(1400, 70)
        except Exception:
            self.root.bell()

    def _process_receive_queue(self):
        while not self.receive_queue.empty():
            text, is_system, sound = self.receive_queue.get()
            self.append_message(text)
            if is_system:
                self.status_label.configure(text=text)
            if sound == "receive":
                self._play_sound("receive")
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

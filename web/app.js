const FIXED_ROOM = "Tallk Servers";
const DEFAULT_BROKER = "test.mosquitto.org";
const WS_BROKER_URL = "wss://test.mosquitto.org:8081/mqtt";
const AUDIO_SAMPLE_RATE = 16000;
const AUDIO_CHANNELS = 1;
const AUDIO_BLOCKSIZE = 2048;
const CALL_TIMEOUT_MS = 20000;
const COLORS = ["#1e90ff", "#32cd32", "#dc143c", "#9370db", "#ff8c00", "#8b4513", "#00ced1", "#ff69b4"];

const els = {
  loginButton: document.querySelector("#loginButton"),
  statusText: document.querySelector("#statusText"),
  callStatusText: document.querySelector("#callStatusText"),
  roomValue: document.querySelector("#roomValue"),
  serverValue: document.querySelector("#serverValue"),
  onlineCount: document.querySelector("#onlineCount"),
  chatArea: document.querySelector("#chatArea"),
  messageForm: document.querySelector("#messageForm"),
  messageInput: document.querySelector("#messageInput"),
  participantsList: document.querySelector("#participantsList"),
  modalRoot: document.querySelector("#modalRoot"),
  ringtoneAudio: document.querySelector("#ringtoneAudio"),
};

class TallkWebApp {
  constructor() {
    this.username = "";
    this.sessionId = crypto.randomUUID().replace(/-/g, "").slice(0, 8);
    this.chatRoom = FIXED_ROOM;
    this.client = null;
    this.connected = false;
    this.participantRoles = new Map();
    this.usernameColors = new Map();
    this.modalCleanup = null;
    this.pendingCallId = null;
    this.pendingCallTimer = null;
    this.activeCallId = null;
    this.activeCallPeer = null;
    this.activeCallPeerSession = null;
    this.audioTopic = null;
    this.audioContext = null;
    this.captureStream = null;
    this.captureSource = null;
    this.captureProcessor = null;
    this.playbackGain = null;
    this.playbackCursor = 0;
    this.duplicateKickHandled = false;

    this.bindEvents();
    this.updateOverview();
    this.renderParticipants();
    this.showLoginDialog();
  }

  bindEvents() {
    els.loginButton.addEventListener("click", () => {
      if (this.connected) {
        this.disconnect();
      } else {
        this.showLoginDialog();
      }
    });

    els.messageForm.addEventListener("submit", (event) => {
      event.preventDefault();
      this.sendMessage();
    });
  }

  appendMessage(message, isSystem = false) {
    const line = document.createElement("div");
    line.className = `chat-line${isSystem ? " system" : ""}`;

    if (!isSystem && message.includes(": ")) {
      const separatorIndex = message.indexOf(": ");
      const username = message.slice(0, separatorIndex);
      const body = message.slice(separatorIndex + 2);
      const name = document.createElement("span");
      name.className = "chat-user";
      name.style.color = this.getColor(username);
      name.textContent = username;
      line.append(name, document.createTextNode(`: ${body}`));
    } else {
      line.textContent = message;
    }

    els.chatArea.append(line);
    els.chatArea.scrollTop = els.chatArea.scrollHeight;
  }

  setStatus(text) {
    els.statusText.textContent = text;
  }

  setCallStatus(text) {
    els.callStatusText.textContent = text;
  }

  updateOverview() {
    if (els.roomValue) {
      els.roomValue.textContent = this.chatRoom;
    }
    if (els.serverValue) {
      els.serverValue.textContent = DEFAULT_BROKER;
    }
    if (els.onlineCount) {
      const count = this.participantRoles.size;
      els.onlineCount.textContent = `${count} ${count === 1 ? "person" : "people"}`;
    }
  }

  getColor(username) {
    if (!this.usernameColors.has(username)) {
      const seed = [...username].reduce((sum, char) => sum + char.charCodeAt(0), 0);
      this.usernameColors.set(username, COLORS[seed % COLORS.length]);
    }
    return this.usernameColors.get(username);
  }

  setParticipantRole(username, role, present = true) {
    if (!username) {
      return;
    }
    const roles = this.participantRoles.get(username) ?? new Set();
    if (present) {
      roles.add(role);
      this.participantRoles.set(username, roles);
    } else {
      roles.delete(role);
      if (roles.size === 0) {
        this.participantRoles.delete(username);
      } else {
        this.participantRoles.set(username, roles);
      }
    }
  }

  renderParticipants() {
    els.participantsList.textContent = "";
    const names = [...this.participantRoles.keys()].sort((a, b) => a.localeCompare(b));

    for (const username of names) {
      const button = document.createElement("button");
      button.className = "participant";
      button.type = "button";
      button.disabled = username === this.username;
      button.addEventListener("click", () => this.callParticipant(username));

      const dot = document.createElement("span");
      dot.className = "participant-dot";
      dot.style.background = this.getColor(username);

      const label = document.createElement("span");
      label.textContent = username;

      button.append(dot, label);
      els.participantsList.append(button);
    }

    this.updateOverview();
  }

  showModal(content) {
    this.closeModal();
    const overlay = document.createElement("div");
    overlay.className = "overlay";
    overlay.append(content);
    els.modalRoot.replaceChildren(overlay);
    this.modalCleanup = () => {
      overlay.remove();
      if (els.modalRoot.childElementCount === 0) {
        els.modalRoot.textContent = "";
      }
    };
  }

  closeModal() {
    if (this.modalCleanup) {
      this.modalCleanup();
      this.modalCleanup = null;
    }
  }

  showInfoDialog(title, message, heroClass = "") {
    const dialog = document.createElement("div");
    dialog.className = "dialog";
    dialog.innerHTML = `
      <div class="dialog-hero ${heroClass}">
        <h2 class="dialog-title">${escapeHtml(title)}</h2>
        <p class="dialog-subtitle">Tallk notice</p>
      </div>
      <div class="dialog-body">${escapeHtml(message)}</div>
      <div class="dialog-actions">
        <button class="primary-button" type="button">OK</button>
      </div>
    `;
    dialog.querySelector("button").addEventListener("click", () => this.closeModal());
    this.showModal(dialog);
  }

  showLoginDialog() {
    const dialog = document.createElement("div");
    dialog.className = "dialog";
    dialog.innerHTML = `
      <div class="dialog-hero">
        <h2 class="dialog-title">Welcome Back</h2>
        <p class="dialog-subtitle">Jump straight into the Tallk room.</p>
      </div>
      <form class="dialog-body" id="loginForm">
        <label for="loginName">Display Name</label>
        <input id="loginName" type="text" autocomplete="nickname" placeholder="Your name">
      </form>
      <div class="dialog-actions">
        <button class="secondary-button" type="button" id="loginCancel">Cancel</button>
        <button class="primary-button" type="button" id="loginSubmit">Log In</button>
      </div>
    `;

    const input = dialog.querySelector("#loginName");
    input.value = this.username;
    dialog.querySelector("#loginCancel").addEventListener("click", () => this.closeModal());
    dialog.querySelector("#loginSubmit").addEventListener("click", () => {
      this.username = input.value.trim();
      this.closeModal();
      this.connect();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        dialog.querySelector("#loginSubmit").click();
      }
    });

    this.showModal(dialog);
    queueMicrotask(() => input.focus());
  }

  connect() {
    if (this.connected) {
      this.showInfoDialog("Already online", "You are already online.");
      return;
    }
    if (!this.username) {
      this.showInfoDialog("Missing name", "Please enter a display name before connecting.");
      return;
    }

    this.appendMessage("Connecting to Tallk servers...");
    this.setStatus(`Connecting to ${DEFAULT_BROKER}...`);
    els.loginButton.disabled = true;
    this.duplicateKickHandled = false;
    this.participantRoles.clear();
    this.renderParticipants();

    this.client = window.mqtt.connect(WS_BROKER_URL, {
      clientId: `tallk-${this.sessionId}`,
      clean: true,
      keepalive: 60,
      reconnectPeriod: 0,
      will: {
        topic: this.presenceTopic,
        payload: `LEAVE|${this.username}|${this.sessionId}|app`,
        qos: 0,
        retain: false,
      },
    });

    this.client.on("connect", () => this.handleConnect());
    this.client.on("message", (topic, payload) => this.handleMessage(topic, payload));
    this.client.on("close", () => this.handleDisconnect());
    this.client.on("error", (error) => {
      this.appendMessage(`[ERROR] Could not connect to broker: ${error.message}`, true);
      this.setStatus("Ready");
      els.loginButton.disabled = false;
      this.client?.end(true);
      this.client = null;
    });
  }

  handleConnect() {
    const topics = [this.chatTopic, this.presenceTopic, this.callTopic];
    this.client.subscribe(topics, (error) => {
      if (error) {
        this.appendMessage(`[ERROR] MQTT subscribe failed: ${error.message}`, true);
        this.disconnect();
        return;
      }

      this.connected = true;
      this.setParticipantRole(this.username, "app", true);
      this.renderParticipants();
      this.appendMessage("Connected to Tallk servers.", true);
      this.setStatus(`Connected to ${DEFAULT_BROKER}`);
      els.loginButton.textContent = "Disconnect";
      els.loginButton.disabled = false;
      this.client.publish(this.presenceTopic, `JOIN|${this.username}|${this.sessionId}|app`);
    });
  }

  handleDisconnect() {
    if (!this.client && !this.connected) {
      return;
    }

    this.stopAudioStreams();
    this.stopRingtone();
    this.clearPendingCallTimeout();
    this.activeCallId = null;
    this.activeCallPeer = null;
    this.activeCallPeerSession = null;
    this.pendingCallId = null;
    this.audioTopic = null;
    this.connected = false;
    this.participantRoles.clear();
    this.renderParticipants();
    this.closeModal();
    this.appendMessage("Disconnected from Tallk servers.", true);
    this.setStatus("Ready");
    this.setCallStatus("Click someone in the room to start a voice call.");
    els.loginButton.textContent = "Log In";
    els.loginButton.disabled = false;
    this.client = null;
  }

  disconnect() {
    if (!this.connected || !this.client) {
      return;
    }

    this.endActiveCall(true);
    this.client.publish(this.presenceTopic, `LEAVE|${this.username}|${this.sessionId}|app`);
    this.client.end();
  }

  handleMessage(topic, payloadBuffer) {
    const payload = payloadBuffer.toString();

    if (topic === this.presenceTopic) {
      this.handlePresenceMessage(payload);
      return;
    }

    if (topic === this.callTopic) {
      this.handleCallMessage(payload);
      return;
    }

    if (this.audioTopic && topic === this.audioTopic) {
      this.handleAudioMessage(payload);
      return;
    }

    if (topic.includes("/call-audio/")) {
      return;
    }

    this.appendMessage(payload, false);
  }

  handlePresenceMessage(payload) {
    const [action = "", username = "", senderSession = "", senderRole = "app"] = payload.split("|");

    if (action === "JOIN") {
      if (senderSession === this.sessionId) {
        return;
      }
      if (username === this.username && senderSession) {
        this.publishPresence("KICK", username, senderSession, senderRole);
        return;
      }
      if (username !== this.username) {
        this.publishPresence("HERE", this.username, this.sessionId, "app");
      }
      this.setParticipantRole(username, senderRole, true);
      this.renderParticipants();
      this.appendMessage(`${username} is available.`, true);
      return;
    }

    if (action === "HERE") {
      if (senderSession === this.sessionId) {
        return;
      }
      this.setParticipantRole(username, senderRole, true);
      this.renderParticipants();
      return;
    }

    if (action === "LEAVE") {
      if (senderSession === this.sessionId) {
        return;
      }
      this.setParticipantRole(username, senderRole, false);
      this.renderParticipants();
      if (username === this.activeCallPeer) {
        this.endActiveCall(false, `${username} left the room.`);
      }
      if (!this.participantRoles.has(username)) {
        this.appendMessage(`${username} went offline.`, true);
      }
      return;
    }

    if (action === "KICK" && username === this.username && senderSession === this.sessionId) {
      this.handleDuplicateUsername();
    }
  }

  handleDuplicateUsername() {
    if (this.duplicateKickHandled) {
      return;
    }
    this.duplicateKickHandled = true;
    if (this.connected) {
      this.disconnect();
    } else if (this.client) {
      this.client.end(true);
      this.client = null;
    }
    this.showInfoDialog("Username in use", "That username is already online. You were disconnected.");
  }

  publishPresence(action, username, sessionId, role) {
    if (!this.client) {
      return;
    }
    this.client.publish(this.presenceTopic, `${action}|${username}|${sessionId}|${role}`);
  }

  sendMessage() {
    if (!this.connected || !this.client) {
      this.showInfoDialog("Not connected", "Connect to a chatroom before sending messages.");
      return;
    }

    const text = els.messageInput.value.trim();
    if (!text) {
      return;
    }

    const fullText = `${this.username}: ${text}`;
    this.client.publish(this.chatTopic, fullText);
    this.appendMessage(fullText);
    els.messageInput.value = "";
  }

  callParticipant(username) {
    if (username === this.username) {
      return;
    }
    if (!this.connected || !this.client) {
      this.showInfoDialog("Not connected", "Connect to a chatroom before starting a voice call.");
      return;
    }
    if (this.activeCallId || this.pendingCallId) {
      this.showInfoDialog("Call in progress", "Finish the current call before starting another one.");
      return;
    }

    const callId = crypto.randomUUID().replace(/-/g, "").slice(0, 10);
    this.pendingCallId = callId;
    this.setCallStatus(`Calling ${username}...`);
    this.publishCallControl("REQUEST", callId, username, "");
    this.schedulePendingCallTimeout(callId, username);
  }

  publishCallControl(action, callId, targetName = "", targetSession = "") {
    if (!this.connected || !this.client) {
      return;
    }
    const payload = [action, callId, this.username || "", this.sessionId, targetName || "", targetSession || ""].join("|");
    this.client.publish(this.callTopic, payload);
  }

  handleCallMessage(payload) {
    const [action = "", callId = "", senderName = "", senderSession = "", targetName = ""] = payload.split("|");
    if (senderSession === this.sessionId) {
      return;
    }

    if (action === "REQUEST" && targetName === this.username) {
      this.handleIncomingCall(callId, senderName, senderSession);
      return;
    }
    if (action === "ACCEPT" && targetName === this.username && callId === this.pendingCallId) {
      this.clearPendingCallTimeout();
      this.pendingCallId = null;
      void this.beginCall(callId, senderName, senderSession);
      return;
    }
    if (action === "DECLINE" && targetName === this.username && callId === this.pendingCallId) {
      this.clearPendingCallTimeout();
      this.pendingCallId = null;
      this.setCallStatus(`${senderName} declined your call.`);
      return;
    }
    if (action === "END" && targetName === this.username && callId === this.activeCallId) {
      this.endActiveCall(false, `${senderName} ended the call.`);
    }
  }

  handleIncomingCall(callId, callerName, callerSession) {
    if (this.activeCallId || this.pendingCallId) {
      this.publishCallControl("DECLINE", callId, callerName, callerSession);
      return;
    }

    this.startRingtone();

    const dialog = document.createElement("div");
    dialog.className = "dialog";
    dialog.innerHTML = `
      <div class="dialog-hero incoming">
        <div class="avatar">${escapeHtml((callerName[0] || "?").toUpperCase())}</div>
        <h2 class="dialog-title">${escapeHtml(callerName)}</h2>
        <p class="dialog-subtitle">Incoming voice call</p>
      </div>
      <div class="dialog-body">Answer now or decline the call.</div>
      <div class="dialog-actions">
        <button class="danger-button" type="button" id="declineCall">Decline</button>
        <button class="primary-button" type="button" id="acceptCall">Accept</button>
      </div>
    `;

    const decline = () => {
      this.stopRingtone();
      this.closeModal();
      this.publishCallControl("DECLINE", callId, callerName, callerSession);
    };

    dialog.querySelector("#declineCall").addEventListener("click", decline);
    dialog.querySelector("#acceptCall").addEventListener("click", async () => {
      this.stopRingtone();
      this.closeModal();
      this.publishCallControl("ACCEPT", callId, callerName, callerSession);
      await this.beginCall(callId, callerName, callerSession);
    });

    this.showModal(dialog);
  }

  async beginCall(callId, peerName, peerSession) {
    this.activeCallId = callId;
    this.activeCallPeer = peerName;
    this.activeCallPeerSession = peerSession;
    this.audioTopic = `tallk/${this.chatRoom}/call-audio/${callId}`;
    this.client.subscribe(this.audioTopic);
    this.setCallStatus(`In voice call with ${peerName}.`);
    this.showActiveCallDialog(peerName);

    try {
      await this.startAudioStreams();
    } catch (error) {
      this.endActiveCall(true, "Voice call failed to start.");
      this.showInfoDialog("Audio error", `Voice call setup failed: ${error.message}`);
    }
  }

  showActiveCallDialog(peerName) {
    const dialog = document.createElement("div");
    dialog.className = "dialog";
    dialog.innerHTML = `
      <div class="dialog-hero calling">
        <div class="avatar">${escapeHtml((peerName[0] || "?").toUpperCase())}</div>
        <h2 class="dialog-title">${escapeHtml(peerName)}</h2>
        <p class="dialog-subtitle">Live voice call</p>
      </div>
      <div class="dialog-body">Connection is active.</div>
      <div class="dialog-actions">
        <button class="danger-button" type="button">Hang Up</button>
      </div>
    `;

    dialog.querySelector("button").addEventListener("click", () => this.endActiveCall(true));
    this.showModal(dialog);
  }

  endActiveCall(notifyPeer = true, reason = "Call ended.") {
    const endedCallId = this.activeCallId;
    const peerName = this.activeCallPeer;
    const peerSession = this.activeCallPeerSession;

    if (notifyPeer && endedCallId && peerName) {
      this.publishCallControl("END", endedCallId, peerName, peerSession);
    }

    this.clearPendingCallTimeout();
    this.stopRingtone();
    this.stopAudioStreams();

    if (this.client && this.audioTopic) {
      this.client.unsubscribe(this.audioTopic);
    }

    this.activeCallId = null;
    this.activeCallPeer = null;
    this.activeCallPeerSession = null;
    this.pendingCallId = null;
    this.audioTopic = null;
    this.closeModal();
    this.setCallStatus(reason);
  }

  schedulePendingCallTimeout(callId, username) {
    this.clearPendingCallTimeout();
    this.pendingCallTimer = window.setTimeout(() => {
      if (this.pendingCallId === callId) {
        this.pendingCallId = null;
        this.setCallStatus(`${username} did not answer.`);
      }
    }, CALL_TIMEOUT_MS);
  }

  clearPendingCallTimeout() {
    if (this.pendingCallTimer) {
      window.clearTimeout(this.pendingCallTimer);
      this.pendingCallTimer = null;
    }
  }

  startRingtone() {
    els.ringtoneAudio.currentTime = 0;
    els.ringtoneAudio.play().catch(() => {
      this.setCallStatus("Incoming call. Interact with the page if the ringtone is blocked.");
    });
  }

  stopRingtone() {
    els.ringtoneAudio.pause();
    els.ringtoneAudio.currentTime = 0;
  }

  async startAudioStreams() {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Microphone access is not supported in this browser.");
    }

    this.audioContext = new AudioContext({ sampleRate: AUDIO_SAMPLE_RATE });
    await this.audioContext.resume();
    this.playbackGain = this.audioContext.createGain();
    this.playbackGain.connect(this.audioContext.destination);
    this.playbackCursor = this.audioContext.currentTime;

    this.captureStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: AUDIO_CHANNELS,
        noiseSuppression: true,
        echoCancellation: true,
        autoGainControl: true,
      },
      video: false,
    });

    this.captureSource = this.audioContext.createMediaStreamSource(this.captureStream);
    this.captureProcessor = this.audioContext.createScriptProcessor(AUDIO_BLOCKSIZE, AUDIO_CHANNELS, AUDIO_CHANNELS);
    this.captureProcessor.onaudioprocess = (event) => {
      if (!this.client || !this.audioTopic || !this.activeCallId) {
        return;
      }
      const input = event.inputBuffer.getChannelData(0);
      const pcm = floatTo16BitPCM(input);
      const payload = ["AUDIO", this.activeCallId, this.username, this.sessionId, bytesToBase64(pcm)].join("|");
      this.client.publish(this.audioTopic, payload);
    };

    this.captureSource.connect(this.captureProcessor);
    this.captureProcessor.connect(this.audioContext.destination);
  }

  stopAudioStreams() {
    if (this.captureProcessor) {
      this.captureProcessor.disconnect();
      this.captureProcessor.onaudioprocess = null;
      this.captureProcessor = null;
    }
    if (this.captureSource) {
      this.captureSource.disconnect();
      this.captureSource = null;
    }
    if (this.captureStream) {
      for (const track of this.captureStream.getTracks()) {
        track.stop();
      }
      this.captureStream = null;
    }
    if (this.audioContext) {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }
    this.playbackGain = null;
    this.playbackCursor = 0;
  }

  handleAudioMessage(payload) {
    if (!this.activeCallId || !this.audioContext || !this.playbackGain) {
      return;
    }

    const [kind = "", callId = "", senderName = "", senderSession = "", encodedAudio = ""] = payload.split("|");
    if (kind !== "AUDIO" || callId !== this.activeCallId || senderSession === this.sessionId) {
      return;
    }
    if (senderName !== this.activeCallPeer) {
      return;
    }

    const pcmBytes = base64ToBytes(encodedAudio);
    const floatSamples = int16BytesToFloat32(pcmBytes);
    const buffer = this.audioContext.createBuffer(1, floatSamples.length, AUDIO_SAMPLE_RATE);
    buffer.copyToChannel(floatSamples, 0);

    const source = this.audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(this.playbackGain);

    const now = this.audioContext.currentTime;
    const startAt = Math.max(now, this.playbackCursor);
    source.start(startAt);
    this.playbackCursor = startAt + buffer.duration;
  }

  get chatTopic() {
    return `tallk/${this.chatRoom}/chat`;
  }

  get presenceTopic() {
    return `tallk/${this.chatRoom}/presence`;
  }

  get callTopic() {
    return `tallk/${this.chatRoom}/call`;
  }
}

function floatTo16BitPCM(float32Array) {
  const buffer = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buffer);
  for (let index = 0; index < float32Array.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, float32Array[index]));
    view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return new Uint8Array(buffer);
}

function int16BytesToFloat32(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const samples = new Float32Array(bytes.byteLength / 2);
  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = view.getInt16(index * 2, true) / 0x8000;
  }
  return samples;
}

function bytesToBase64(bytes) {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

function base64ToBytes(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function loadMqttScript() {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://cdn.jsdelivr.net/npm/mqtt@5.10.4/dist/mqtt.min.js";
    script.onload = resolve;
    script.onerror = () => reject(new Error("Failed to load the MQTT browser client."));
    document.head.append(script);
  });
}

loadMqttScript()
  .then(() => {
    window.tallkApp = new TallkWebApp();
  })
  .catch((error) => {
    els.statusText.textContent = error.message;
    els.callStatusText.textContent = "The app could not finish loading.";
  });

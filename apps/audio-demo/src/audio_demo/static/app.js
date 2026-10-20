const $ = (id) => document.getElementById(id);
let current = null;

function status(text) { $("status").textContent = text; }
function error(text) { $("error").textContent = text; $("error").hidden = !text; }

function send(call, message) {
  if (call.socket?.readyState === WebSocket.OPEN) call.socket.send(JSON.stringify(message));
}

function finish(call, message = "Call ended") {
  if (current !== call) return;
  current = null;
  call.source?.stop();
  call.stream?.getTracks().forEach((track) => track.stop());
  call.capture?.disconnect();
  call.context?.close().catch(() => {});
  call.socket?.close();
  $("start").disabled = false;
  $("stop").disabled = true;
  $("mute").disabled = true;
  $("mute").textContent = "Mute microphone";
  $("mute").setAttribute("aria-pressed", "false");
  document.body.dataset.active = "false";
  status(message);
}

function caption(call, event) {
  $("empty")?.remove();
  let turn = call.turns.get(event.id);
  if (!turn) {
    turn = document.createElement("div");
    turn.className = `turn ${event.speaker}`;
    const label = document.createElement("span");
    label.className = "speaker";
    label.textContent = event.speaker === "user" ? "You" : "Assistant";
    turn.append(label, document.createElement("p"));
    call.turns.set(event.id, turn);
    $("transcript").append(turn);
  }
  turn.querySelector("p").textContent = event.text;
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

function play(call, event) {
  const bytes = Uint8Array.from(atob(event.data), (c) => c.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  const buffer = call.context.createBuffer(1, bytes.length / 2, call.outputRate);
  const channel = buffer.getChannelData(0);
  for (let i = 0; i < channel.length; i++) channel[i] = view.getInt16(i * 2, true) / 32768;
  const source = call.context.createBufferSource();
  source.buffer = buffer;
  source.connect(call.context.destination);
  call.source = source;
  call.playing = { id: event.id, bytes: bytes.length, started: call.context.currentTime };
  source.onended = () => {
    if (current !== call || call.source !== source) return;
    send(call, { type: "played", id: event.id, played_bytes: bytes.length });
    call.source = null;
    call.playing = null;
    status("Listening — you can speak anytime");
  };
  source.start();
  status("Assistant speaking — interrupt anytime");
}

function clearAudio(call, event) {
  const playing = call.playing;
  if (!playing || playing.id !== event.id) return; // natural end already acknowledged
  const samples = Math.floor(Math.max(0, call.context.currentTime - playing.started) * call.outputRate);
  const played = Math.min(playing.bytes, samples * 2);
  call.source.onended = null;
  call.source.stop();
  call.source = null;
  call.playing = null;
  send(call, { type: "played", id: event.id, played_bytes: played });
}

async function prepareAudio(call, event) {
  call.outputRate = event.output_sample_rate;
  await call.context.audioWorklet.addModule("/static/pcm-worklet.js");
  if (current !== call) return;
  call.capture = new AudioWorkletNode(call.context, "pcm-capture", {
    processorOptions: { targetRate: event.input_sample_rate },
  });
  call.capture.port.onmessage = ({ data }) => {
    if (current === call && call.socket.readyState === WebSocket.OPEN && call.socket.bufferedAmount < 96000) {
      call.socket.send(data);
    }
  };
  call.context.createMediaStreamSource(call.stream).connect(call.capture);
  call.capture.connect(call.context.destination);
  $("mute").disabled = false;
  document.body.dataset.active = "true";
  status("Listening — start talking");
}

$("start").onclick = async () => {
  if (current) return;
  error("");
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
    error("Use a browser with microphone and AudioWorklet support on localhost or HTTPS.");
    return;
  }
  const call = { turns: new Map(), muted: false };
  current = call;
  $("start").disabled = true;
  $("stop").disabled = false;
  $("tool-status").textContent = "";
  status("Connecting…");
  try {
    // Resume on the user gesture so audible responses are allowed by the browser.
    call.context = new AudioContext({ sampleRate: 24000 });
    await call.context.resume();
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    if (current !== call) { stream.getTracks().forEach((track) => track.stop()); return; }
    call.stream = stream;
    call.socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/conversation`);
    call.socket.onmessage = async ({ data }) => {
      if (current !== call) return;
      try {
        const event = JSON.parse(data);
        if (event.type === "ready") await prepareAudio(call, event);
        else if (event.type === "audio") play(call, event);
        else if (event.type === "clear_audio") clearAudio(call, event);
        else if (event.type === "transcript") caption(call, event);
        else if (event.type === "status") status(event.state === "thinking" ? "Thinking…" : "Listening…");
        else if (event.type === "tool") $("tool-status").textContent = event.state === "calling" ? "Looking up sample payments…" : "Payment lookup complete";
        else if (event.type === "error") error(event.message);
      } catch (err) {
        error(`Audio connection failed: ${err.message}`);
        finish(call);
      }
    };
    call.socket.onerror = () => { if (current === call) error("Could not connect to the voice service."); };
    call.socket.onclose = ({ code }) => {
      if (current === call && code !== 1000 && !$("error").textContent) error("The connection was interrupted. Start a new conversation to reconnect.");
      finish(call);
    };
  } catch (err) {
    if (current === call) {
      error(err.name === "NotAllowedError" ? "Microphone permission was denied. Allow microphone access and try again." : `Could not start audio: ${err.message}`);
      finish(call, "Ready when you are");
    }
  }
};

$("stop").onclick = () => {
  if (!current) return;
  send(current, { type: "stop" });
  finish(current);
};
$("mute").onclick = () => {
  if (!current) return;
  current.muted = !current.muted;
  current.stream.getAudioTracks().forEach((track) => { track.enabled = !current.muted; });
  $("mute").textContent = current.muted ? "Unmute microphone" : "Mute microphone";
  $("mute").setAttribute("aria-pressed", String(current.muted));
};
window.addEventListener("pagehide", () => { if (current) finish(current); });

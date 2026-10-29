const params = new URLSearchParams(location.search);
const config = {
  ingestUrl: params.get("ingestUrl") || "",
  targetUrl: params.get("targetUrl") || "",
  fps: Math.max(1, Number(params.get("fps") || 60)),
  bitrate: Math.max(1, Number(params.get("bitrate") || 4000000)),
  chunkMs: Math.max(100, Number(params.get("chunkMs") || 1000)),
  width: Math.max(1, Number(params.get("width") || 1280)),
  height: Math.max(1, Number(params.get("height") || 720))
};

let recorder = null;
let stream = null;
let sequence = 0;
const pendingUploads = new Set();

const statusNode = document.querySelector("#status");

window.__recorderStatus = {
  state: "idle",
  error: null,
  chunks: 0,
  bytes: 0
};

function setStatus(patch) {
  window.__recorderStatus = {
    ...window.__recorderStatus,
    ...patch
  };
  statusNode.textContent = JSON.stringify(window.__recorderStatus, null, 2);
}

async function postJson(path, payload) {
  await fetch(`${config.ingestUrl}${path}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      ...payload,
      controller_perf_ms: performance.now(),
      controller_epoch_ms: Date.now()
    })
  });
}

async function postChunk(blob) {
  const seq = sequence++;
  const arrayBuffer = await blob.arrayBuffer();
  await fetch(`${config.ingestUrl}/video-chunk?seq=${seq}`, {
    method: "POST",
    headers: {
      "Content-Type": blob.type || "application/octet-stream",
      "X-Recorder-Sequence": String(seq)
    },
    body: arrayBuffer
  });
  setStatus({
    chunks: window.__recorderStatus.chunks + 1,
    bytes: window.__recorderStatus.bytes + arrayBuffer.byteLength
  });
}

function queueUpload(promise) {
  pendingUploads.add(promise);
  promise.finally(() => pendingUploads.delete(promise));
}

async function findTargetTab() {
  const tabs = await chrome.tabs.query({});
  const exact = tabs.find(tab => tab.url === config.targetUrl);
  if (exact) return exact;
  const prefix = tabs.find(tab => tab.url && tab.url.startsWith(config.targetUrl));
  if (prefix) return prefix;
  throw new Error(`No target tab found for ${config.targetUrl}`);
}

async function startRecording() {
  if (recorder && recorder.state !== "inactive") return window.__recorderStatus;
  if (!config.ingestUrl) throw new Error("Missing ingestUrl.");
  if (!config.targetUrl) throw new Error("Missing targetUrl.");

  setStatus({state: "starting", error: null});
  try {
    const target = await findTargetTab();
    const streamId = await chrome.tabCapture.getMediaStreamId({targetTabId: target.id});
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        cursor: "never",
        mandatory: {
          chromeMediaSource: "tab",
          chromeMediaSourceId: streamId,
          cursor: "never",
          maxFrameRate: String(config.fps),
          maxWidth: String(config.width),
          maxHeight: String(config.height)
        }
      }
    });
    const videoTrack = stream.getVideoTracks()[0] || null;
    const videoSettings = videoTrack ? videoTrack.getSettings() : null;
    const videoConstraints = videoTrack ? videoTrack.getConstraints() : null;

    const options = {
      mimeType: "video/webm;codecs=vp8",
      videoBitsPerSecond: config.bitrate
    };
    recorder = new MediaRecorder(stream, options);
    recorder.ondataavailable = event => {
      if (event.data && event.data.size > 0) {
        const upload = postChunk(event.data).catch(error => {
          setStatus({state: "error", error: String(error.stack || error)});
        });
        queueUpload(upload);
      }
    };
    recorder.onerror = event => {
      setStatus({state: "error", error: String(event.error || "MediaRecorder error")});
    };
    recorder.onstop = () => {
      (async () => {
        await Promise.allSettled([...pendingUploads]);
        for (const track of stream ? stream.getTracks() : []) track.stop();
        setStatus({state: "stopped"});
        await postJson("/extension-event", {
          type: "mediarecorder_stop",
          chunks: window.__recorderStatus.chunks,
          bytes: window.__recorderStatus.bytes
        }).catch(() => {});
      })();
    };

    await postJson("/extension-event", {
      type: "mediarecorder_starting",
      target_tab_id: target.id,
      target_url: target.url,
      fps: config.fps,
      bitrate: config.bitrate,
      width: config.width,
      height: config.height,
      cursor: "never",
      video_track_settings: videoSettings,
      video_track_constraints: videoConstraints
    });
    recorder.start(config.chunkMs);
    setStatus({state: "recording"});
    await postJson("/extension-event", {type: "mediarecorder_start"});
  } catch (error) {
    setStatus({state: "error", error: String(error.stack || error)});
    await postJson("/extension-event", {
      type: "mediarecorder_error",
      error: String(error.stack || error)
    }).catch(() => {});
    throw error;
  }
  return window.__recorderStatus;
}

async function stopRecording() {
  if (!recorder || recorder.state === "inactive") return window.__recorderStatus;
  setStatus({state: "stopping"});
  const stopped = new Promise(resolve => {
    const previous = recorder.onstop;
    recorder.onstop = event => {
      if (previous) previous(event);
      resolve(window.__recorderStatus);
    };
  });
  recorder.stop();
  return stopped;
}

window.__startRecording = startRecording;
window.__stopRecording = stopRecording;

document.querySelector("#start")?.addEventListener("click", () => {
  startRecording().catch(() => {});
});
document.querySelector("#stop")?.addEventListener("click", () => {
  stopRecording().catch(() => {});
});

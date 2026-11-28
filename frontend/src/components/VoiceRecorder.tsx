import { useEffect, useRef, useState } from "react";
import { AUDIO_ACCEPT, formatDuration } from "../util";
import { fixWebmDuration } from "../webmDuration";
import { Icon } from "./Icon";

/** What to record in, best first: Chrome and Firefox record WebM/Opus, Safari MP4/AAC. */
const FORMATS = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4;codecs=mp4a.40.2", "audio/mp4"];

const STATUS = {
  starting: "Allow the microphone…",
  recording: "Recording",
  paused: "Paused",
  saving: "Saving…",
};

type Phase = "idle" | keyof typeof STATUS;

export interface Recording {
  file: File;
  seconds: number;
}

/** One recording, from opening the microphone to the finished file. */
interface Take {
  stream?: MediaStream;
  recorder?: MediaRecorder;
  audio?: AudioContext; // drives the level meter only
  chunks: Blob[];
  timer: number;
  frame: number;
  since: number; // performance.now() when the current stretch began
  banked: number; // ms recorded before it, pauses left out
  running: boolean;
  discard: boolean;
}

function micError(e: unknown): string {
  const name = e instanceof DOMException ? e.name : "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Microphone access is blocked. Allow it in the browser's site settings, or upload a recording instead.";
  if (name === "NotFoundError" || name === "OverconstrainedError")
    return "No microphone found. Connect one, or upload a recording instead.";
  if (name === "NotReadableError") return "The microphone is in use by another app. Close it and try again.";
  return `Could not start recording: ${e instanceof Error ? e.message : String(e)}`;
}

function memoName(type: string): string {
  const ext = /ogg/i.test(type) ? "ogg" : /mp4|aac/i.test(type) ? "m4a" : "webm";
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  const day = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return `Voice memo ${day} ${p(d.getHours())}.${p(d.getMinutes())}.${p(d.getSeconds())}.${ext}`;
}

const elapsedMs = (t: Take) => t.banked + (t.running ? performance.now() - t.since : 0);

function release(t: Take) {
  clearInterval(t.timer);
  cancelAnimationFrame(t.frame);
  t.stream?.getTracks().forEach((track) => track.stop());
  t.audio?.close().catch(() => undefined);
  t.audio = undefined;
}

/** Stop a take; a discarded one never reaches onRecorded. */
function end(t: Take, discard: boolean) {
  t.discard ||= discard;
  if (t.recorder && t.recorder.state !== "inactive") t.recorder.stop(); // fires onstop -> save()
  release(t);
}

/**
 * Record a voice memo in the browser, or pick recordings from disk. A finished memo comes back as a
 * File through onRecorded; picked files go to onUpload. onActive says whether a recording is under way.
 */
export function VoiceRecorder({
  onRecorded,
  onUpload,
  onActive,
  disabled,
}: {
  onRecorded: (recording: Recording) => void;
  onUpload: (files: FileList) => void;
  onActive?: (active: boolean) => void;
  disabled?: boolean;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string>();
  const take = useRef<Take | null>(null);
  const meter = useRef<HTMLSpanElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const callbacks = useRef({ onRecorded, onActive });
  callbacks.current = { onRecorded, onActive };
  const live = phase === "recording" || phase === "paused";

  // Leaving the page drops a recording in progress and frees the microphone.
  useEffect(
    () => () => {
      const t = take.current;
      take.current = null;
      if (t) end(t, true);
    },
    [],
  );

  useEffect(() => callbacks.current.onActive?.(phase !== "idle"), [phase]);

  useEffect(() => {
    if (!live) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault(); // "Leave site?" while recording
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [live]);

  function showLevel(t: Take) {
    const ctx = t.audio;
    if (!ctx || !t.stream) return;
    try {
      ctx.resume().catch(() => undefined);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      ctx.createMediaStreamSource(t.stream).connect(analyser);
      const samples = new Uint8Array(analyser.fftSize);
      let level = 0;
      const draw = () => {
        if (t.audio !== ctx) return;
        analyser.getByteTimeDomainData(samples);
        let sum = 0;
        for (const v of samples) sum += ((v - 128) / 128) ** 2;
        level = Math.max(Math.min(1, Math.sqrt(sum / samples.length) * 4), level * 0.92);
        meter.current?.style.setProperty("--level", level.toFixed(3));
        t.frame = requestAnimationFrame(draw);
      };
      draw();
    } catch {
      /* no meter, the recording itself is unaffected */
    }
  }

  async function start() {
    if (take.current) return;
    setError(undefined);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError(
        window.isSecureContext
          ? "This browser can't record audio. Upload a recording instead."
          : "Recording needs a secure page (https or localhost). Upload a recording instead.",
      );
      return;
    }
    const t: Take = { chunks: [], timer: 0, frame: 0, since: 0, banked: 0, running: false, discard: false };
    take.current = t;
    try {
      t.audio = new AudioContext(); // made during the click, so the browser lets it run
    } catch {
      /* no meter */
    }
    setPhase("starting");
    try {
      t.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (take.current !== t) return end(t, true); // discarded while the browser asked
      const mimeType = FORMATS.find((f) => MediaRecorder.isTypeSupported(f));
      t.recorder = new MediaRecorder(t.stream, mimeType ? { mimeType, audioBitsPerSecond: 64_000 } : undefined);
    } catch (e) {
      end(t, true);
      if (take.current === t) {
        take.current = null;
        setPhase("idle");
        setError(micError(e));
      }
      return;
    }
    const recorder = t.recorder;
    recorder.ondataavailable = (e) => {
      if (e.data.size) t.chunks.push(e.data);
    };
    recorder.onstop = () => void save(t);
    recorder.onerror = () => setError("The recording stopped unexpectedly; what was recorded so far is kept.");
    recorder.start(1000);
    t.since = performance.now();
    t.running = true;
    t.timer = window.setInterval(() => setElapsed(elapsedMs(t)), 250);
    showLevel(t);
    setElapsed(0);
    setPhase("recording");
  }

  function pause() {
    const t = take.current;
    if (t?.recorder?.state !== "recording") return;
    t.recorder.pause();
    t.banked = elapsedMs(t);
    t.running = false;
    setElapsed(t.banked);
    setPhase("paused");
  }

  function resume() {
    const t = take.current;
    if (t?.recorder?.state !== "paused") return;
    t.recorder.resume();
    t.since = performance.now();
    t.running = true;
    setPhase("recording");
  }

  function stop() {
    const t = take.current;
    if (!t?.recorder || t.recorder.state === "inactive") return;
    setPhase("saving");
    t.recorder.stop();
  }

  function discard() {
    const t = take.current;
    if (!t || (elapsedMs(t) > 15_000 && !confirm("Discard this recording?"))) return;
    take.current = null;
    end(t, true);
    setPhase("idle");
    setElapsed(0);
  }

  /** The recorder stopped (Stop, or the microphone went away): hand over the memo. */
  async function save(t: Take) {
    t.banked = elapsedMs(t);
    t.running = false;
    release(t);
    if (t.discard) return;
    if (take.current === t) take.current = null;
    const type = t.recorder?.mimeType || t.chunks[0]?.type || "audio/webm";
    if (t.chunks.length) {
      let blob = new Blob(t.chunks, { type });
      if (/webm/i.test(type)) blob = await fixWebmDuration(blob, t.banked);
      const file = new File([blob], memoName(type), { type: type.split(";")[0] });
      callbacks.current.onRecorded({ file, seconds: t.banked / 1000 });
    } else {
      setError("Nothing was recorded.");
    }
    setPhase("idle");
    setElapsed(0);
  }

  return (
    <div className="voice">
      {phase === "idle" ? (
        <div className="voice-actions">
          <button type="button" className="btn" onClick={start} disabled={disabled}>
            <span className="rec-dot" aria-hidden="true" /> Record a memo
          </button>
          <button type="button" className="btn" onClick={() => picker.current?.click()} disabled={disabled}>
            <Icon name="upload" size={16} /> Upload a recording
          </button>
          <input
            ref={picker}
            type="file"
            accept={AUDIO_ACCEPT}
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files?.length) {
                setError(undefined);
                onUpload(e.target.files);
              }
              e.target.value = "";
            }}
          />
        </div>
      ) : (
        <div className={`voice-live ${phase}`}>
          <span className="rec-dot" aria-hidden="true" />
          <span className="voice-status" role="status">
            {STATUS[phase]}
          </span>
          {phase !== "starting" && <span className="voice-time">{formatDuration(elapsed / 1000)}</span>}
          {live && (
            <span className="voice-meter" ref={meter} aria-hidden="true">
              <span />
            </span>
          )}
          <span className="grow" />
          {phase === "recording" && (
            <button type="button" className="btn small" onClick={pause}>
              <Icon name="pause" size={14} /> Pause
            </button>
          )}
          {phase === "paused" && (
            <button type="button" className="btn small" onClick={resume}>
              <span className="rec-dot" aria-hidden="true" /> Resume
            </button>
          )}
          {live && (
            <button type="button" className="btn primary small" onClick={stop}>
              <Icon name="stop" size={14} /> Stop &amp; add
            </button>
          )}
          {phase !== "saving" && (
            <button type="button" className="icon-btn" title="Discard recording" aria-label="Discard recording" onClick={discard}>
              <Icon name="x" size={15} />
            </button>
          )}
        </div>
      )}
      {error && <p className="warn small voice-error">{error}</p>}
    </div>
  );
}

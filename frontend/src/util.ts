import type { SourceKind } from "./types";

export const KIND_META: Record<SourceKind, { label: string; hint: string }> = {
  image: { label: "Image", hint: "Photo, screenshot or scan" },
  text: { label: "Note", hint: "Thoughts, chat export, document" },
  video: { label: "Video", hint: "Video file or YouTube link" },
  audio: { label: "Audio", hint: "Voice memo or recording" },
  academic: { label: "Paper", hint: "Academic paper — fact-checked" },
};

/** Every profile has a priority: 1 is the highest; people nobody ranked get this. */
export const DEFAULT_PRIORITY = 4;

const INK = ["#c4452f", "#2f4a6b", "#5f7d45", "#a87b2a", "#7a4a6e", "#3d6e6b", "#8a5a3c"];

export function inkColor(seed: string): string {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.codePointAt(0)!) >>> 0;
  return INK[h % INK.length];
}

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  const first = [...words[0]][0] ?? "?";
  if (!/[\p{Script=Latin}\p{Script=Cyrillic}\p{Script=Greek}]/u.test(first)) return first;
  const last = words.length > 1 ? [...words[words.length - 1]][0] : "";
  return (first + last).toUpperCase();
}

export function timeAgo(iso: string): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const s = Math.round((Date.now() - then) / 1000);
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} d ago`;
  return new Date(iso).toLocaleDateString();
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** "1:05", or "1:02:05" past an hour. */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

const IMAGE_EXT = /\.(jpe?g|png|webp|heic|heif)$/i;
const VIDEO_EXT = /\.(mp4|m4v|mov|webm|mpe?g|avi|wmv|flv|3gp)$/i;
const AUDIO_EXT = /\.(mp3|wav|m4a|aac|ogg|oga|opus|flac|aiff?|weba)$/i;
/** WebM holds video or sound only; browsers record voice memos as .webm. */
const WEBM_EXT = /\.webm$/i;
const TEXT_EXT = /\.(txt|md|markdown|csv|tsv|json|log|html?|xml|ya?ml|srt|vtt|rtf)$/i;

/** What the "Upload a recording" button lets you pick. */
export const AUDIO_ACCEPT = "audio/*,.mp3,.wav,.m4a,.aac,.ogg,.oga,.opus,.flac,.aif,.aiff,.weba,.webm";

/**
 * Mirror of the backend's detect_kind: default kind + the kinds the user may switch to.
 * `audio` is set for files picked as recordings: a .webm then starts as audio instead of video.
 */
export function fileKinds(file: File, audio = false): { kind: SourceKind; options: SourceKind[] } | null {
  const name = file.name;
  if (IMAGE_EXT.test(name)) return { kind: "image", options: ["image"] };
  if (WEBM_EXT.test(name))
    return audio || file.type.startsWith("audio/")
      ? { kind: "audio", options: ["audio", "video"] }
      : { kind: "video", options: ["video", "audio"] };
  if (VIDEO_EXT.test(name)) return { kind: "video", options: ["video"] };
  if (AUDIO_EXT.test(name)) return { kind: "audio", options: ["audio"] };
  if (/\.pdf$/i.test(name)) return { kind: "academic", options: ["academic", "text"] };
  if (TEXT_EXT.test(name) || file.type.startsWith("text/")) return { kind: "text", options: ["text", "academic"] };
  return null;
}

export function linkKinds(url: string): { kind: SourceKind; options: SourceKind[] } | null {
  try {
    const u = new URL(url);
    if (!/^https?:$/.test(u.protocol)) return null;
    const yt = /(^|\.)((youtube\.com)|(youtu\.be))$/i.test(u.hostname);
    if (yt) return { kind: "video", options: ["video", "text"] };
    // Social profiles are web pages, not papers (the backend also saves them to the profile's details).
    const social = /(^|\.)(facebook|instagram|linkedin|github)\.com$/i.test(u.hostname);
    return social ? { kind: "text", options: ["text", "academic"] } : { kind: "academic", options: ["academic", "text"] };
  } catch {
    return null;
  }
}

export function uid(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

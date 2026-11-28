/**
 * Chrome and Firefox record WebM without a duration (MediaRecorder streams the file, so its length is
 * unknown when the header is written). Players then can't show how long a memo is or seek in it, here
 * or in Obsidian. This writes the duration into the file's Segment Info, as fix-webm-duration does.
 * A file laid out differently than expected is left untouched.
 */

const EBML = 0x1a45dfa3;
const SEGMENT = 0x18538067;
const SEEK_HEAD = 0x114d9b74;
const INFO = 0x1549a966;
const TIMECODE_SCALE = 0x2ad7b1;
const DURATION = 0x4489;
const CLUSTER = 0x1f43b675;

interface Vint {
  value: number;
  length: number;
  /** A size whose bits are all 1 means "unknown": MediaRecorder writes the Segment that way. */
  unknown: boolean;
}

/** An EBML variable-length integer: an element ID (marker bit kept) or a size (marker bit dropped). */
function readVint(buf: Uint8Array, pos: number, isId: boolean): Vint | null {
  const first = buf[pos];
  if (first === undefined || first === 0) return null;
  let length = 1;
  while (!(first & (0x80 >> (length - 1)))) length++;
  if (pos + length > buf.length || (isId && length > 4)) return null;
  const mask = (0x80 >> (length - 1)) - 1;
  let value = isId ? first : first & mask;
  let allOnes = (first & mask) === mask;
  for (let i = 1; i < length; i++) {
    value = value * 256 + buf[pos + i];
    allOnes &&= buf[pos + i] === 0xff;
  }
  return { value, length, unknown: !isId && allOnes };
}

interface Element {
  id: number;
  start: number;
  idLength: number;
  dataStart: number;
  /** -1 when the size is unknown. */
  end: number;
}

function readElement(buf: Uint8Array, pos: number): Element | null {
  const id = readVint(buf, pos, true);
  const size = id && readVint(buf, pos + id.length, false);
  if (!id || !size) return null;
  const dataStart = pos + id.length + size.length;
  return { id: id.value, start: pos, idLength: id.length, dataStart, end: size.unknown ? -1 : dataStart + size.value };
}

/** The file with a Duration element added to its Info, or null when that can't be done safely. */
export function addWebmDuration(buf: Uint8Array, durationMs: number): Uint8Array<ArrayBuffer> | null {
  if (!(durationMs > 0)) return null;
  const header = readElement(buf, 0);
  if (!header || header.id !== EBML || header.end < 0) return null;
  const segment = readElement(buf, header.end);
  // A Segment of known size would have to grow as well; MediaRecorder never writes one.
  if (!segment || segment.id !== SEGMENT || segment.end >= 0) return null;

  let info: Element | null = null;
  for (let pos = segment.dataStart; !info; ) {
    const el = readElement(buf, pos);
    if (!el || el.end < 0 || el.end > buf.length) return null;
    // A SeekHead holds offsets the insertion would shift; media before the Info is a layout we don't know.
    if (el.id === SEEK_HEAD || el.id === CLUSTER) return null;
    if (el.id === INFO) info = el;
    pos = el.end;
  }

  let scale = 1_000_000; // nanoseconds per timecode unit (the Matroska default)
  for (let pos = info.dataStart; pos < info.end; ) {
    const child = readElement(buf, pos);
    if (!child || child.end < 0 || child.end > info.end) return null;
    if (child.id === DURATION) return null; // it has one already
    if (child.id === TIMECODE_SCALE) {
      let value = 0;
      for (let i = child.dataStart; i < child.end; i++) value = value * 256 + buf[i];
      scale = value || scale;
    }
    pos = child.end;
  }

  // Duration: ID 0x4489, size 8, then a big-endian float64 in timecode units.
  const duration = new Uint8Array(11);
  duration.set([0x44, 0x89, 0x88]);
  new DataView(duration.buffer).setFloat64(3, (durationMs * 1e6) / scale);

  // The Info again, with the Duration appended and its new size as an 8-byte vint (0x01, then 7 bytes).
  const payload = info.end - info.dataStart + duration.length;
  const newInfo = new Uint8Array(info.idLength + 8 + payload);
  newInfo.set(buf.subarray(info.start, info.start + info.idLength), 0);
  newInfo[info.idLength] = 0x01;
  for (let i = 7, rest = payload; i >= 1; i--, rest = Math.floor(rest / 256)) newInfo[info.idLength + i] = rest % 256;
  newInfo.set(buf.subarray(info.dataStart, info.end), info.idLength + 8);
  newInfo.set(duration, newInfo.length - duration.length);

  const out = new Uint8Array(buf.length - (info.end - info.start) + newInfo.length);
  out.set(buf.subarray(0, info.start), 0);
  out.set(newInfo, info.start);
  out.set(buf.subarray(info.end), info.start + newInfo.length);
  return out;
}

/** `blob` with its duration written in, or `blob` itself when it isn't a WebM this can fix. */
export async function fixWebmDuration(blob: Blob, durationMs: number): Promise<Blob> {
  try {
    const fixed = addWebmDuration(new Uint8Array(await blob.arrayBuffer()), durationMs);
    return fixed ? new Blob([fixed], { type: blob.type }) : blob;
  } catch {
    return blob;
  }
}

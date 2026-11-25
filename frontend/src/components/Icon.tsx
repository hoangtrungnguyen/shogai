import type { CSSProperties } from "react";

const PATHS: Record<string, string> = {
  users:
    '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8"/><path d="M18 14.3c2.1.8 3.5 2.8 3.5 5.7"/>',
  consume: '<path d="M12 3v11"/><path d="m7.5 9.5 4.5 4.5 4.5-4.5"/><path d="M3 14v4a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-4"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>',
  text: '<path d="M5 3h10l4 4v14H5z"/><path d="M14 3v5h5"/><path d="M8 13h8M8 17h6"/>',
  video: '<rect x="2.5" y="6" width="13" height="12" rx="2"/><path d="m15.5 10 6-3.5v11l-6-3.5"/>',
  audio: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
  academic: '<path d="M2 9.5 12 5l10 4.5-10 4.5z"/><path d="M6 11.5V16c0 1.5 2.7 3 6 3s6-1.5 6-3v-4.5"/><path d="M22 9.5V15"/>',
  link: '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  graph:
    '<circle cx="6" cy="6.5" r="2.5"/><circle cx="18" cy="8" r="2.5"/><circle cx="10" cy="18" r="2.5"/><path d="M8.5 6.8 15.5 7.7M6.8 8.9l2.4 6.7M16.4 10l-4.8 6"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  trash: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
  refresh:
    '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 3v5h5"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 21v-5h-5"/>',
  send: '<path d="M4 12 20 4l-6 16-3-7z"/><path d="m11 13 9-9"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.01"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16v4M17 18h4"/>',
  upload: '<path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  history: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  back: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  book: '<path d="M2.5 5.5c3-1.6 6.2-1.6 9.5.8 3.3-2.4 6.5-2.4 9.5-.8v13.6c-3-1.6-6.2-1.6-9.5.8-3.3-2.4-6.5-2.4-9.5-.8z"/><path d="M12 6.3v14"/>',
  phone: '<path d="M5 3h3.5l2 5-2.5 1.6a11 11 0 0 0 5.9 5.9L15.5 13l5 2v3.5a2 2 0 0 1-2 2A16.5 16.5 0 0 1 3 5a2 2 0 0 1 2-2z"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 7 8.5 6 8.5-6"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  flag: '<path d="M5 21V4"/><path d="M5 4h12l-2.5 4.5L17 13H5"/>',
  linkedin: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 10.5V17M8 7.5v.01M12 17v-6.5M12 13.5a2.5 2.5 0 0 1 5 0V17"/>',
  facebook: '<path d="M15 3h-2.2A3.8 3.8 0 0 0 9 6.8V10H6.5v3.2H9V21h3.3v-7.8h2.6l.6-3.2h-3.2V7.2a1 1 0 0 1 1-1H15z"/>',
  instagram: '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><path d="M17.3 6.7v.01"/>',
  github: '<path d="M9 19.5c-4.3 1.4-4.3-2-6-2.5m12 4.5v-3.4a3 3 0 0 0-.8-2.3c2.8-.3 5.8-1.4 5.8-6.2a4.8 4.8 0 0 0-1.3-3.3 4.5 4.5 0 0 0-.1-3.3s-1-.3-3.4 1.3a11.6 11.6 0 0 0-6.2 0C6.6 1.7 5.6 2 5.6 2a4.5 4.5 0 0 0-.1 3.3 4.8 4.8 0 0 0-1.3 3.3c0 4.8 3 5.9 5.8 6.2a3 3 0 0 0-.8 2.3v3.4"/>',
  file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18, style, className }: { name: IconName | string; size?: number; style?: CSSProperties; className?: string }) {
  return (
    <svg
      className={`icon ${className ?? ""}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={style}
      dangerouslySetInnerHTML={{ __html: PATHS[name] ?? PATHS.file }}
    />
  );
}

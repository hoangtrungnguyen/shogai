export type SourceKind = "image" | "text" | "video" | "audio" | "academic";

export interface PersonProperties {
  priority: number; // whole number, 1 = highest; DEFAULT_PRIORITY when not set
  follow_up: boolean;
  phone: string | null;
  email: string | null;
  linkedin: string | null;
  facebook: string | null;
  instagram: string | null;
  github: string | null;
  blog: string | null;
  related_webs: string[];
}

export interface PersonSummary {
  slug: string;
  name: string;
  relationship: string;
  aliases: string[];
  tags: string[];
  summary: string;
  avatar: string | null;
  created: string;
  updated: string;
  source_count: number;
  properties: PersonProperties;
}

/** A link between two people: `source` lists `target` in the References section of their profile. */
export interface GraphEdge {
  source: string;
  target: string;
  /** Both profiles list each other. */
  mutual: boolean;
}

export interface GraphData {
  edges: GraphEdge[];
  /** References entries that match no one in the circle. */
  unresolved: { person: string; target: string }[];
}

export interface LinkResult {
  scanned: number;
  profiles: number;
  links: number;
  added: Record<string, string[]>;
  graph: GraphData;
}

export interface Person extends PersonSummary {
  body: string;
  raw: string;
}

export interface SourceRecord {
  id: string;
  kind: SourceKind;
  title: string;
  summary: string;
  created: string;
  filename: string;
  original: string | null;
  media: string | null;
  note: string | null;
  url: string | null;
}

export interface SourceDetail extends SourceRecord {
  markdown: string;
}

export interface MediaFile {
  name: string;
  size: number;
  path: string;
}

export interface Health {
  ok: boolean;
  version: string;
  commit: string | null;
  built_at: string | null;
  gemini_configured: boolean;
  models: { analyze: string; synthesize: string; advisor: string };
  language: string;
  keep_media: boolean;
  data_dir: string;
  people: number;
}

export interface JobSnapshot {
  id: string;
  kind: "ingest" | "rebuild" | "library";
  person: string;
  person_name: string;
  label: string;
  created: string;
  status: "running" | "done" | "error";
  result: JobResult | null;
  error: string | null;
}

export interface JobResult {
  person: string;
  name: string;
  sources: SourceRecord[];
  changes: string[];
  failed: { id: string; message: string }[];
}

export type JobEvent =
  | { type: "plan"; ts: string; items: { id: string; kind: SourceKind; label: string }[] }
  | { type: "stage"; ts: string; stage: string; message: string }
  | {
      type: "item";
      ts: string;
      id: string;
      status: "running" | "done" | "error";
      message: string;
      summary?: string;
      observations?: number;
      about_subject?: boolean;
    }
  | { type: "done"; ts: string; result: JobResult }
  | { type: "error"; ts: string; message: string };

export type ChatEvent =
  | { type: "text"; delta: string }
  | { type: "tool"; name: string; label: string }
  | { type: "done" }
  | { type: "error"; message: string };

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  tools?: string[];
  error?: string;
}

export interface Conversation {
  id: string;
  title: string;
  focus: string[];
  messages: ChatMessage[];
  updated: string;
}

export type ResourceType =
  | "paper"
  | "book"
  | "chapter"
  | "textbook"
  | "thesis"
  | "report"
  | "course"
  | "lecture"
  | "other";

export interface ResourceMention {
  person: string;
  name: string;
  source_id: string;
  source_file: string;
  quote: string;
  context: string;
}

export interface ConsumedPaper {
  person: string;
  name: string;
  source_id: string;
  source_file: string;
  relation: string | null;
  verdicts: string[];
  venue: string | null;
  identifier: string | null;
}

export interface LibraryResource {
  id: string;
  title: string;
  type: ResourceType;
  authors: string[];
  year: string | null;
  field: string | null;
  identified: boolean;
  mentions: ResourceMention[];
  consumed: ConsumedPaper[];
}

export interface LibraryStatus {
  notes: number;
  scanned: number;
  pending: number;
  updated: string | null;
  scanning: boolean;
}

export interface LibraryData {
  status: LibraryStatus;
  resources: LibraryResource[];
}

/* ------------------------------------------------------------ about me (public) */

export interface PublicOwner {
  /** From the frontmatter of about.md. */
  name: string | null;
  headline: string | null;
}

export interface PublicFileSummary {
  /** Relative to the public folder, e.g. "about.md" or "work/projects.md". */
  path: string;
  title: string;
  updated: string;
  size: number;
  /** `draft: true` in the frontmatter: not public. */
  draft: boolean;
}

export interface PublicFileDetail extends PublicFileSummary {
  meta: Record<string, unknown>;
  body: string;
  /** The whole file (owner API only). */
  raw?: string;
}

export interface PublicInfo {
  owner: PublicOwner;
  files: PublicFileDetail[];
  /** The owner's Shōgai Relay: with a key from them, ask them there. */
  relay: string | null;
}

export interface PublicListener {
  enabled: boolean;
  running: boolean;
  /** Where the public listener answers on this machine, e.g. http://127.0.0.1:8001. */
  local_url: string | null;
  /** SHOGAI_PUBLIC_URL: the address visitors use (a tunnel or proxy), if set. */
  url: string | null;
  error: string | null;
}

export interface MeInfo {
  owner: PublicOwner;
  folder: string;
  files: PublicFileSummary[];
  public: PublicListener;
}

export type RequestStatus = "pending" | "replied" | "declined";

export interface InfoRequest {
  id: string;
  status: RequestStatus;
  /** As the sender typed it (not verified). */
  name: string;
  question: string;
  /** Your reply, or the message sent with a decline. */
  reply: string;
  created: string;
  updated: string;
  answered: string | null;
  /** The request on the relay. */
  relay_id: string;
  /** The key it was sent with, and the label you gave that key. */
  key_id: string;
  key_label: string | null;
  /** The relay has your latest reply (false: it is sent at the next sync). */
  synced: boolean;
  /** Deleted on the relay (its key was revoked): a reply can't reach the sender. */
  relay_gone: boolean;
  /** Someone in your circle with the same name. Not proof: anyone can type a name. */
  match: { slug: string; name: string; relationship: string; avatar: string | null } | null;
}

export interface RequestCounts {
  pending: number;
  replied: number;
  declined: number;
  all: number;
}

export interface RequestList {
  counts: RequestCounts;
  requests: InfoRequest[];
}

/** A key you hand out: whoever has it can ask you things on your relay. */
export interface RelayKey {
  id: string;
  /** Whom you gave it to; stays in this app. */
  label: string;
  key: string;
  created: string;
}

export interface RelayInfo {
  connected: boolean;
  /** Where this app reaches the relay. */
  server: string | null;
  /** The relay's address for people (key links). */
  share_url: string | null;
  /** Plain http to a machine other than this one: keys travel unencrypted. */
  insecure: boolean;
  keys: RelayKey[];
  last_sync: string | null;
  error: string | null;
}

export interface RelaySyncResult extends RelayInfo {
  added: number;
  sent: number;
}

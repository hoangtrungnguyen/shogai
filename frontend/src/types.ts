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

export type SourceKind = "image" | "text" | "video" | "audio" | "academic";

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
  gemini_configured: boolean;
  models: { analyze: string; synthesize: string; advisor: string };
  language: string;
  keep_media: boolean;
  data_dir: string;
  people: number;
}

export interface JobSnapshot {
  id: string;
  kind: "ingest" | "rebuild";
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

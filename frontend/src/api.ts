import type {
  ChatEvent,
  GraphData,
  Health,
  LibraryData,
  PersonProperties,
  JobSnapshot,
  LinkResult,
  MediaFile,
  Person,
  PersonSummary,
  SourceDetail,
  SourceKind,
  SourceRecord,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const p = (slug: string) => `/api/people/${encodeURIComponent(slug)}`;

export const api = {
  health: () => request<Health>("/api/health"),
  people: () => request<PersonSummary[]>("/api/people"),
  person: (slug: string) => request<Person>(p(slug)),
  createPerson: (body: { name: string; relationship?: string; aliases?: string[]; tags?: string[] }) =>
    request<Person>("/api/people", json("POST", body)),
  savePerson: (slug: string, markdown: string) => request<Person>(p(slug), json("PUT", { markdown })),
  updateProperties: (slug: string, props: Partial<PersonProperties>) =>
    request<Person>(`${p(slug)}/properties`, json("PATCH", props)),
  deletePerson: (slug: string) => request<{ deleted: string }>(p(slug), { method: "DELETE" }),
  rebuild: (slug: string) => request<JobSnapshot>(`${p(slug)}/rebuild`, { method: "POST" }),
  sources: (slug: string) => request<SourceRecord[]>(`${p(slug)}/sources`),
  source: (slug: string, id: string) => request<SourceDetail>(`${p(slug)}/sources/${encodeURIComponent(id)}`),
  deleteSource: (slug: string, id: string) =>
    request<{ deleted: string }>(`${p(slug)}/sources/${encodeURIComponent(id)}`, { method: "DELETE" }),
  media: (slug: string) => request<MediaFile[]>(`${p(slug)}/media`),
  jobs: () => request<JobSnapshot[]>("/api/jobs"),
  graph: () => request<GraphData>("/api/graph"),
  linkPeople: () => request<LinkResult>("/api/graph/link", { method: "POST" }),
  job: (id: string) => request<JobSnapshot>(`/api/jobs/${id}`),
  library: () => request<LibraryData>("/api/library"),
  scanLibrary: (force = false) =>
    request<JobSnapshot>(`/api/library/scan${force ? "?force=true" : ""}`, { method: "POST" }),
};

export interface IngestFile {
  file: File;
  kind: SourceKind;
  note: string;
}

export interface IngestLink {
  url: string;
  kind: SourceKind;
  note: string;
}

export function ingest(person: string, thoughts: string, files: IngestFile[], links: IngestLink[]) {
  const form = new FormData();
  form.append("person", person);
  form.append("thoughts", thoughts);
  form.append("manifest", JSON.stringify(files.map((f) => ({ kind: f.kind, note: f.note }))));
  form.append("links", JSON.stringify(links));
  for (const f of files) form.append("files", f.file, f.file.name);
  return request<JobSnapshot>("/api/ingest", { method: "POST", body: form });
}

export function mediaUrl(slug: string, path: string): string {
  const name = path.replace(/^(\.\.\/)?media\//, "");
  return `${p(slug)}/media/${encodeURIComponent(name)}`;
}

/** POST /api/chat and yield server-sent events as they arrive. */
export async function* streamChat(
  body: { messages: { role: string; content: string }[]; focus: string[] },
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch("/api/chat", { ...json("POST", body), signal });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const data = chunk
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) yield JSON.parse(data) as ChatEvent;
    }
  }
}

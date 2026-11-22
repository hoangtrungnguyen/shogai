import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ingest } from "../api";
import { Icon } from "../components/Icon";
import { useJobs, usePeople, type TrackedJob } from "../state";
import type { SourceKind } from "../types";
import { fileKinds, formatBytes, KIND_META, linkKinds, uid } from "../util";

interface FileEntry {
  key: string;
  file: File;
  kind: SourceKind;
  options: SourceKind[];
  note: string;
  preview?: string;
}

interface LinkEntry {
  key: string;
  url: string;
  kind: SourceKind;
  options: SourceKind[];
  note: string;
}

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

const PIPELINE: { kind: SourceKind; title: string; text: string }[] = [
  { kind: "image", title: "Images", text: "Gemini vision reads photos, screenshots and cards — scene, mood, visible text." },
  { kind: "text", title: "Personal thoughts", text: "Gemini extracts every fact, preference and feeling from your notes or chat exports." },
  { kind: "video", title: "Video & audio", text: "Gemini watches or listens end-to-end: key moments, quotes, tone." },
  { kind: "academic", title: "Academic papers", text: "Gemini reads the paper, then fact-checks its claims with Google Search." },
];

function StatusIcon({ status }: { status: string }) {
  if (status === "running") return <span className="spinner" />;
  if (status === "done") return <Icon name="check" size={16} className="ok" />;
  if (status === "error") return <Icon name="alert" size={16} className="bad" />;
  return <span className="dot idle" />;
}

function JobPanel({ job }: { job: TrackedJob }) {
  return (
    <section className="card job-panel">
      <header className="job-panel-head">
        <div className="min0">
          <h3 className="truncate">{job.kind === "rebuild" ? "Rebuilding profile" : "Consuming"} · {job.personName}</h3>
          <p className="muted small">{job.status === "running" ? job.stage ?? "Starting…" : job.status === "done" ? "Done" : "Failed"}</p>
        </div>
        <StatusIcon status={job.status} />
      </header>
      {job.items.length > 0 && (
        <ol className="steps">
          {job.items.map((it) => (
            <li key={it.id} className={`step ${it.status}`}>
              <span className={`kind-badge k-${it.kind}`}>
                <Icon name={it.kind} size={16} />
              </span>
              <div className="min0 grow">
                <div className="truncate strong">{it.label}</div>
                <div className="muted small">
                  {it.status === "done" ? `${it.message} · ${it.observations ?? 0} observations` : it.message}
                </div>
                {it.status === "done" && it.aboutSubject === false && (
                  <div className="warn small">Gemini thinks this is not about {job.personName}.</div>
                )}
              </div>
              <StatusIcon status={it.status} />
            </li>
          ))}
        </ol>
      )}
      {job.status === "running" && (
        <p className="stage">
          <span className="spinner" /> {job.stage ?? "Starting…"}
        </p>
      )}
      {job.status === "done" && job.result && (
        <div className="result">
          {job.result.changes.length > 0 && (
            <>
              <h4>What changed</h4>
              <ul>
                {job.result.changes.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </>
          )}
          {job.result.failed.length > 0 && (
            <p className="warn small">{job.result.failed.length} source(s) failed and were skipped.</p>
          )}
          <Link className="btn primary" to={`/people/${encodeURIComponent(job.person)}`}>
            Open {job.personName}’s profile <Icon name="arrow" size={16} />
          </Link>
        </div>
      )}
      {job.status === "error" && <div className="banner error">{job.error}</div>}
    </section>
  );
}

export function ConsumePage() {
  const [params, setParams] = useSearchParams();
  const { people, refresh } = usePeople();
  const { jobs, track } = useJobs();
  const [mode, setMode] = useState<"existing" | "new" | null>(null);
  const [personSlug, setPersonSlug] = useState(params.get("person") ?? "");
  const [newName, setNewName] = useState("");
  const [newRel, setNewRel] = useState("");
  const [thoughts, setThoughts] = useState("");
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [links, setLinks] = useState<LinkEntry[]>([]);
  const [linkInput, setLinkInput] = useState("");
  const [rejected, setRejected] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const inputRef = useRef<HTMLInputElement>(null);
  const filesRef = useRef(files);
  filesRef.current = files;

  const effectiveMode = mode ?? (people.length ? "existing" : "new");
  const jobId = params.get("job");
  const job = jobs.find((j) => j.id === jobId);
  const recent = jobs.filter((j) => j.id !== jobId).slice(0, 3);

  useEffect(() => {
    const p = params.get("person");
    if (p) setPersonSlug(p);
  }, [params]);

  useEffect(() => {
    if (jobId && !job) api.job(jobId).then(track).catch(() => undefined);
  }, [jobId, job, track]);

  useEffect(() => () => filesRef.current.forEach((f) => f.preview && URL.revokeObjectURL(f.preview)), []);

  function addFiles(list: FileList | File[] | null) {
    if (!list) return;
    const bad: string[] = [];
    const add: FileEntry[] = [];
    for (const file of Array.from(list)) {
      const kinds = fileKinds(file);
      if (!kinds) {
        bad.push(file.name);
        continue;
      }
      add.push({
        key: uid(),
        file,
        ...kinds,
        note: "",
        preview: kinds.kind === "image" && !/\.hei[cf]$/i.test(file.name) ? URL.createObjectURL(file) : undefined,
      });
    }
    setRejected(bad);
    setFiles((cur) => [...cur, ...add]);
  }

  function removeFile(key: string) {
    setFiles((cur) => {
      const f = cur.find((x) => x.key === key);
      if (f?.preview) URL.revokeObjectURL(f.preview);
      return cur.filter((x) => x.key !== key);
    });
  }

  function addLink() {
    const url = linkInput.trim();
    if (!url) return;
    const kinds = linkKinds(url);
    if (!kinds) {
      setError(`Not a valid http(s) link: ${url}`);
      return;
    }
    setError(undefined);
    setLinks((cur) => [...cur, { key: uid(), url, ...kinds, note: "" }]);
    setLinkInput("");
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragOver(false);
    addFiles(e.dataTransfer.files);
  }

  function onPaste(e: ClipboardEvent) {
    if (e.clipboardData.files.length) {
      e.preventDefault();
      addFiles(e.clipboardData.files);
    }
  }

  const hasPerson = effectiveMode === "existing" ? !!personSlug : !!newName.trim();
  const hasInput = !!thoughts.trim() || files.length > 0 || links.length > 0;

  async function submit() {
    setBusy(true);
    setError(undefined);
    try {
      let slug = personSlug;
      if (effectiveMode === "new") {
        const created = await api.createPerson({ name: newName.trim(), relationship: newRel.trim() });
        slug = created.slug;
        await refresh();
        setMode("existing");
        setPersonSlug(slug);
        setNewName("");
        setNewRel("");
      }
      const snapshot = await ingest(
        slug,
        thoughts,
        files.map((f) => ({ file: f.file, kind: f.kind, note: f.note })),
        links.map((l) => ({ url: l.url, kind: l.kind, note: l.note })),
      );
      track(snapshot);
      setParams({ person: slug, job: snapshot.id });
      files.forEach((f) => f.preview && URL.revokeObjectURL(f.preview));
      setFiles([]);
      setLinks([]);
      setThoughts("");
      setRejected([]);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  const selected = people.find((p) => p.slug === personSlug);

  return (
    <div className="page consume" onPaste={onPaste}>
      <header className="page-head">
        <div>
          <h1>Consume</h1>
          <p className="sub">Feed Shōgai what you know. Gemini analyzes each source and updates one Markdown profile.</p>
        </div>
      </header>

      <div className="consume-grid">
        <div className="consume-form">
          <section className="card step-card">
            <h2>
              <span className="step-no">1</span> Who is this about?
            </h2>
            <div className="segmented">
              <button type="button" className={effectiveMode === "existing" ? "active" : ""} disabled={!people.length} onClick={() => setMode("existing")}>
                Someone in my circle
              </button>
              <button type="button" className={effectiveMode === "new" ? "active" : ""} onClick={() => setMode("new")}>
                Someone new
              </button>
            </div>
            {effectiveMode === "existing" ? (
              <label className="field">
                <span>Person</span>
                <select value={personSlug} onChange={(e) => setPersonSlug(e.target.value)}>
                  <option value="">Choose a person…</option>
                  {people.map((p) => (
                    <option key={p.slug} value={p.slug}>
                      {p.name}
                      {p.relationship ? ` — ${p.relationship}` : ""}
                    </option>
                  ))}
                </select>
                {selected?.summary && <small className="muted">{selected.summary}</small>}
              </label>
            ) : (
              <div className="field-row">
                <label className="field">
                  <span>Name</span>
                  <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. Linh Trần" />
                </label>
                <label className="field">
                  <span>Relationship to you</span>
                  <input value={newRel} onChange={(e) => setNewRel(e.target.value)} placeholder="e.g. college friend" />
                </label>
              </div>
            )}
          </section>

          <section className="card step-card">
            <h2>
              <span className="step-no">2</span> What do you know?
            </h2>
            <label className="field">
              <span>
                <Icon name="text" size={15} /> Personal thoughts
              </span>
              <textarea
                rows={6}
                value={thoughts}
                onChange={(e) => setThoughts(e.target.value)}
                placeholder="How did you meet? What do they care about? What's going on in their life? Birthdays, likes, sensitivities, promises… How do you feel about the relationship lately?"
              />
            </label>

            <div
              className={`dropzone ${dragOver ? "over" : ""}`}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
            >
              <Icon name="upload" size={26} />
              <strong>Drop photos, videos, voice memos, papers or text files</strong>
              <span className="muted small">click to browse · or paste a screenshot (⌘V)</span>
              <input
                ref={inputRef}
                type="file"
                multiple
                hidden
                accept="image/*,video/*,audio/*,.heic,.heif,.pdf,.txt,.md,.markdown,.csv,.tsv,.json,.html,.htm,.srt,.vtt,.xml,.yaml,.yml,.rtf,.log"
                onChange={(e) => {
                  addFiles(e.target.files);
                  e.target.value = "";
                }}
              />
            </div>
            {rejected.length > 0 && <p className="warn small">Unsupported, skipped: {rejected.join(", ")}</p>}

            {files.length > 0 && (
              <ul className="file-list">
                {files.map((f) => (
                  <li key={f.key} className="file-row">
                    {f.preview ? (
                      <img className="thumb" src={f.preview} alt="" />
                    ) : (
                      <span className={`kind-badge k-${f.kind}`}>
                        <Icon name={f.kind} size={18} />
                      </span>
                    )}
                    <div className="grow min0">
                      <div className="row gap-s">
                        <span className="truncate strong">{f.file.name}</span>
                        <span className="muted small nowrap">{formatBytes(f.file.size)}</span>
                      </div>
                      <input
                        className="note-input"
                        placeholder="Context for Gemini (optional): who, when, where…"
                        value={f.note}
                        onChange={(e) => setFiles((cur) => cur.map((x) => (x.key === f.key ? { ...x, note: e.target.value } : x)))}
                      />
                    </div>
                    {f.options.length > 1 ? (
                      <select
                        className="kind-select"
                        value={f.kind}
                        onChange={(e) =>
                          setFiles((cur) => cur.map((x) => (x.key === f.key ? { ...x, kind: e.target.value as SourceKind } : x)))
                        }
                      >
                        {f.options.map((o) => (
                          <option key={o} value={o}>
                            {o === "text" ? "Document" : KIND_META[o].label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className={`kind-chip k-${f.kind}`}>{KIND_META[f.kind].label}</span>
                    )}
                    <button type="button" className="icon-btn" title="Remove" onClick={() => removeFile(f.key)}>
                      <Icon name="x" size={15} />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <label className="field">
              <span>
                <Icon name="link" size={15} /> Links — YouTube videos, paper URLs (arXiv, DOI, PDF), web pages
              </span>
              <div className="row gap-s">
                <input
                  value={linkInput}
                  onChange={(e) => setLinkInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addLink();
                    }
                  }}
                  placeholder="https://…"
                />
                <button type="button" className="btn" onClick={addLink} disabled={!linkInput.trim()}>
                  Add
                </button>
              </div>
            </label>
            {links.length > 0 && (
              <ul className="file-list">
                {links.map((l) => (
                  <li key={l.key} className="file-row">
                    <span className={`kind-badge k-${l.kind}`}>
                      <Icon name={l.kind} size={18} />
                    </span>
                    <div className="grow min0">
                      <div className="truncate strong">{l.url}</div>
                      <input
                        className="note-input"
                        placeholder="Context for Gemini (optional)"
                        value={l.note}
                        onChange={(e) => setLinks((cur) => cur.map((x) => (x.key === l.key ? { ...x, note: e.target.value } : x)))}
                      />
                    </div>
                    <select
                      className="kind-select"
                      value={l.kind}
                      onChange={(e) => setLinks((cur) => cur.map((x) => (x.key === l.key ? { ...x, kind: e.target.value as SourceKind } : x)))}
                    >
                      {l.options.map((o) => (
                        <option key={o} value={o}>
                          {o === "text" ? "Web page" : KIND_META[o].label}
                        </option>
                      ))}
                    </select>
                    <button type="button" className="icon-btn" title="Remove" onClick={() => setLinks((cur) => cur.filter((x) => x.key !== l.key))}>
                      <Icon name="x" size={15} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {error && <div className="banner error">{error}</div>}
          <div className="submit-row">
            <span className="muted small">
              {files.length + links.length + (thoughts.trim() ? 1 : 0)} source(s) ready
            </span>
            <button type="button" className="btn primary big" disabled={!hasPerson || !hasInput || busy} onClick={submit}>
              <Icon name="sparkle" size={18} /> {busy ? "Uploading…" : "Consume with Gemini"}
            </button>
          </div>
        </div>

        <aside className="consume-side">
          {job ? (
            <JobPanel job={job} />
          ) : (
            <section className="card how">
              <h3>How Shōgai consumes</h3>
              <ul className="how-list">
                {PIPELINE.map((p) => (
                  <li key={p.kind}>
                    <span className={`kind-badge k-${p.kind}`}>
                      <Icon name={p.kind} size={16} />
                    </span>
                    <div>
                      <strong>{p.title}</strong>
                      <p className="muted small">{p.text}</p>
                    </div>
                  </li>
                ))}
              </ul>
              <p className="muted small">
                Each source becomes a note in <code>sources/</code>; Gemini then merges them into{" "}
                <code>profile.md</code>, citing every fact like <span className="cite static">s3</span>.
              </p>
            </section>
          )}
          {recent.length > 0 && (
            <section className="recent-jobs">
              <h4 className="muted small">Recent activity</h4>
              {recent.map((j) => (
                <button key={j.id} type="button" className="recent-job" onClick={() => setParams({ person: j.person, job: j.id })}>
                  <StatusIcon status={j.status} />
                  <span className="truncate">{j.personName}</span>
                  <span className="muted small">{j.items.length} sources</span>
                </button>
              ))}
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}

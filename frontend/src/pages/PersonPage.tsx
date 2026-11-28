import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, mediaUrl } from "../api";
import { Avatar } from "../components/Avatar";
import { Icon } from "../components/Icon";
import { Markdown } from "../components/Markdown";
import { PropertiesCard } from "../components/PropertiesCard";
import { useJobs, usePeople } from "../state";
import type { MediaFile, Person, SourceDetail, SourceRecord } from "../types";
import { formatBytes, KIND_META, timeAgo } from "../util";

type Tab = "profile" | "sources" | "media" | "markdown";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const stripFrontmatter = (md: string) => md.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, "");

function SourceItem({
  slug,
  source,
  open,
  onToggle,
  onDeleted,
}: {
  slug: string;
  source: SourceRecord;
  open: boolean;
  onToggle: () => void;
  onDeleted: () => void;
}) {
  const [detail, setDetail] = useState<SourceDetail>();
  const [error, setError] = useState<string>();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open && !detail) api.source(slug, source.id).then(setDetail).catch((e) => setError(errMsg(e)));
  }, [open, detail, slug, source.id]);

  useEffect(() => {
    if (open) ref.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [open]);

  async function remove() {
    if (!confirm(`Delete source ${source.id} “${source.title}”? It is moved to data/.trash. Rebuild the profile afterwards to drop its facts.`))
      return;
    try {
      await api.deleteSource(slug, source.id);
      onDeleted();
    } catch (e) {
      alert(errMsg(e));
    }
  }

  return (
    <div ref={ref} className={`source card ${open ? "open" : ""}`}>
      <button type="button" className="source-head" onClick={onToggle} aria-expanded={open}>
        <span className={`kind-badge k-${source.kind}`}>
          <Icon name={source.kind} size={18} />
        </span>
        <span className="min0 grow">
          <span className="source-title">
            <span className="cite static">{source.id}</span> {source.title}
          </span>
          <span className="source-meta">
            {KIND_META[source.kind]?.label ?? source.kind} · {new Date(source.created).toLocaleDateString()}
            {source.original ? ` · ${source.original}` : ""}
            {source.url ? ` · ${new URL(source.url).hostname}` : ""}
          </span>
          {!open && source.summary && <span className="source-summary">{source.summary}</span>}
        </span>
        <span className={`chev ${open ? "up" : ""}`}>⌄</span>
      </button>
      {open && (
        <div className="source-body">
          {error && <p className="error-text">{error}</p>}
          {detail ? <Markdown text={stripFrontmatter(detail.markdown)} slug={slug} /> : !error && <p className="muted">Loading…</p>}
          <div className="actions">
            <button type="button" className="btn ghost danger small" onClick={remove}>
              <Icon name="trash" size={14} /> Delete source
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function MediaGrid({ slug, media, sources }: { slug: string; media: MediaFile[]; sources: SourceRecord[] }) {
  // A .webm may hold sound only (recorded voice memos): its source says whether it was read as audio.
  const audioFiles = useMemo(
    () => new Set(sources.filter((s) => s.kind === "audio" && s.media).map((s) => s.media)),
    [sources],
  );
  if (!media.length) return <p className="muted center pad">No original files stored for this person.</p>;
  return (
    <div className="media-grid">
      {media.map((m) => {
        const url = mediaUrl(slug, m.path);
        const lower = m.name.toLowerCase();
        let preview;
        if (/\.(jpe?g|png|webp|gif)$/.test(lower)) preview = <img src={url} alt={m.name} loading="lazy" />;
        else if (/\.(mp4|m4v|mov|webm)$/.test(lower) && !audioFiles.has(m.path))
          preview = <video src={url} controls preload="metadata" />;
        else if (/\.(mp3|wav|m4a|aac|ogg|oga|opus|flac|aiff?|weba|webm)$/.test(lower))
          preview = (
            <div className="media-audio">
              <Icon name="audio" size={28} />
              <audio src={url} controls preload="none" />
            </div>
          );
        else
          preview = (
            <a className="media-file" href={url} target="_blank" rel="noreferrer">
              <Icon name="file" size={34} />
            </a>
          );
        return (
          <figure key={m.name} className="media-item card">
            {preview}
            <figcaption>
              <a href={url} target="_blank" rel="noreferrer" className="truncate">
                {m.name.replace(/^\d{8}-\d{6}-[0-9a-f]{4}-/, "")}
              </a>
              <span className="muted">{formatBytes(m.size)}</span>
            </figcaption>
          </figure>
        );
      })}
    </div>
  );
}

function RawEditor({ person, onSaved }: { person: Person; onSaved: (p: Person) => void }) {
  const [text, setText] = useState(person.raw);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  useEffect(() => setText(person.raw), [person.raw]);
  const dirty = text !== person.raw;

  async function save() {
    setBusy(true);
    setError(undefined);
    try {
      onSaved(await api.savePerson(person.slug, text));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card editor">
      <p className="muted small">
        This is <code>profile.md</code> itself. Keep the <code>name</code> in the frontmatter; the Source Log is
        regenerated automatically. A backup of every version is kept in <code>history/</code>.
      </p>
      <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      {error && <p className="error-text">{error}</p>}
      <div className="actions">
        <button type="button" className="btn ghost" disabled={!dirty || busy} onClick={() => setText(person.raw)}>
          Revert
        </button>
        <button type="button" className="btn primary" disabled={!dirty || busy} onClick={save}>
          {busy ? "Saving…" : "Save Markdown"}
        </button>
      </div>
    </div>
  );
}

export function PersonPage() {
  const { slug = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "profile";
  const openSource = params.get("source");
  const navigate = useNavigate();
  const { refresh } = usePeople();
  const { jobs, track } = useJobs();
  const [person, setPerson] = useState<Person | null>(null);
  const [sources, setSources] = useState<SourceRecord[]>([]);
  const [media, setMedia] = useState<MediaFile[]>([]);
  const [error, setError] = useState<string>();

  const finishedJobs = jobs.filter((j) => j.person === slug && j.status !== "running").length;
  const running = jobs.find((j) => j.person === slug && j.status === "running");

  const load = useCallback(async () => {
    try {
      const [p, s, m] = await Promise.all([api.person(slug), api.sources(slug), api.media(slug)]);
      setPerson(p);
      setSources(s);
      setMedia(m);
      setError(undefined);
    } catch (e) {
      setError(errMsg(e));
    }
  }, [slug]);

  useEffect(() => {
    void load();
  }, [load, finishedJobs]);

  const sourceMap = useMemo(() => new Map(sources.map((s) => [s.id, s])), [sources]);
  const setTab = (t: Tab) => setParams(t === "profile" ? {} : { tab: t });
  const cite = useCallback((id: string) => setParams({ tab: "sources", source: id }), [setParams]);

  async function rebuild() {
    if (!person) return;
    if (!confirm(`Re-merge all ${sources.length} sources into ${person.name}'s profile with Gemini?`)) return;
    try {
      track(await api.rebuild(slug));
    } catch (e) {
      alert(errMsg(e));
    }
  }

  async function remove() {
    if (!person) return;
    if (!confirm(`Delete ${person.name}? The folder is moved to data/.trash (not erased).`)) return;
    try {
      await api.deletePerson(slug);
      await refresh();
      navigate("/");
    } catch (e) {
      alert(errMsg(e));
    }
  }

  if (error && !person) {
    return (
      <div className="page narrow empty-state">
        <h2>Couldn’t open this person</h2>
        <p className="muted">{error}</p>
        <Link to="/" className="btn">
          Back to people
        </Link>
      </div>
    );
  }
  if (!person) return <div className="page muted">Loading…</div>;

  return (
    <div className="page">
      <header className="person-head">
        <Avatar person={person} size={92} />
        <div className="person-id min0">
          <h1>{person.name}</h1>
          <p className="rel">
            {person.relationship || "relationship not set"}
            {person.aliases.length > 0 && <span className="muted"> · aka {person.aliases.join(", ")}</span>}
          </p>
          {person.summary && <p className="summary">{person.summary}</p>}
          {person.tags.length > 0 && (
            <div className="tags">
              {person.tags.map((t) => (
                <span className="tag" key={t}>
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="head-actions col">
          <Link className="btn primary" to={`/consume?person=${encodeURIComponent(slug)}`}>
            <Icon name="consume" size={16} /> Add info
          </Link>
          <Link className="btn" to={`/advisor?focus=${encodeURIComponent(slug)}`}>
            <Icon name="chat" size={16} /> Ask advisor
          </Link>
          <Link className="btn" to={`/circle?focus=${encodeURIComponent(slug)}`}>
            <Icon name="graph" size={16} /> Show in circle
          </Link>
          <div className="row gap-s">
            <button type="button" className="icon-btn" title="Rebuild profile from all sources" onClick={rebuild} disabled={!sources.length || !!running}>
              <Icon name="refresh" size={16} />
            </button>
            <button type="button" className="icon-btn" title="Edit Markdown" onClick={() => setTab("markdown")}>
              <Icon name="edit" size={16} />
            </button>
            <button type="button" className="icon-btn danger" title="Delete person" onClick={remove}>
              <Icon name="trash" size={16} />
            </button>
          </div>
        </div>
      </header>

      <PropertiesCard
        person={person}
        onSaved={(p) => {
          setPerson(p);
          void refresh();
        }}
      />

      {running && (
        <div className="banner info">
          <span className="spinner" /> {running.stage ?? "Working…"} — this page refreshes automatically.
        </div>
      )}

      <div className="tabs" role="tablist">
        {(
          [
            ["profile", "Profile"],
            ["sources", `Sources · ${sources.length}`],
            ["media", `Media · ${media.length}`],
            ["markdown", "Markdown"],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {tab === "profile" && (
        <article className="card profile">
          <Markdown text={person.body} slug={slug} sources={sourceMap} onCite={cite} />
          <footer className="profile-foot muted small">
            Updated {timeAgo(person.updated)} · {person.source_count} sources · stored as{" "}
            <code>people/{slug}/profile.md</code>
          </footer>
        </article>
      )}

      {tab === "sources" && (
        <div className="sources">
          {!sources.length && (
            <p className="muted center pad">
              No sources yet. <Link to={`/consume?person=${encodeURIComponent(slug)}`}>Consume some information</Link>.
            </p>
          )}
          {sources
            .slice()
            .reverse()
            .map((s) => (
              <SourceItem
                key={s.id}
                slug={slug}
                source={s}
                open={openSource === s.id}
                onToggle={() => setParams(openSource === s.id ? { tab: "sources" } : { tab: "sources", source: s.id })}
                onDeleted={() => {
                  setParams({ tab: "sources" });
                  void load();
                  void refresh();
                }}
              />
            ))}
        </div>
      )}

      {tab === "media" && <MediaGrid slug={slug} media={media} sources={sources} />}

      {tab === "markdown" && (
        <RawEditor
          person={person}
          onSaved={(p) => {
            setPerson(p);
            void refresh();
            setTab("profile");
          }}
        />
      )}
    </div>
  );
}

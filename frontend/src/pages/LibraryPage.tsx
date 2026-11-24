import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Avatar } from "../components/Avatar";
import { Icon } from "../components/Icon";
import { useJobs, usePeople } from "../state";
import type { LibraryData, LibraryResource, ResourceType } from "../types";
import { timeAgo } from "../util";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

const TYPE_LABEL: Record<ResourceType, string> = {
  paper: "Paper",
  report: "Report",
  thesis: "Thesis",
  book: "Book",
  textbook: "Textbook",
  chapter: "Chapter",
  course: "Course",
  lecture: "Lecture",
  other: "Other",
};

const TYPE_ORDER = Object.keys(TYPE_LABEL) as ResourceType[];

function typeStyle(type: ResourceType): { icon: string; badge: string } {
  if (type === "book" || type === "textbook" || type === "chapter") return { icon: "book", badge: "k-audio" };
  if (type === "course" || type === "lecture") return { icon: "academic", badge: "k-image" };
  return { icon: "academic", badge: "k-academic" };
}

const VERDICT: Record<string, { label: string; cls: string }> = {
  supported: { label: "supported", cls: "ok" },
  partially_supported: { label: "partially supported", cls: "warn" },
  contested: { label: "contested", cls: "warn" },
  contradicted: { label: "contradicted", cls: "bad" },
  unverifiable: { label: "unverifiable", cls: "muted" },
};

/** Notes are Obsidian-flavoured Markdown: show quotes the way the note reads. */
function cleanQuote(quote: string): string {
  return quote
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s*(?:[-*+]|\d+\.)\s+/, "")
    .replace(/\*\*|__/g, "")
    .trim();
}

function ResourceCard({ resource }: { resource: LibraryResource }) {
  const { people } = usePeople();
  const navigate = useNavigate();
  const style = typeStyle(resource.type);
  const meta = [resource.authors.join(", "), resource.year ?? "", resource.field ?? ""].filter(Boolean).join(" · ");
  const personOf = (slug: string, name: string) =>
    people.find((p) => p.slug === slug) ?? { slug, name, avatar: null };
  const openSource = (slug: string, id: string) =>
    navigate(`/people/${encodeURIComponent(slug)}?tab=sources&source=${id}`);

  return (
    <article className="card resource">
      <span className={`kind-badge ${style.badge}`}>
        <Icon name={style.icon} size={18} />
      </span>
      <div className="grow min0">
        <div className="resource-head">
          <h3>{resource.title}</h3>
          <span className={`type-pill ${style.badge}`}>{TYPE_LABEL[resource.type] ?? resource.type}</span>
        </div>
        {(meta || resource.identified) && (
          <p className="resource-meta">
            {meta}
            {resource.identified && (
              <span
                className="identified"
                title="The note only names this work in part; Gemini completed the title, authors or year. The mention itself is quoted below."
              >
                details completed by Gemini
              </span>
            )}
          </p>
        )}
        <ul className="mentions">
          {resource.mentions.map((m, i) => (
            <li key={`m${i}`}>
              <div className="mention-head">
                <Link to={`/people/${encodeURIComponent(m.person)}`} className="mention-person">
                  <Avatar person={personOf(m.person, m.name)} size={22} />
                  {m.name}
                </Link>
                <button type="button" className="cite" title="Open the note" onClick={() => openSource(m.person, m.source_id)}>
                  {m.source_id}
                </button>
                {m.context && <span className="muted small min0">{m.context}</span>}
              </div>
              <blockquote className="mention-quote">{cleanQuote(m.quote)}</blockquote>
            </li>
          ))}
          {resource.consumed.map((c, i) => (
            <li key={`c${i}`}>
              <div className="mention-head">
                <Link to={`/people/${encodeURIComponent(c.person)}`} className="mention-person">
                  <Avatar person={personOf(c.person, c.name)} size={22} />
                  {c.name}
                </Link>
                <button type="button" className="cite" title="Open the paper analysis" onClick={() => openSource(c.person, c.source_id)}>
                  {c.source_id}
                </button>
                <span className="muted small">paper consumed{c.relation === "author" ? " · they are an author" : ""}</span>
              </div>
              {c.verdicts.length > 0 && (
                <div className="verdicts">
                  <span className="muted small">Fact-check:</span>
                  {c.verdicts.map((v, j) => (
                    <span key={j} className={`verdict ${VERDICT[v]?.cls ?? "muted"}`}>
                      {VERDICT[v]?.label ?? v}
                    </span>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </article>
  );
}

export function LibraryPage() {
  const { jobs, track } = useJobs();
  const [data, setData] = useState<LibraryData>();
  const [error, setError] = useState<string>();
  const [query, setQuery] = useState("");
  const [type, setType] = useState<ResourceType | "all">("all");
  const [busy, setBusy] = useState(false);

  const scanJob = jobs.find((j) => j.kind === "library");
  const running = scanJob?.status === "running";
  const finishedScans = jobs.filter((j) => j.kind === "library" && j.status !== "running").length;

  const load = useCallback(async () => {
    try {
      setData(await api.library());
      setError(undefined);
    } catch (e) {
      setError(errMsg(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, finishedScans]);

  async function scan(force = false) {
    if (force && !confirm("Re-read every note with Gemini? Normally only new or edited notes are scanned.")) return;
    setBusy(true);
    try {
      track(await api.scanLibrary(force));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  const resources = useMemo(() => data?.resources ?? [], [data]);
  const counts = useMemo(() => {
    const c = new Map<ResourceType, number>();
    resources.forEach((r) => c.set(r.type, (c.get(r.type) ?? 0) + 1));
    return c;
  }, [resources]);

  const needle = query.trim().toLowerCase();
  const shown = resources.filter((r) => {
    if (type !== "all" && r.type !== type) return false;
    if (!needle) return true;
    const hay = [
      r.title,
      ...r.authors,
      r.field ?? "",
      ...r.mentions.flatMap((m) => [m.name, m.quote, m.context]),
      ...r.consumed.map((c) => c.name),
    ]
      .join(" ")
      .toLowerCase();
    return hay.includes(needle);
  });

  const status = data?.status;
  const done = scanJob?.items.filter((i) => i.status !== "running" && i.status !== "queued").length ?? 0;

  return (
    <div className="page library">
      <header className="page-head">
        <div>
          <h1>Academic resources</h1>
          <p className="sub">
            Only works mentioned in your notes. Each one shows the exact line that mentions it.
          </p>
        </div>
        <div className="head-actions">
          <button
            type="button"
            className="btn primary"
            disabled={busy || running || !status || status.pending === 0}
            onClick={() => scan()}
          >
            <Icon name="sparkle" size={16} />
            {!status
              ? "Loading…"
              : running
              ? "Scanning…"
              : status.pending > 0
                ? `Scan ${status.pending} new note${status.pending === 1 ? "" : "s"}`
                : "All notes scanned"}
          </button>
          <button
            type="button"
            className="icon-btn"
            title="Rescan every note"
            disabled={busy || running || !status?.notes}
            onClick={() => scan(true)}
          >
            <Icon name="refresh" size={16} />
          </button>
        </div>
      </header>

      {status && (
        <p className="library-status muted small">
          {status.scanned} of {status.notes} text notes scanned
          {status.updated ? ` · last scan ${timeAgo(status.updated)}` : ""}
          {" · "}stored in <code>data/library/resources.md</code>
        </p>
      )}

      {error && <div className="banner error">{error}</div>}
      {running && scanJob && (
        <div className="banner info">
          <span className="spinner" />
          <span className="grow">{scanJob.stage ?? "Starting…"}</span>
          {scanJob.items.length > 0 && (
            <span className="muted small">
              batch {Math.min(done + 1, scanJob.items.length)} of {scanJob.items.length}
            </span>
          )}
        </div>
      )}
      {scanJob?.status === "error" && <div className="banner error">Last scan failed: {scanJob.error}</div>}
      {scanJob?.status === "done" && (scanJob.result?.failed.length ?? 0) > 0 && (
        <div className="banner error">
          {scanJob.result!.failed.length} batch(es) failed: {scanJob.result!.failed[0].message} — run the scan again to retry
          them.
        </div>
      )}

      {status && status.notes === 0 && (
        <div className="empty-state">
          <div className="enso" aria-hidden="true" />
          <h2>No text notes yet</h2>
          <p>
            Personal thoughts and text files you consume are kept as notes. Papers, books and courses they mention will
            be listed here.
          </p>
          <Link to="/consume" className="btn primary">
            Consume a note
          </Link>
        </div>
      )}

      {status && status.notes > 0 && status.scanned === 0 && !running && (
        <div className="empty-state">
          <div className="enso" aria-hidden="true" />
          <h2>Scan your notes</h2>
          <p>
            Gemini reads your {status.notes} notes and lists the papers, books, courses and reports they mention. Each
            entry has to quote its note word for word, so nothing that isn’t written in your notes can appear here.
          </p>
          <button type="button" className="btn primary" disabled={busy} onClick={() => scan()}>
            <Icon name="sparkle" size={16} /> Scan {status.notes} notes
          </button>
        </div>
      )}

      {resources.length > 0 && (
        <div className="library-tools">
          <div className="type-filter">
            <button type="button" className={type === "all" ? "active" : ""} onClick={() => setType("all")}>
              All <span>{resources.length}</span>
            </button>
            {TYPE_ORDER.filter((t) => counts.has(t)).map((t) => (
              <button key={t} type="button" className={type === t ? "active" : ""} onClick={() => setType(t)}>
                {TYPE_LABEL[t]} <span>{counts.get(t)}</span>
              </button>
            ))}
          </div>
          <label className="search">
            <Icon name="search" size={16} />
            <input placeholder="Search title, author, person…" value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
        </div>
      )}

      {status && status.scanned > 0 && resources.length === 0 && !running && (
        <p className="muted center pad">None of your scanned notes mention an academic resource yet.</p>
      )}

      <div className="resources">
        {shown.map((r) => (
          <ResourceCard key={r.id} resource={r} />
        ))}
      </div>
      {resources.length > 0 && !shown.length && <p className="muted center pad">Nothing matches this filter.</p>}
    </div>
  );
}

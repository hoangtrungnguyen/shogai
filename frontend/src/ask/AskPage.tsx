import { useEffect, useMemo, useState } from "react";
import { publicApi } from "../api";
import { Icon } from "../components/Icon";
import { Markdown } from "../components/Markdown";
import type { PublicInfo } from "../types";

/** The public page: the owner's public notes, read-only. Questions go through their relay, with a key. */
export function AskPage() {
  const [info, setInfo] = useState<PublicInfo>();
  const [error, setError] = useState<string>();
  const [query, setQuery] = useState("");

  useEffect(() => {
    publicApi
      .info()
      .then(setInfo)
      .catch((e) => setError(`Couldn’t load the notes: ${e instanceof Error ? e.message : String(e)}`));
  }, []);

  const name = info?.owner.name ?? null;
  const who = name ?? "the author";

  useEffect(() => {
    document.title = name ? `${name} · public notes` : "Public notes";
  }, [name]);

  const files = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const all = info?.files ?? [];
    return needle ? all.filter((f) => `${f.title}\n${f.body}`.toLowerCase().includes(needle)) : all;
  }, [info, query]);

  return (
    <div className="visitor">
      <header className="visitor-head">
        <div className="enso small" aria-hidden="true" />
        <div className="min0">
          <h1>{name ?? "Public notes"}</h1>
          {info?.owner.headline && <p className="sub">{info.owner.headline}</p>}
        </div>
      </header>

      <div className="visitor-grid">
        <main className="visitor-notes min0">
          {error && <div className="banner error">{error}</div>}
          {!info && !error && <p className="muted">Loading…</p>}
          {info && info.files.length > 1 && (
            <label className="search visitor-search">
              <Icon name="search" size={16} />
              <input placeholder="Search these notes" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
          )}
          {info && info.files.length === 0 && (
            <div className="card visitor-note">
              <p className="muted">Nothing published yet.</p>
            </div>
          )}
          {files.map((f) => (
            <article key={f.path} className="card visitor-note">
              <Markdown text={f.body} />
            </article>
          ))}
          {info && info.files.length > 0 && files.length === 0 && (
            <p className="muted">No note mentions “{query.trim()}”.</p>
          )}
        </main>

        {info && (
          <aside className="visitor-side">
            <section className="card ask-card">
              <h2>Ask {name ?? "a question"}</h2>
              <p className="muted small">
                To ask {who} something that isn’t here, you need a key from them: only people {who} gave a key can
                send a question. {who.charAt(0).toUpperCase() + who.slice(1)} answers it personally; nothing is
                answered automatically.
              </p>
              {info.relay ? (
                <a className="btn primary wide" href={info.relay} target="_blank" rel="noreferrer">
                  <Icon name="open" size={15} /> Ask with your key
                </a>
              ) : (
                <p className="muted small">Ask {who} for a key and the address of their relay.</p>
              )}
            </section>
          </aside>
        )}
      </div>

      <footer className="visitor-foot muted small">
        For AI agents: <a href="/llms.txt">/llms.txt</a> · <a href="/docs">API</a>
      </footer>
    </div>
  );
}

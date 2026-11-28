import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { Markdown } from "../components/Markdown";
import type { MeInfo, PublicFileDetail, PublicListener } from "../types";
import { formatBytes, timeAgo } from "../util";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const stripFrontmatter = (md: string) => md.replace(/^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/, "");

const TEMPLATE = `---
name: Your Name
headline: What you do, in one line
---

# About me

A few sentences about who you are.

## What I do

## What I'm into

## How to reach me
`;

function ListenerCard({ listener }: { listener: PublicListener }) {
  if (!listener.enabled) {
    return (
      <div className="banner info">
        <Icon name="globe" size={16} />
        <span>
          The public page is off (<code>SHOGAI_PUBLIC_PORT=0</code>). You can still preview it here.
        </span>
      </div>
    );
  }
  const address = listener.url ?? listener.local_url ?? "";
  return (
    <section className="card listener">
      <span className={`dot ${listener.running ? "ok" : "bad"}`} />
      <div className="grow min0">
        <p className="listener-line">
          <strong>Public page</strong>
          {listener.running ? (
            <a href={address} target="_blank" rel="noreferrer">
              {address}
            </a>
          ) : (
            <span className="error-text">not running{listener.error ? ` — ${listener.error}` : ""}</span>
          )}
          {listener.url && listener.local_url && <span className="muted small">on this machine: {listener.local_url}</span>}
        </p>
        <p className="muted small">
          This address serves your public notes, read-only; people ask you questions on your relay, with a key (see{" "}
          <Link to="/requests">Requests</Link>). To share the notes, expose only this address, e.g.{" "}
          <code>cloudflared tunnel --url {listener.local_url}</code>, and set <code>SHOGAI_PUBLIC_URL</code> to the
          address you get. Never expose port 8000: it holds your whole circle.
        </p>
      </div>
    </section>
  );
}

export function MePage() {
  const [params, setParams] = useSearchParams();
  const [me, setMe] = useState<MeInfo>();
  const [file, setFile] = useState<PublicFileDetail>();
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const current = params.get("file") ?? me?.files[0]?.path ?? null;
  const dirty = !!file && text !== (file.raw ?? "");

  const load = useCallback(async () => {
    try {
      const fresh = await api.me();
      setMe(fresh);
      setError(undefined);
      return fresh;
    } catch (e) {
      setError(errMsg(e));
      return undefined;
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!current) {
      setFile(undefined);
      return;
    }
    let live = true;
    api
      .meFile(current)
      .then((f) => {
        if (!live) return;
        setFile(f);
        setText(f.raw ?? "");
      })
      .catch((e) => live && setError(errMsg(e)));
    return () => {
      live = false;
    };
  }, [current]);

  function select(path: string) {
    if (path === current) return;
    if (dirty && !confirm("Discard your unsaved changes?")) return;
    setParams({ file: path });
    setMode("preview");
  }

  async function create(path: string, content: string) {
    setBusy(true);
    setError(undefined);
    try {
      const saved = await api.saveMeFile(path, content);
      await load();
      setParams({ file: saved.path });
      setMode("edit");
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function newFile() {
    if (dirty && !confirm("Discard your unsaved changes?")) return;
    const input = prompt("Name of the new file, e.g. work.md or notes/projects.md")?.trim();
    if (!input) return;
    const path = /\.md$/i.test(input) ? input : `${input}.md`;
    const existing = me?.files.find((f) => f.path.toLowerCase() === path.toLowerCase());
    if (existing) {
      setParams({ file: existing.path });
      return;
    }
    const title = path.split("/").pop()!.replace(/\.md$/i, "").replace(/[-_]+/g, " ").trim();
    await create(path, `# ${title.charAt(0).toUpperCase()}${title.slice(1)}\n`);
  }

  async function save() {
    if (!file) return;
    setBusy(true);
    setError(undefined);
    try {
      const saved = await api.saveMeFile(file.path, text);
      setFile(saved);
      setText(saved.raw ?? "");
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!file || !confirm(`Delete ${file.path}? It stops being public and is moved to data/.trash.`)) return;
    try {
      await api.deleteMeFile(file.path);
      setFile(undefined);
      const fresh = await load();
      setParams(fresh?.files[0] ? { file: fresh.files[0].path } : {});
      setMode("preview");
    } catch (e) {
      setError(errMsg(e));
    }
  }

  return (
    <div className="page me">
      <header className="page-head">
        <div>
          <h1>About me</h1>
          <p className="sub">
            Markdown about you that anyone can read and search, people and AI agents alike. Put here only what you’d
            share with the world.
          </p>
        </div>
        <div className="head-actions">
          <a className="btn" href="/ask.html" target="_blank" rel="noreferrer">
            <Icon name="open" size={16} /> Preview public page
          </a>
          <button type="button" className="btn primary" onClick={newFile} disabled={busy || !me}>
            <Icon name="plus" size={16} /> New file
          </button>
        </div>
      </header>

      {me && <ListenerCard listener={me.public} />}
      {error && <div className="banner error">{error}</div>}
      {!me && !error && <p className="muted">Loading…</p>}

      {me && me.files.length === 0 && (
        <div className="empty-state">
          <div className="enso" aria-hidden="true" />
          <h2>Nothing public yet</h2>
          <p>
            Write a few notes about yourself: what you do, what you’re into, how to reach you. Anyone can read them on
            your public page, and they ground the replies Gemini drafts for you. For anything else, people with one of
            your keys ask you on your relay, under <Link to="/requests">Requests</Link>.
          </p>
          <button type="button" className="btn primary" disabled={busy} onClick={() => create("about.md", TEMPLATE)}>
            <Icon name="edit" size={16} /> Start with about.md
          </button>
        </div>
      )}

      {me && me.files.length > 0 && (
        <div className="me-grid">
          <nav className="me-files card" aria-label="Files in your public folder">
            {me.files.map((f) => (
              <button
                key={f.path}
                type="button"
                className={`me-file ${f.path === current ? "active" : ""}`}
                onClick={() => select(f.path)}
              >
                <span className="me-file-title truncate">{f.title}</span>
                <span className="me-file-meta">
                  <code className="truncate">{f.path}</code>
                  {f.draft && (
                    <span className="draft-pill" title="draft: true in the frontmatter — not public">
                      Draft
                    </span>
                  )}
                </span>
              </button>
            ))}
            <p className="me-folder muted small truncate" title={me.folder}>
              Folder: <code>{me.folder}</code>
            </p>
          </nav>

          {file && (
            <section className="me-editor min0">
              <div className="me-editor-head">
                <div className="segmented">
                  <button type="button" className={mode === "preview" ? "active" : ""} onClick={() => setMode("preview")}>
                    Preview
                  </button>
                  <button type="button" className={mode === "edit" ? "active" : ""} onClick={() => setMode("edit")}>
                    Edit{dirty ? " •" : ""}
                  </button>
                </div>
                <span className="muted small grow">
                  {file.draft ? <span className="draft-pill">Draft — not public</span> : "Public"} · updated{" "}
                  {timeAgo(file.updated)} · {formatBytes(file.size)}
                </span>
                <button type="button" className="icon-btn danger" title="Delete this file" onClick={remove}>
                  <Icon name="trash" size={16} />
                </button>
              </div>

              {mode === "preview" ? (
                <article className="card me-preview">
                  {stripFrontmatter(text).trim() ? (
                    <Markdown text={stripFrontmatter(text)} />
                  ) : (
                    <p className="muted">This file is empty.</p>
                  )}
                </article>
              ) : (
                <div className="card editor">
                  <p className="muted small">
                    Markdown with optional frontmatter. In <code>about.md</code>, <code>name</code> and{" "}
                    <code>headline</code> introduce you on the public page. Add <code>draft: true</code> to keep a file
                    private while you work on it.
                  </p>
                  <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
                  <div className="actions">
                    <button type="button" className="btn ghost" disabled={!dirty || busy} onClick={() => setText(file.raw ?? "")}>
                      Revert
                    </button>
                    <button type="button" className="btn primary" disabled={!dirty || busy} onClick={save}>
                      {busy ? "Saving…" : "Save"}
                    </button>
                  </div>
                </div>
              )}
            </section>
          )}
        </div>
      )}
    </div>
  );
}

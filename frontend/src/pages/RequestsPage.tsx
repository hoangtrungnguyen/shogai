import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Avatar } from "../components/Avatar";
import { Icon } from "../components/Icon";
import { Markdown } from "../components/Markdown";
import { useInbox } from "../state";
import type { InfoRequest, RelayInfo, RelayKey, RequestCounts, RequestStatus } from "../types";
import { timeAgo } from "../util";

type Filter = RequestStatus | "all";
type Busy = "send" | "decline" | "draft" | null;

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

const FILTERS: [Filter, string][] = [
  ["pending", "Waiting"],
  ["replied", "Replied"],
  ["declined", "Declined"],
  ["all", "All"],
];

const STATUS_LABEL: Record<RequestStatus, string> = { pending: "Waiting", replied: "Replied", declined: "Declined" };

/** shk_AbCd…WxYz */
const maskKey = (key: string) => (key.length > 16 ? `${key.slice(0, 8)}…${key.slice(-4)}` : key);

/** Opens the relay's page with the key filled in (the key stays in the link's #fragment, never sent to a server). */
const keyLink = (server: string, key: string) => `${server}/#key=${encodeURIComponent(key)}`;

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    window.prompt("Copy:", text);
    return false;
  }
}

/* ------------------------------------------------------------------ relay */

function KeyRow({ server, entry, fresh, onRevoke }: { server: string; entry: RelayKey; fresh: boolean; onRevoke: () => void }) {
  const [copied, setCopied] = useState<"link" | "key" | null>(null);
  const [shown, setShown] = useState(fresh);

  async function copyIt(what: "link" | "key") {
    if (await copy(what === "link" ? keyLink(server, entry.key) : entry.key)) {
      setCopied(what);
      window.setTimeout(() => setCopied(null), 1500);
    }
  }

  return (
    <li className={`key-row ${fresh ? "fresh" : ""}`}>
      <div className="key-main min0">
        <span className="key-label truncate">{entry.label}</span>
        <button type="button" className="key-secret truncate" title={shown ? "Hide" : "Show the key"} onClick={() => setShown(!shown)}>
          {shown ? entry.key : maskKey(entry.key)}
        </button>
        {fresh && <span className="muted small">Send {entry.label} the link: it opens your relay with this key filled in.</span>}
      </div>
      <button type="button" className="btn ghost small" onClick={() => void copyIt("link")}>
        <Icon name={copied === "link" ? "check" : "link"} size={14} /> {copied === "link" ? "Copied" : "Copy link"}
      </button>
      <button type="button" className="btn ghost small" onClick={() => void copyIt("key")}>
        <Icon name={copied === "key" ? "check" : "copy"} size={14} /> {copied === "key" ? "Copied" : "Copy key"}
      </button>
      <button type="button" className="icon-btn tiny danger" title="Revoke this key" onClick={onRevoke}>
        <Icon name="trash" size={13} />
      </button>
    </li>
  );
}

function RelayPanel({ relay, onChange, onSynced }: { relay: RelayInfo; onChange: (r: RelayInfo) => void; onSynced: () => void }) {
  const [server, setServer] = useState("");
  const [code, setCode] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [note, setNote] = useState<string>();
  const [fresh, setFresh] = useState<string>();

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(undefined);
    setNote(undefined);
    try {
      await action();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  const connect = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      onChange(await api.relayConnect(server.trim(), code.trim()));
      onSynced();
    });
  };

  const sync = () =>
    run(async () => {
      const result = await api.relaySync();
      onChange(result);
      if (!result.error) setNote(`${result.added} new · ${result.sent} repl${result.sent === 1 ? "y" : "ies"} sent`);
      onSynced();
    });

  const createKey = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const result = await api.relayCreateKey(label.trim());
      onChange(result.relay);
      setFresh(result.key.id);
      setLabel("");
    });
  };

  const revoke = (entry: RelayKey) => {
    if (!confirm(`Revoke the key for ${entry.label}? It stops working at once, and the relay deletes the questions sent with it (your copies here stay).`))
      return;
    void run(async () => onChange(await api.relayRevokeKey(entry.id)));
  };

  const disconnect = () => {
    if (!confirm("Disconnect from the relay? Your account there is deleted, with all your keys and the questions waiting there. Questions already here stay."))
      return;
    void run(async () => {
      const result = await api.relayDisconnect();
      onChange(result);
      if (result.warning) setError(result.warning);
    });
  };

  if (!relay.connected) {
    return (
      <section className="card relay-card">
        <h2>Connect your relay</h2>
        <p className="muted small">
          People ask you things on a Shōgai Relay, with a key you give them. This app picks their questions up and sends
          your replies back, so nothing on this computer is exposed. Run a relay with <code>relay/docker-compose.yml</code>,
          or use one a friend runs.
        </p>
        <form onSubmit={connect}>
          <div className="field-row">
            <label className="field">
              <span>Relay address</span>
              <input required placeholder="https://relay.example.com" value={server} onChange={(e) => setServer(e.target.value)} />
            </label>
            <label className="field">
              <span>
                Sign-up code <em className="muted">(if the relay has one)</em>
              </span>
              <input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" />
            </label>
          </div>
          {error && <p className="error-text">{error}</p>}
          <button type="submit" className="btn primary" disabled={busy || !server.trim()}>
            {busy ? "Connecting…" : "Connect"}
          </button>
        </form>
      </section>
    );
  }

  return (
    <section className="card relay-card">
      <div className="relay-head">
        <span className={`dot ${relay.error ? "bad" : "ok"}`} />
        <div className="grow min0">
          <strong>Relay</strong>{" "}
          <a href={relay.share_url ?? "#"} target="_blank" rel="noreferrer" title={`This app reaches it at ${relay.server}`}>
            {relay.share_url}
          </a>
          <span className="muted small"> · {relay.last_sync ? `synced ${timeAgo(relay.last_sync)}` : "not synced yet"}</span>
          {note && <span className="muted small"> · {note}</span>}
        </div>
        <button type="button" className="btn small" disabled={busy} onClick={() => void sync()}>
          {busy ? <span className="spinner" /> : <Icon name="refresh" size={14} />} Sync now
        </button>
        <button type="button" className="btn ghost small danger" disabled={busy} onClick={disconnect}>
          Disconnect
        </button>
      </div>
      {relay.insecure && (
        <div className="banner error small">
          This relay uses plain http, so keys and replies cross the network unencrypted. Use https for a relay on the
          internet.
        </div>
      )}
      {relay.error && <div className="banner error small">{relay.error}</div>}
      {error && <div className="banner error small">{error}</div>}

      <div className="keys">
        <div className="keys-head">
          <h3>Keys</h3>
          <p className="muted small">
            Whoever has one of your keys can ask you questions; only they see your answers. Give each person their own
            key, and revoke it to cut them off.
          </p>
        </div>
        {relay.keys.length === 0 && <p className="muted small">No keys yet. Make one for someone you want to hear from.</p>}
        <ul className="key-list">
          {relay.keys.map((k) => (
            <KeyRow key={k.id} server={relay.share_url ?? ""} entry={k} fresh={k.id === fresh} onRevoke={() => revoke(k)} />
          ))}
        </ul>
        <form className="key-new" onSubmit={createKey}>
          <input
            className="note-input"
            placeholder="Who is this key for? e.g. Linh"
            maxLength={80}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
          <button type="submit" className="btn small" disabled={busy}>
            <Icon name="plus" size={14} /> New key
          </button>
        </form>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ requests */

function DeliveryNote({ request }: { request: InfoRequest }) {
  if (!request.relay_id) return null; // sent through the old public-page form, before the relay
  if (request.relay_gone) {
    return (
      <p className="delivery warn small">
        This question’s key was revoked, so {request.name || "the sender"} can’t see a reply on the relay.
      </p>
    );
  }
  if (request.status === "pending") return null;
  return request.synced ? (
    <p className="delivery muted small">
      <Icon name="check" size={13} className="ok" /> On the relay: {request.name || "the sender"} can read it.
    </p>
  ) : (
    <p className="delivery warn small">Not on the relay yet: it is sent at the next sync.</p>
  );
}

function RequestCard({ request, onChanged }: { request: InfoRequest; onChanged: (note?: string) => void }) {
  const [editing, setEditing] = useState(request.status === "pending");
  const [text, setText] = useState(request.status === "replied" ? request.reply : "");
  const [instructions, setInstructions] = useState("");
  const [publish, setPublish] = useState(false);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string>();
  const who = request.name || "Someone";

  async function run(kind: Busy, action: () => Promise<string | null | void>, changes = true) {
    setBusy(kind);
    setError(undefined);
    try {
      const relayError = await action();
      if (changes) onChanged(relayError ? `Your answer is saved here; the relay will get it at the next sync (${relayError}).` : undefined);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  const send = () => run("send", async () => (await api.reply(request.id, text, publish)).relay_error);

  function draft() {
    if (text.trim() && !confirm("Replace what you wrote with a draft?")) return;
    void run("draft", async () => setText((await api.draftReply(request.id, instructions)).draft), false);
  }

  function decline() {
    const note = text.trim() ? " They will see your message." : "";
    if (!confirm(`Decline ${who}’s question?${note}`)) return;
    void run("decline", async () => (await api.decline(request.id, text)).relay_error);
  }

  async function remove() {
    if (!confirm(`Delete this question from ${who}? It is moved to data/.trash and deleted on the relay.`)) return;
    try {
      await api.deleteRequest(request.id);
      onChanged();
    } catch (e) {
      alert(errMsg(e));
    }
  }

  return (
    <article className={`card request ${request.status}`}>
      <header className="request-head">
        <div className="request-who min0">
          <strong>{who}</strong>
          <span className="muted small">
            {request.key_id ? (
              <span className="key-chip" title="The key this question was sent with">
                <Icon name="link" size={12} /> {request.key_label ?? "a revoked key"}
              </span>
            ) : (
              <span className="key-chip old" title="Sent through your public page's old form, before the relay">
                public page
              </span>
            )}{" "}
            · {timeAgo(request.created)}
          </span>
        </div>
        <span className={`status-pill ${request.status}`}>{STATUS_LABEL[request.status]}</span>
        <button type="button" className="icon-btn tiny" title="Delete" onClick={remove}>
          <Icon name="trash" size={13} />
        </button>
      </header>

      {request.match && (
        <p className="match-hint">
          <Avatar person={request.match} size={20} />
          <span>
            Same name as <Link to={`/people/${encodeURIComponent(request.match.slug)}`}>{request.match.name}</Link>
            {request.match.relationship ? ` (${request.match.relationship})` : ""} in your circle. Not verified: the key
            tells you more about who sent it.
          </span>
        </p>
      )}

      <p className="request-question">{request.question}</p>

      {!editing && request.status !== "pending" && (
        <div className="request-answer">
          <span className="muted small">
            {request.status === "replied" ? "You replied" : "You declined"}
            {request.answered ? ` ${timeAgo(request.answered)}` : ""}
          </span>
          {request.reply ? <Markdown text={request.reply} /> : <p className="muted small">No message.</p>}
          <button
            type="button"
            className="btn small ghost"
            onClick={() => {
              setText(request.reply);
              setEditing(true);
            }}
          >
            <Icon name="edit" size={14} /> Change
          </button>
        </div>
      )}

      <DeliveryNote request={request} />

      {editing && (
        <div className="request-composer">
          <div className="draft-row">
            <input
              className="note-input"
              placeholder="Optional: what the draft should say (e.g. yes, share my work email)"
              value={instructions}
              maxLength={4000}
              onChange={(e) => setInstructions(e.target.value)}
            />
            <button type="button" className="btn small" disabled={!!busy} onClick={draft}>
              {busy === "draft" ? <span className="spinner" /> : <Icon name="sparkle" size={14} />}
              Draft from my public notes
            </button>
          </div>
          <textarea
            className="reply-input"
            rows={5}
            maxLength={20000}
            placeholder={`Your reply to ${who}…`}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <label className="check">
            <input type="checkbox" checked={publish} onChange={(e) => setPublish(e.target.checked)} />
            <span>
              Also add this question and reply to my public notes (<code>faq.md</code>)
            </span>
          </label>
          {error && <p className="error-text">{error}</p>}
          <div className="actions">
            {request.status !== "pending" && (
              <button type="button" className="btn ghost" disabled={!!busy} onClick={() => setEditing(false)}>
                Cancel
              </button>
            )}
            <button type="button" className="btn ghost danger" disabled={!!busy} onClick={decline}>
              {busy === "decline" ? "Declining…" : "Decline"}
            </button>
            <button type="button" className="btn primary" disabled={!!busy || !text.trim()} onClick={() => void send()}>
              <Icon name="send" size={15} /> {busy === "send" ? "Sending…" : "Send reply"}
            </button>
          </div>
        </div>
      )}
    </article>
  );
}

export function RequestsPage() {
  const [params, setParams] = useSearchParams();
  const filter = (params.get("status") as Filter | null) ?? "pending";
  const inbox = useInbox();
  const [requests, setRequests] = useState<InfoRequest[]>();
  const [counts, setCounts] = useState<RequestCounts>();
  const [relay, setRelay] = useState<RelayInfo>();
  const [error, setError] = useState<string>();
  const [note, setNote] = useState<string>();

  const load = useCallback(async () => {
    try {
      const data = await api.requests(filter === "all" ? undefined : filter);
      setRequests(data.requests);
      setCounts(data.counts);
      setError(undefined);
    } catch (e) {
      setError(errMsg(e));
    }
  }, [filter]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api
      .relay()
      .then(setRelay)
      .catch((e) => setError(errMsg(e)));
  }, []);

  // New questions arrive while the page is open: reload when the sidebar's count changes.
  const seenPending = useRef(inbox.pending);
  useEffect(() => {
    if (inbox.pending !== seenPending.current) {
      seenPending.current = inbox.pending;
      void load();
      void api.relay().then(setRelay).catch(() => undefined);
    }
  }, [inbox.pending, load]);

  const changed = useCallback(
    (message?: string) => {
      setNote(message);
      void load();
      void inbox.refresh();
    },
    [load, inbox],
  );

  return (
    <div className="page requests">
      <header className="page-head">
        <div>
          <h1>Requests</h1>
          <p className="sub">
            Questions people asked you on your relay, with one of your keys. Nothing is answered automatically: each
            waits here for your reply, which goes back to the relay for them to read.
          </p>
        </div>
        <Link className="btn" to="/me">
          <Icon name="user" size={16} /> Your public notes
        </Link>
      </header>

      {error && <div className="banner error">{error}</div>}
      {relay && <RelayPanel relay={relay} onChange={setRelay} onSynced={() => changed()} />}
      {note && <div className="banner info">{note}</div>}

      <div className="type-filter request-filter">
        {FILTERS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={filter === key ? "active" : ""}
            onClick={() => setParams(key === "pending" ? {} : { status: key })}
          >
            {label} <span>{counts ? counts[key] : ""}</span>
          </button>
        ))}
      </div>

      {requests && requests.length === 0 && (
        <div className="empty-state">
          <div className="enso small" aria-hidden="true" />
          {filter === "pending" ? (
            <>
              <h2>Nothing waiting</h2>
              <p>
                {relay?.connected
                  ? "When someone asks you something with one of your keys, it shows up here until you reply."
                  : "Connect a relay above, then give people a key: their questions show up here."}
              </p>
            </>
          ) : (
            <p className="muted">Nothing here yet.</p>
          )}
        </div>
      )}

      <div className="requests-list">
        {requests?.map((r) => (
          <RequestCard key={`${r.id}-${r.updated}-${r.synced}`} request={r} onChanged={changed} />
        ))}
      </div>
    </div>
  );
}

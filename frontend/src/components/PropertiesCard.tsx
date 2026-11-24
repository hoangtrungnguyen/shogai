import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import type { Person, PersonProperties } from "../types";
import { Icon } from "./Icon";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

function host(url: string): string {
  try {
    const u = new URL(url);
    return (u.hostname.replace(/^www\./, "") + u.pathname.replace(/\/$/, "")).slice(0, 48);
  } catch {
    return url;
  }
}

interface Draft {
  priority: string;
  follow_up: boolean;
  phone: string;
  email: string;
  linkedin: string;
  facebook: string;
  instagram: string;
  github: string;
  blog: string;
  related_webs: string;
}

const toDraft = (p: PersonProperties): Draft => ({
  priority: p.priority == null ? "" : String(p.priority),
  follow_up: p.follow_up,
  phone: p.phone ?? "",
  email: p.email ?? "",
  linkedin: p.linkedin ?? "",
  facebook: p.facebook ?? "",
  instagram: p.instagram ?? "",
  github: p.github ?? "",
  blog: p.blog ?? "",
  related_webs: p.related_webs.join("\n"),
});

const TEXT_FIELDS: { key: keyof Draft; label: string; placeholder: string; type?: string }[] = [
  { key: "phone", label: "Phone", placeholder: "0912 345 678", type: "tel" },
  { key: "email", label: "Email", placeholder: "name@example.com", type: "email" },
  { key: "linkedin", label: "LinkedIn", placeholder: "https://www.linkedin.com/in/…" },
  { key: "facebook", label: "Facebook", placeholder: "https://www.facebook.com/…" },
  { key: "instagram", label: "Instagram", placeholder: "handle (without @)" },
  { key: "github", label: "GitHub", placeholder: "https://github.com/…" },
  { key: "blog", label: "Blog", placeholder: "https://…" },
];

export function PropertiesCard({ person, onSaved }: { person: Person; onSaved: (p: Person) => void }) {
  const p = person.properties;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => toDraft(p));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!editing) setDraft(toDraft(person.properties));
  }, [person.properties, editing]);

  async function save(update: Partial<PersonProperties>) {
    setBusy(true);
    setError(undefined);
    try {
      onSaved(await api.updateProperties(person.slug, update));
      setEditing(false);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const priority = draft.priority.trim();
    if (priority && Number.isNaN(Number(priority))) {
      setError("Priority must be a number.");
      return;
    }
    const text = (v: string) => v.trim() || null;
    void save({
      priority: priority ? Number(priority) : null,
      follow_up: draft.follow_up,
      phone: text(draft.phone),
      email: text(draft.email),
      linkedin: text(draft.linkedin),
      facebook: text(draft.facebook),
      instagram: text(draft.instagram),
      github: text(draft.github),
      blog: text(draft.blog),
      related_webs: draft.related_webs
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    });
  }

  const links: { icon: string; label: string; href: string }[] = [];
  if (p.phone) links.push({ icon: "phone", label: p.phone, href: `tel:${p.phone.replace(/[^\d+]/g, "")}` });
  if (p.email) links.push({ icon: "mail", label: p.email, href: `mailto:${p.email}` });
  if (p.linkedin) links.push({ icon: "linkedin", label: host(p.linkedin), href: p.linkedin });
  if (p.facebook) links.push({ icon: "facebook", label: host(p.facebook), href: p.facebook });
  if (p.instagram)
    links.push({ icon: "instagram", label: `@${p.instagram}`, href: `https://www.instagram.com/${encodeURIComponent(p.instagram)}/` });
  if (p.github) links.push({ icon: "github", label: host(p.github), href: p.github });
  if (p.blog) links.push({ icon: "text", label: host(p.blog), href: p.blog });
  p.related_webs.forEach((url) => links.push({ icon: "globe", label: host(url), href: url }));

  if (editing) {
    return (
      <form className="card props-card editing" onSubmit={submit}>
        <div className="props-grid">
          <label className="field">
            <span>Priority</span>
            <input
              inputMode="decimal"
              value={draft.priority}
              onChange={(e) => setDraft({ ...draft, priority: e.target.value })}
              placeholder="e.g. 1"
            />
          </label>
          <label className="field check">
            <input
              type="checkbox"
              checked={draft.follow_up}
              onChange={(e) => setDraft({ ...draft, follow_up: e.target.checked })}
            />
            <span>Follow up</span>
          </label>
          {TEXT_FIELDS.map((f) => (
            <label key={f.key} className="field">
              <span>{f.label}</span>
              <input
                type={f.type ?? "text"}
                value={draft[f.key] as string}
                onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
                placeholder={f.placeholder}
              />
            </label>
          ))}
          <label className="field wide">
            <span>Related webs (one URL per line)</span>
            <textarea
              rows={3}
              value={draft.related_webs}
              onChange={(e) => setDraft({ ...draft, related_webs: e.target.value })}
              placeholder="https://…"
            />
          </label>
        </div>
        {error && <p className="error-text">{error}</p>}
        <div className="actions">
          <button type="button" className="btn ghost" onClick={() => setEditing(false)} disabled={busy}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy}>
            {busy ? "Saving…" : "Save details"}
          </button>
        </div>
      </form>
    );
  }

  return (
    <section className="card props-card">
      <div className="props-row">
        <span className={`prio ${p.priority == null ? "unset" : ""}`} title="priority">
          {p.priority == null ? "No priority" : `Priority ${p.priority}`}
        </span>
        <button
          type="button"
          className={`follow ${p.follow_up ? "on" : ""}`}
          disabled={busy}
          onClick={() => void save({ follow_up: !p.follow_up })}
          title={p.follow_up ? "Marked for follow-up — click to clear" : "Mark for follow-up"}
        >
          <Icon name="flag" size={14} /> {p.follow_up ? "Follow up" : "No follow-up"}
        </button>
        <div className="contact-links">
          {links.map((l, i) => (
            <a
              key={i}
              className="contact"
              href={l.href}
              {...(l.href.startsWith("http") ? { target: "_blank", rel: "noreferrer noopener" } : {})}
            >
              <Icon name={l.icon} size={14} /> <span className="truncate">{l.label}</span>
            </a>
          ))}
          {!links.length && <span className="muted small">No contact details yet</span>}
        </div>
        <button type="button" className="icon-btn" title="Edit details" onClick={() => setEditing(true)}>
          <Icon name="edit" size={15} />
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
    </section>
  );
}

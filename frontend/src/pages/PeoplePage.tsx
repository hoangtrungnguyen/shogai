import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Avatar } from "../components/Avatar";
import { Icon } from "../components/Icon";
import { usePeople } from "../state";
import { DEFAULT_PRIORITY, timeAgo } from "../util";

function NewPersonForm({ onClose }: { onClose: () => void }) {
  const { refresh } = usePeople();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [relationship, setRelationship] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const person = await api.createPerson({ name: name.trim(), relationship: relationship.trim() });
      await refresh();
      navigate(`/consume?person=${encodeURIComponent(person.slug)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <form className="card new-person" onSubmit={submit}>
      <div className="field-row">
        <label className="field">
          <span>Name</span>
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Linh Trần" />
        </label>
        <label className="field">
          <span>Relationship to you</span>
          <input
            value={relationship}
            onChange={(e) => setRelationship(e.target.value)}
            placeholder="e.g. college friend, manager, sister"
          />
        </label>
      </div>
      {error && <p className="error-text">{error}</p>}
      <div className="actions">
        <button type="button" className="btn ghost" onClick={onClose}>
          Cancel
        </button>
        <button className="btn primary" disabled={!name.trim() || busy}>
          {busy ? "Creating…" : "Create & add info"}
        </button>
      </div>
    </form>
  );
}

export function PeoplePage() {
  const { people, loading, error } = usePeople();
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [followOnly, setFollowOnly] = useState(false);
  const [sort, setSort] = useState<"name" | "priority" | "updated">("name");
  const needle = query.trim().toLowerCase();
  const followCount = people.filter((p) => p.properties.follow_up).length;
  const shown = people
    .filter((p) => !followOnly || p.properties.follow_up)
    .filter(
      (p) =>
        !needle ||
        [p.name, p.relationship, p.summary, ...p.aliases, ...p.tags].join(" ").toLowerCase().includes(needle),
    )
    .sort((a, b) => {
      if (sort === "updated") return b.updated.localeCompare(a.updated);
      if (sort === "priority" && a.properties.priority !== b.properties.priority)
        return a.properties.priority - b.properties.priority;
      return a.name.localeCompare(b.name);
    });

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>People</h1>
          <p className="sub">
            {people.length ? `${people.length} ${people.length === 1 ? "person" : "people"} in your circle` : "Your circle"}
          </p>
        </div>
        <div className="head-actions">
          {people.length > 0 && (
            <label className="search">
              <Icon name="search" size={16} />
              <input placeholder="Search name, tag, summary…" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
          )}
          <button className="btn primary" onClick={() => setAdding(true)}>
            <Icon name="plus" size={16} /> Add person
          </button>
        </div>
      </header>

      {people.length > 0 && (
        <div className="people-tools">
          <button
            type="button"
            className={`follow ${followOnly ? "on" : ""}`}
            onClick={() => setFollowOnly((v) => !v)}
            title="Show only people marked follow_up"
          >
            <Icon name="flag" size={14} /> Follow up <span>{followCount}</span>
          </button>
          <label className="sort">
            Sort
            <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
              <option value="name">Name</option>
              <option value="priority">Priority (1 = highest)</option>
              <option value="updated">Recently updated</option>
            </select>
          </label>
        </div>
      )}

      {adding && <NewPersonForm onClose={() => setAdding(false)} />}
      {error && <div className="banner error">Could not reach the backend: {error}</div>}

      {!loading && !people.length && !adding && (
        <div className="empty-state">
          <div className="enso" aria-hidden="true" />
          <h2>Begin your circle</h2>
          <p>
            Add someone who matters to you, then feed Shōgai photos, personal notes, videos or papers. Gemini turns them
            into a living, sourced profile your advisor can draw on.
          </p>
          <button className="btn primary" onClick={() => setAdding(true)}>
            <Icon name="plus" size={16} /> Add your first person
          </button>
        </div>
      )}

      <div className="people-grid">
        {shown.map((p) => (
          <Link key={p.slug} to={`/people/${encodeURIComponent(p.slug)}`} className="person-card card">
            <div className="person-card-top">
              <Avatar person={p} size={52} />
              <div className="min0 grow">
                <h3 className="truncate">{p.name}</h3>
                <p className="muted truncate">{p.relationship || "relationship not set"}</p>
              </div>
              <div className="card-badges">
                <span
                  className={`prio${p.properties.priority >= DEFAULT_PRIORITY ? " low" : ""}`}
                  title="Priority (1 = highest)"
                >
                  P{p.properties.priority}
                </span>
                {p.properties.follow_up && (
                  <span className="follow on" title="Follow up">
                    <Icon name="flag" size={13} />
                  </span>
                )}
              </div>
            </div>
            <p className="person-summary">{p.summary || "No profile yet — add some information."}</p>
            {p.tags.length > 0 && (
              <div className="tags">
                {p.tags.slice(0, 5).map((t) => (
                  <span key={t} className="tag">
                    {t}
                  </span>
                ))}
              </div>
            )}
            <div className="person-card-foot">
              <span>
                {p.source_count} source{p.source_count === 1 ? "" : "s"}
              </span>
              <span>updated {timeAgo(p.updated)}</span>
            </div>
          </Link>
        ))}
      </div>
      {(needle || followOnly) && !shown.length && <p className="muted center">No one matches these filters.</p>}
    </div>
  );
}

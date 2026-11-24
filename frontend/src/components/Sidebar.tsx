import { useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useJobs, usePeople, type TrackedJob } from "../state";
import type { Health } from "../types";
import { Avatar } from "./Avatar";
import { Icon } from "./Icon";

const JOB_LABEL: Record<TrackedJob["kind"], string> = { ingest: "Consume", rebuild: "Rebuild", library: "Library scan" };

/** Commit and build time of a Docker build (absent when run from source). */
function buildStamp(health: Health): string {
  const parts = [health.commit && `commit ${health.commit}`, health.built_at && `built ${health.built_at}`];
  return parts.filter(Boolean).join(" · ") || "Running from source";
}

function JobRow({ job, onDismiss }: { job: TrackedJob; onDismiss: () => void }) {
  const navigate = useNavigate();
  const target = job.kind === "library" ? "/resources" : `/consume?job=${job.id}`;
  const total = job.items.length || 1;
  const finished = job.items.filter((i) => i.status === "done" || i.status === "error").length;
  const pct = job.status === "running" ? Math.max(6, Math.round((finished / total) * 85)) : 100;
  return (
    <div className={`job-row ${job.status}`}>
      <button type="button" className="job-main" onClick={() => navigate(target)}>
        <span className="job-title">
          {job.status === "running" && <span className="spinner" />}
          {job.status === "done" && <Icon name="check" size={14} />}
          {job.status === "error" && <Icon name="alert" size={14} />}
          <span className="truncate">
            {JOB_LABEL[job.kind]}
            {job.kind !== "library" && ` · ${job.personName}`}
          </span>
        </span>
        <span className="job-stage truncate">
          {job.status === "error" ? job.error : job.stage ?? "Starting…"}
        </span>
        <span className="job-bar">
          <span style={{ width: `${pct}%` }} />
        </span>
      </button>
      {job.status !== "running" && (
        <button type="button" className="icon-btn tiny" title="Dismiss" onClick={onDismiss}>
          <Icon name="x" size={13} />
        </button>
      )}
    </div>
  );
}

export function Sidebar() {
  const { people, health } = usePeople();
  const { jobs, dismiss } = useJobs();
  const [filter, setFilter] = useState("");
  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? people.filter((p) => [p.name, p.relationship, ...p.aliases, ...p.tags].join(" ").toLowerCase().includes(needle))
    : people;

  return (
    <aside className="sidebar">
      <Link to="/" className="brand">
        <span className="brand-mark" aria-hidden="true">
          生涯
        </span>
        <span className="brand-text">
          <strong>Shōgai</strong>
          <small>personal relationship advisor</small>
        </span>
      </Link>

      <nav className="nav">
        <NavLink to="/" end>
          <Icon name="users" /> People
        </NavLink>
        <NavLink to="/consume">
          <Icon name="consume" /> Consume
        </NavLink>
        <NavLink to="/advisor">
          <Icon name="chat" /> Advisor
        </NavLink>
        <NavLink to="/resources">
          <Icon name="academic" /> Resources
        </NavLink>
      </nav>

      <div className="side-people">
        <div className="side-head">
          <span>Your circle</span>
          <span className="count">{people.length}</span>
        </div>
        {people.length > 6 && (
          <input
            className="side-filter"
            placeholder="Filter people…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        )}
        <ul>
          {shown.map((p) => (
            <li key={p.slug}>
              <NavLink to={`/people/${encodeURIComponent(p.slug)}`}>
                <Avatar person={p} size={24} />
                <span className="truncate">{p.name}</span>
              </NavLink>
            </li>
          ))}
          {!people.length && <li className="side-empty">No one yet.</li>}
        </ul>
      </div>

      {jobs.length > 0 && (
        <div className="jobs-tray">
          <div className="side-head">
            <span>Activity</span>
          </div>
          {jobs.slice(0, 4).map((job) => (
            <JobRow key={job.id} job={job} onDismiss={() => dismiss(job.id)} />
          ))}
        </div>
      )}

      <div className="side-foot" title={health?.data_dir}>
        <span className={`dot ${health?.gemini_configured ? "ok" : "bad"}`} />
        <span className="side-status">
          {health ? (health.gemini_configured ? `Gemini · ${health.models.analyze}` : "Gemini key missing") : "Backend offline"}
        </span>
        {health && (
          <span className="side-version" title={buildStamp(health)}>
            v{health.version}
          </span>
        )}
      </div>
    </aside>
  );
}

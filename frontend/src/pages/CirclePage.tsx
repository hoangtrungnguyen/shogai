import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, mediaUrl } from "../api";
import { Avatar } from "../components/Avatar";
import { Icon } from "../components/Icon";
import { useForceLayout, type SimNode } from "../forceLayout";
import { usePeople } from "../state";
import type { GraphData, GraphEdge } from "../types";
import { initials, inkColor } from "../util";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const fold = (s: string) =>
  s.normalize("NFD").replace(/\p{Diacritic}/gu, "").replace(/đ/gi, "d").toLowerCase();

interface View {
  x: number;
  y: number;
  k: number;
}

type Drag =
  | { kind: "node"; id: string; x0: number; y0: number; moved: boolean }
  | { kind: "pan"; x0: number; y0: number; view: View; moved: boolean };

const MIN_K = 0.15;
const MAX_K = 4;
const clampK = (k: number) => Math.min(MAX_K, Math.max(MIN_K, k));

/** Scale and offset that fit every node (with room for its label) into the box. */
function fitView(nodes: SimNode[], w: number, h: number): View {
  if (!nodes.length) return { x: w / 2, y: h / 2, k: 1 };
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const n of nodes) {
    x0 = Math.min(x0, n.x - n.r - 40);
    x1 = Math.max(x1, n.x + n.r + 40);
    y0 = Math.min(y0, n.y - n.r);
    y1 = Math.max(y1, n.y + n.r + 22);
  }
  const bottom = 44; // the legend and zoom buttons
  const k = clampK(Math.min((w - 48) / (x1 - x0), (h - 48 - bottom) / (y1 - y0), 1.4));
  return { x: w / 2 - ((x0 + x1) / 2) * k, y: (h - bottom) / 2 - ((y0 + y1) / 2) * k, k };
}

function Direction({ edge, from }: { edge: GraphEdge; from: string }) {
  if (edge.mutual) return <span className="dir" title="Both profiles list each other">↔</span>;
  return edge.source === from ? (
    <span className="dir" title="Listed in this profile's References">→</span>
  ) : (
    <span className="dir" title="This person is listed in their References">←</span>
  );
}

export function CirclePage() {
  const { people } = usePeople();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const selected = params.get("focus");
  const [graph, setGraph] = useState<GraphData>();
  const [error, setError] = useState<string>();
  const [linking, setLinking] = useState(false);
  const [notice, setNotice] = useState<string>();
  const [showAll, setShowAll] = useState<boolean | null>(null); // null: decided by whether there are links
  const [query, setQuery] = useState("");
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    api.graph().then(setGraph, (e) => setError(errMsg(e)));
  }, []);

  const bySlug = useMemo(() => new Map(people.map((p) => [p.slug, p])), [people]);
  const edges = useMemo(
    () => (graph?.edges ?? []).filter((e) => bySlug.has(e.source) && bySlug.has(e.target)),
    [graph, bySlug],
  );
  const neighbours = useMemo(() => {
    const map = new Map<string, Map<string, GraphEdge>>();
    for (const e of edges) {
      if (!map.has(e.source)) map.set(e.source, new Map());
      if (!map.has(e.target)) map.set(e.target, new Map());
      map.get(e.source)!.set(e.target, e);
      map.get(e.target)!.set(e.source, e);
    }
    return map;
  }, [edges]);
  const degree = useCallback((slug: string) => neighbours.get(slug)?.size ?? 0, [neighbours]);

  const linkedCount = neighbours.size;
  const everyone = showAll ?? (graph !== undefined && linkedCount === 0);
  const visible = useMemo(
    () => people.filter((p) => everyone || neighbours.has(p.slug) || p.slug === selected),
    [people, everyone, neighbours, selected],
  );

  const radius = useCallback((slug: string) => {
    const d = degree(slug);
    return d ? 14 + 4 * Math.sqrt(d) : 11;
  }, [degree]);
  const { nodes, reheat } = useForceLayout(
    useMemo(() => visible.map((p) => p.slug), [visible]),
    useMemo(() => edges.map((e) => [e.source, e.target] as [string, string]), [edges]),
    radius,
  );
  const placed = visible.map((p) => nodes.get(p.slug)).filter((n): n is SimNode => !!n);

  // ---------------------------------------------------------------- view
  // Callback refs: the graph only mounts once the people have loaded.
  const [wrap, setWrap] = useState<HTMLDivElement | null>(null);
  const [svg, setSvg] = useState<SVGSVGElement | null>(null);
  const [size, setSize] = useState({ w: 800, h: 560 });
  useEffect(() => {
    if (!wrap) return;
    const ro = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [wrap]);
  // Follow the layout (keep it fitted) until the user pans, zooms or picks someone.
  const [follow, setFollow] = useState(true);
  const [manual, setManual] = useState<View>({ x: 0, y: 0, k: 1 });
  const view = follow ? fitView(placed, size.w, size.h) : manual;
  const viewRef = useRef(view);
  viewRef.current = view;

  const takeOver = (next: View) => {
    setFollow(false);
    setManual(next);
  };

  useEffect(() => {
    const el = svg;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      const v = viewRef.current;
      const k = clampK(v.k * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)));
      setFollow(false);
      setManual({ k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [svg]);

  const zoomBy = (factor: number) => {
    const k = clampK(view.k * factor);
    const cx = size.w / 2;
    const cy = size.h / 2;
    takeOver({ k, x: cx - ((cx - view.x) * k) / view.k, y: cy - ((cy - view.y) * k) / view.k });
  };

  const select = useCallback(
    (slug: string | null) => setParams(slug ? { focus: slug } : {}, { replace: true }),
    [setParams],
  );
  const centreOn = (slug: string) => {
    const n = nodes.get(slug);
    if (!n) return;
    const k = Math.max(view.k, 1);
    takeOver({ k, x: size.w / 2 - n.x * k, y: size.h / 2 - n.y * k });
  };
  const pick = (slug: string) => {
    select(slug);
    setQuery("");
    // A person without links joins the graph on the next layout pass; centre once placed.
    requestAnimationFrame(() => requestAnimationFrame(() => centreOn(slug)));
  };

  // ---------------------------------------------------------------- pointer
  const drag = useRef<Drag | null>(null);
  const toWorld = (clientX: number, clientY: number) => {
    const rect = svg!.getBoundingClientRect();
    return { x: (clientX - rect.left - view.x) / view.k, y: (clientY - rect.top - view.y) / view.k };
  };
  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    const id = (e.target as Element).closest<SVGGElement>("[data-node]")?.dataset.node;
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = id
      ? { kind: "node", id, x0: e.clientX, y0: e.clientY, moved: false }
      : { kind: "pan", x0: e.clientX, y0: e.clientY, view, moved: false };
  };
  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x0;
    const dy = e.clientY - d.y0;
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    d.moved = true;
    if (d.kind === "pan") {
      takeOver({ ...d.view, x: d.view.x + dx, y: d.view.y + dy });
    } else {
      const n = nodes.get(d.id);
      if (!n) return;
      const w = toWorld(e.clientX, e.clientY);
      n.fx = w.x;
      n.fy = w.y;
      if (follow) takeOver(view);
      reheat(0.3);
    }
  };
  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.kind === "node") {
      const n = nodes.get(d.id);
      if (n) n.fx = n.fy = null;
      if (!d.moved) select(d.id === selected ? null : d.id);
    } else if (!d.moved) {
      select(null);
    }
  };

  // ---------------------------------------------------------------- actions
  async function findLinks() {
    setLinking(true);
    setNotice(undefined);
    setError(undefined);
    try {
      const res = await api.linkPeople();
      setGraph(res.graph);
      setNotice(
        res.links
          ? `Added ${res.links} link${res.links === 1 ? "" : "s"} to ${res.profiles} profile${res.profiles === 1 ? "" : "s"}.`
          : "No new links found in the notes.",
      );
      setFollow(true);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLinking(false);
    }
  }

  const matches = useMemo(() => {
    const q = fold(query.trim());
    if (!q) return [];
    return people
      .filter((p) => fold([p.name, ...p.aliases, p.relationship].join(" ")).includes(q))
      .sort((a, b) => degree(b.slug) - degree(a.slug) || a.name.localeCompare(b.name))
      .slice(0, 8);
  }, [query, people, degree]);

  // ---------------------------------------------------------------- render
  const focus = hover ?? selected;
  const lit = focus ? new Set([focus, ...(neighbours.get(focus)?.keys() ?? [])]) : null;
  const crowded = placed.length > 60;
  const sel = selected ? bySlug.get(selected) : undefined;
  const selLinks = sel
    ? [...(neighbours.get(sel.slug)?.entries() ?? [])]
        .map(([slug, edge]) => ({ person: bySlug.get(slug)!, edge }))
        .sort((a, b) => a.person.name.localeCompare(b.person.name))
    : [];
  const unresolved = graph?.unresolved ?? [];

  if (!people.length) {
    return (
      <div className="page narrow empty-state">
        <div className="enso" aria-hidden="true" />
        <h2>No one in your circle yet</h2>
        <p>
          <Link to="/consume">Consume some notes</Link> to add people.
        </p>
      </div>
    );
  }

  return (
    <div className="page circle">
      <header className="page-head">
        <div>
          <h1>Your circle</h1>
          <p className="sub">
            {people.length} people · {edges.length} link{edges.length === 1 ? "" : "s"} — drawn from the{" "}
            <strong>References</strong> section of each profile.
          </p>
        </div>
        <div className="head-actions">
          <div className="graph-search">
            <label className="search">
              <Icon name="search" size={16} />
              <input
                placeholder="Find someone…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && matches[0]) pick(matches[0].slug);
                  if (e.key === "Escape") setQuery("");
                }}
              />
            </label>
            {matches.length > 0 && (
              <ul className="graph-matches card">
                {matches.map((p) => (
                  <li key={p.slug}>
                    <button type="button" onClick={() => pick(p.slug)}>
                      <Avatar person={p} size={22} />
                      <span className="truncate">{p.name}</span>
                      <span className="muted small">{degree(p.slug) || "no"} links</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <label className="toggle" title="People with no links yet">
            <input
              type="checkbox"
              checked={everyone}
              onChange={(e) => {
                setShowAll(e.target.checked);
                setFollow(true);
              }}
            />
            Show unlinked ({people.length - linkedCount})
          </label>
          <button type="button" className="btn" onClick={findLinks} disabled={linking}>
            {linking ? <span className="spinner" /> : <Icon name="sparkle" size={16} />}
            {linking ? "Looking…" : "Find links in notes"}
          </button>
        </div>
      </header>

      {(error || notice) && <p className={error ? "error-text" : "graph-notice"}>{error ?? notice}</p>}

      <div className="graph-wrap" ref={setWrap}>
        <svg
          ref={setSvg}
          className={`graph${drag.current?.kind === "pan" && drag.current.moved ? " panning" : ""}`}
          width={size.w}
          height={size.h}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          role="img"
          aria-label={`Graph of ${visible.length} people and ${edges.length} links`}
        >
          <defs>
            <clipPath id="node-clip" clipPathUnits="objectBoundingBox">
              <circle cx="0.5" cy="0.5" r="0.5" />
            </clipPath>
            <marker id="graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 1.5 9 5 0 8.5z" />
            </marker>
            <marker id="graph-arrow-lit" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 1.5 9 5 0 8.5z" />
            </marker>
          </defs>
          <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
            <g className="graph-edges">
              {edges.map((e) => {
                const s = nodes.get(e.source);
                const t = nodes.get(e.target);
                if (!s || !t) return null;
                const dx = t.x - s.x;
                const dy = t.y - s.y;
                const d = Math.hypot(dx, dy) || 1;
                const on = !!lit && lit.has(e.source) && lit.has(e.target) && (e.source === focus || e.target === focus);
                return (
                  <line
                    key={`${e.source}>${e.target}`}
                    className={`edge${e.mutual ? " mutual" : ""}${on ? " lit" : lit ? " dim" : ""}`}
                    x1={s.x + (dx / d) * (s.r + 1)}
                    y1={s.y + (dy / d) * (s.r + 1)}
                    x2={t.x - (dx / d) * (t.r + 3)}
                    y2={t.y - (dy / d) * (t.r + 3)}
                    markerEnd={e.mutual ? undefined : `url(#${on ? "graph-arrow-lit" : "graph-arrow"})`}
                  />
                );
              })}
            </g>
            <g className="graph-nodes">
              {visible.map((p) => {
                const n = nodes.get(p.slug);
                if (!n) return null;
                const ink = inkColor(p.name);
                const isLit = !!lit?.has(p.slug);
                const label = isLit || p.slug === selected || view.k >= 1.25 || (!crowded && view.k >= 0.45) || (degree(p.slug) > 0 && view.k >= 0.7);
                const cls = [
                  "node",
                  p.slug === selected && "selected",
                  lit && !isLit && "dim",
                  p.properties.priority <= 2 && "prio",
                  !degree(p.slug) && "lonely",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <g
                    key={p.slug}
                    data-node={p.slug}
                    className={cls}
                    transform={`translate(${n.x},${n.y})`}
                    onPointerEnter={() => !drag.current && setHover(p.slug)}
                    onPointerLeave={() => setHover((h) => (h === p.slug ? null : h))}
                    onDoubleClick={() => navigate(`/people/${encodeURIComponent(p.slug)}`)}
                  >
                    <title>{`${p.name}${p.relationship ? ` — ${p.relationship}` : ""}`}</title>
                    <circle className="halo" r={n.r + 5} />
                    {p.avatar ? (
                      <image
                        href={mediaUrl(p.slug, p.avatar)}
                        x={-n.r}
                        y={-n.r}
                        width={n.r * 2}
                        height={n.r * 2}
                        clipPath="url(#node-clip)"
                        preserveAspectRatio="xMidYMid slice"
                      />
                    ) : (
                      <>
                        <circle r={n.r} style={{ fill: `color-mix(in srgb, ${ink} 16%, var(--card))` }} />
                        <text className="initials" dy="0.36em" fontSize={n.r * 0.72} style={{ fill: ink }}>
                          {initials(p.name)}
                        </text>
                      </>
                    )}
                    <circle className="ring" r={n.r} style={p.avatar ? undefined : { stroke: ink }} />
                    {p.properties.follow_up && <circle className="follow-dot" cx={n.r * 0.72} cy={-n.r * 0.72} r={4.5} />}
                    {label && (
                      <text className="label" y={n.r + 14}>
                        {p.name}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          </g>
        </svg>

        {graph && !edges.length && (
          <div className="graph-empty card">
            <h3>No links yet</h3>
            <p>
              People are linked when a profile lists someone under <strong>## References</strong>. Shōgai can add the
              ones your notes already mention: wiki links like <code>[[Name]]</code> and full names in a profile.
            </p>
            <button type="button" className="btn primary" onClick={findLinks} disabled={linking}>
              <Icon name="sparkle" size={16} /> Find links in notes
            </button>
          </div>
        )}

        {sel && (
          <aside className="graph-card card">
            <button type="button" className="icon-btn tiny graph-card-close" title="Close" onClick={() => select(null)}>
              <Icon name="x" size={13} />
            </button>
            <div className="graph-card-head">
              <Avatar person={sel} size={44} />
              <div className="min0">
                <strong className="truncate">{sel.name}</strong>
                <span className="muted small truncate">{sel.relationship || "Relationship not set"}</span>
              </div>
            </div>
            {selLinks.length ? (
              <ul className="graph-links">
                {selLinks.map(({ person, edge }) => (
                  <li key={person.slug}>
                    <button type="button" onClick={() => pick(person.slug)}>
                      <Direction edge={edge} from={sel.slug} />
                      <Avatar person={person} size={22} />
                      <span className="truncate">{person.name}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted small">
                No links yet. List people under <strong>## References</strong> in the profile's Markdown.
              </p>
            )}
            <Link className="btn primary small" to={`/people/${encodeURIComponent(sel.slug)}`}>
              Open profile <Icon name="arrow" size={14} />
            </Link>
          </aside>
        )}

        <div className="graph-legend">
          <span>
            <i className="lg-line mutual" /> both list each other
          </span>
          <span>
            <i className="lg-line" /> one lists the other
          </span>
          <span>
            <i className="lg-dot prio" /> priority 1–2
          </span>
          <span>
            <i className="lg-dot follow" /> follow up
          </span>
          {unresolved.length > 0 && (
            <span
              className="lg-warn"
              title={unresolved.map((u) => `${bySlug.get(u.person)?.name ?? u.person}: ${u.target}`).join("\n")}
            >
              {unresolved.length} reference{unresolved.length === 1 ? "" : "s"} match no one
            </span>
          )}
        </div>

        <div className="graph-zoom">
          <button type="button" className="icon-btn" title="Zoom in" onClick={() => zoomBy(1.3)}>
            <Icon name="plus" size={16} />
          </button>
          <button type="button" className="icon-btn" title="Zoom out" onClick={() => zoomBy(1 / 1.3)}>
            <Icon name="minus" size={16} />
          </button>
          <button type="button" className="icon-btn" title="Fit everyone" onClick={() => setFollow(true)}>
            <Icon name="fit" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

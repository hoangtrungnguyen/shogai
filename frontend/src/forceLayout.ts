import { useCallback, useEffect, useRef, useState } from "react";

/** A small force-directed layout (springs on links, repulsion between people, a pull to the centre). */

export interface SimNode {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  /** Pinned position while the node is dragged. */
  fx: number | null;
  fy: number | null;
  r: number;
}

const LINK_DISTANCE = 120;
const LINK_STRENGTH = 0.35;
const CHARGE = -380;
const CHARGE_RANGE2 = 520 * 520;
const GRAVITY = 0.045;
const VELOCITY_KEEP = 0.6;
const ALPHA_DECAY = 0.0228; // ~300 ticks from 1 to ALPHA_MIN, like d3
const ALPHA_MIN = 0.002;
const TICKS_PER_FRAME = 2;

function tick(nodes: SimNode[], links: [SimNode, SimNode][], alpha: number) {
  for (const [s, t] of links) {
    let dx = t.x + t.vx - s.x - s.vx;
    let dy = t.y + t.vy - s.y - s.vy;
    const d = Math.hypot(dx, dy) || 1e-6;
    const f = ((d - LINK_DISTANCE) / d) * alpha * LINK_STRENGTH;
    dx *= f;
    dy *= f;
    t.vx -= dx * 0.5;
    t.vy -= dy * 0.5;
    s.vx += dx * 0.5;
    s.vy += dy * 0.5;
  }
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 1e-6) {
        dx = (Math.random() - 0.5) * 1e-2;
        dy = (Math.random() - 0.5) * 1e-2;
        d2 = dx * dx + dy * dy;
      }
      if (d2 < CHARGE_RANGE2) {
        const f = (CHARGE * alpha) / Math.max(d2, 400);
        a.vx += dx * f;
        a.vy += dy * f;
        b.vx -= dx * f;
        b.vy -= dy * f;
      }
      const min = a.r + b.r + 10; // keep circles (and room for a label) apart
      if (d2 < min * min) {
        const d = Math.sqrt(d2);
        const push = ((min - d) / d) * 0.25;
        a.x -= dx * push;
        a.y -= dy * push;
        b.x += dx * push;
        b.y += dy * push;
      }
    }
  }
  for (const n of nodes) {
    n.vx -= n.x * GRAVITY * alpha;
    n.vy -= n.y * GRAVITY * alpha;
    if (n.fx !== null && n.fy !== null) {
      n.x = n.fx;
      n.y = n.fy;
      n.vx = n.vy = 0;
    } else {
      n.vx *= VELOCITY_KEEP;
      n.vy *= VELOCITY_KEEP;
      n.x += n.vx;
      n.y += n.vy;
    }
  }
}

/** Sunflower spiral: an even, deterministic starting layout. */
function seat(i: number): [number, number] {
  const radius = 18 * Math.sqrt(0.5 + i);
  const angle = i * Math.PI * (3 - Math.sqrt(5));
  return [radius * Math.cos(angle), radius * Math.sin(angle)];
}

/**
 * Lay out `ids` linked by `links` (pairs of ids). Positions survive changes to the set, so showing
 * more people doesn't reshuffle the ones already placed. Re-renders on every animation frame
 * until the layout settles.
 */
export function useForceLayout(ids: string[], links: [string, string][], radius: (id: string) => number) {
  const nodes = useRef(new Map<string, SimNode>());
  const list = useRef<SimNode[]>([]);
  const pairs = useRef<[SimNode, SimNode][]>([]);
  const alpha = useRef(0);
  const raf = useRef(0);
  const [frame, setFrame] = useState(0);

  const run = useCallback(() => {
    if (raf.current) return;
    const loop = () => {
      for (let i = 0; i < TICKS_PER_FRAME; i++) {
        tick(list.current, pairs.current, alpha.current);
        alpha.current += (0 - alpha.current) * ALPHA_DECAY;
      }
      setFrame((f) => f + 1);
      raf.current = alpha.current > ALPHA_MIN ? requestAnimationFrame(loop) : 0;
    };
    raf.current = requestAnimationFrame(loop);
  }, []);

  const reheat = useCallback(
    (to: number) => {
      alpha.current = Math.max(alpha.current, to);
      run();
    },
    [run],
  );

  const key = ids.join("\n");
  const linkKey = links.map((l) => l.join(">")).join("\n");
  useEffect(() => {
    const map = nodes.current;
    const wanted = new Set(ids);
    for (const id of [...map.keys()]) if (!wanted.has(id)) map.delete(id);
    const neighbours = new Map<string, string[]>();
    for (const [a, b] of links) {
      neighbours.set(a, [...(neighbours.get(a) ?? []), b]);
      neighbours.set(b, [...(neighbours.get(b) ?? []), a]);
    }
    let added = 0;
    ids.forEach((id, i) => {
      const node = map.get(id);
      if (node) {
        node.r = radius(id);
        return;
      }
      // Start next to someone already placed when possible, else on the spiral.
      const anchor = (neighbours.get(id) ?? []).map((n) => map.get(n)).find(Boolean);
      const [x, y] = anchor ? [anchor.x + (Math.random() - 0.5) * 40, anchor.y + (Math.random() - 0.5) * 40] : seat(i);
      map.set(id, { id, x, y, vx: 0, vy: 0, fx: null, fy: null, r: radius(id) });
      added++;
    });
    list.current = ids.map((id) => map.get(id)!);
    pairs.current = links.filter(([a, b]) => map.has(a) && map.has(b)).map(([a, b]) => [map.get(a)!, map.get(b)!]);
    reheat(added > ids.length / 2 ? 1 : added ? 0.6 : 0.25);
    // `key`/`linkKey` stand for ids/links; radius only matters for new nodes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, linkKey, reheat]);

  useEffect(
    () => () => {
      cancelAnimationFrame(raf.current);
      raf.current = 0;
    },
    [],
  );

  return { nodes: nodes.current, reheat, frame, settling: alpha.current > ALPHA_MIN };
}

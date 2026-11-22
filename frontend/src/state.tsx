import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "./api";
import type { Health, JobEvent, JobResult, JobSnapshot, PersonSummary, SourceKind } from "./types";

/* ------------------------------------------------------------------ people */

interface PeopleCtx {
  people: PersonSummary[];
  loading: boolean;
  error?: string;
  health?: Health;
  refresh: () => Promise<void>;
}

const PeopleContext = createContext<PeopleCtx | null>(null);

export function usePeople(): PeopleCtx {
  const ctx = useContext(PeopleContext);
  if (!ctx) throw new Error("usePeople outside provider");
  return ctx;
}

/* -------------------------------------------------------------------- jobs */

export interface TrackedItem {
  id: string;
  kind: SourceKind;
  label: string;
  status: "queued" | "running" | "done" | "error";
  message: string;
  summary?: string;
  observations?: number;
  aboutSubject?: boolean;
}

export interface TrackedJob {
  id: string;
  kind: JobSnapshot["kind"];
  person: string;
  personName: string;
  label: string;
  created: string;
  status: "running" | "done" | "error";
  items: TrackedItem[];
  stage?: string;
  result?: JobResult;
  error?: string;
}

interface JobsCtx {
  jobs: TrackedJob[];
  track: (snapshot: JobSnapshot) => void;
  dismiss: (id: string) => void;
}

const JobsContext = createContext<JobsCtx | null>(null);

export function useJobs(): JobsCtx {
  const ctx = useContext(JobsContext);
  if (!ctx) throw new Error("useJobs outside provider");
  return ctx;
}

function applyEvent(job: TrackedJob, ev: JobEvent): TrackedJob {
  switch (ev.type) {
    case "plan":
      return { ...job, items: ev.items.map((i) => ({ ...i, status: "queued", message: "Queued" })) };
    case "stage":
      return { ...job, stage: ev.message };
    case "item":
      return {
        ...job,
        items: job.items.map((it) =>
          it.id === ev.id
            ? {
                ...it,
                status: ev.status,
                message: ev.message,
                summary: ev.summary ?? it.summary,
                observations: ev.observations ?? it.observations,
                aboutSubject: ev.about_subject ?? it.aboutSubject,
              }
            : it,
        ),
      };
    case "done":
      return { ...job, status: "done", result: ev.result, stage: "Profile updated" };
    case "error":
      return { ...job, status: "error", error: ev.message };
  }
}

const STORAGE_KEY = "shogai.jobs";

function fromSnapshot(s: JobSnapshot): TrackedJob {
  return {
    id: s.id,
    kind: s.kind,
    person: s.person,
    personName: s.person_name,
    label: s.label,
    created: s.created,
    status: "running",
    items: [],
  };
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [people, setPeople] = useState<PersonSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [health, setHealth] = useState<Health>();

  const refresh = useCallback(async () => {
    try {
      setPeople(await api.people());
      setError(undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    api.health().then(setHealth).catch(() => setHealth(undefined));
  }, [refresh]);

  const [jobs, setJobs] = useState<TrackedJob[]>([]);
  const streams = useRef(new Map<string, EventSource>());

  const open = useCallback(
    (id: string) => {
      if (streams.current.has(id)) return;
      const es = new EventSource(`/api/jobs/${id}/events`);
      streams.current.set(id, es);
      es.onmessage = (msg) => {
        const ev = JSON.parse(msg.data) as JobEvent;
        setJobs((all) => all.map((j) => (j.id === id ? applyEvent(j, ev) : j)));
        if (ev.type === "done" || ev.type === "error") {
          es.close();
          streams.current.delete(id);
          void refresh();
        }
      };
      es.onerror = () => {
        if (es.readyState === EventSource.CLOSED) {
          streams.current.delete(id);
          setJobs((all) =>
            all.map((j) =>
              j.id === id && j.status === "running"
                ? { ...j, status: "error", error: "Lost connection to the server (was it restarted?)." }
                : j,
            ),
          );
        }
      };
    },
    [refresh],
  );

  const track = useCallback(
    (snapshot: JobSnapshot) => {
      // Don't reset a job we already follow: its stream has replayed events into it.
      setJobs((all) => (all.some((j) => j.id === snapshot.id) ? all : [fromSnapshot(snapshot), ...all]));
      open(snapshot.id);
    },
    [open],
  );

  const dismiss = useCallback((id: string) => {
    streams.current.get(id)?.close();
    streams.current.delete(id);
    setJobs((all) => all.filter((j) => j.id !== id));
  }, []);

  // Resume jobs tracked before a page reload (events are replayed from the start).
  useEffect(() => {
    const ids: string[] = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
    ids.forEach((id) => {
      api
        .job(id)
        .then(track)
        .catch(() => undefined);
    });
    const current = streams.current;
    return () => {
      current.forEach((es) => es.close());
      current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(jobs.map((j) => j.id).slice(0, 10)));
  }, [jobs]);

  const peopleValue = useMemo(() => ({ people, loading, error, health, refresh }), [people, loading, error, health, refresh]);
  const jobsValue = useMemo(() => ({ jobs, track, dismiss }), [jobs, track, dismiss]);

  return (
    <PeopleContext.Provider value={peopleValue}>
      <JobsContext.Provider value={jobsValue}>{children}</JobsContext.Provider>
    </PeopleContext.Provider>
  );
}

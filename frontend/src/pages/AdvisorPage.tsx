import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { streamChat } from "../api";
import { Avatar } from "../components/Avatar";
import { Icon } from "../components/Icon";
import { Markdown } from "../components/Markdown";
import { usePeople } from "../state";
import type { ChatMessage, Conversation } from "../types";
import { timeAgo, uid } from "../util";

const STORE_KEY = "shogai.conversations";
const MAX_SAVED = 50;

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

function loadConversations(): Conversation[] {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
  } catch {
    return [];
  }
}

export function AdvisorPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { people } = usePeople();
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations);
  const [activeId, setActiveId] = useState<string>(() => uid());
  const [focus, setFocus] = useState<string[]>(() => params.getAll("focus"));
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true); // follow the stream unless the user scrolled up
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Arriving with ?focus=<slug> (e.g. from a profile's "Ask advisor") starts a fresh chat.
  const focusParam = params.getAll("focus").join(",");
  useEffect(() => {
    if (!focusParam) return;
    abortRef.current?.abort();
    setActiveId(uid());
    setMessages([]);
    setFocus(focusParam.split(","));
    setParams({}, { replace: true });
    inputRef.current?.focus();
  }, [focusParam, setParams]);

  // Persist the active conversation.
  useEffect(() => {
    if (!messages.length) return;
    setConversations((all) => {
      const firstUser = messages.find((m) => m.role === "user")?.content ?? "Conversation";
      const convo: Conversation = {
        id: activeId,
        title: firstUser.length > 70 ? firstUser.slice(0, 67) + "…" : firstUser,
        focus,
        messages,
        updated: new Date().toISOString(),
      };
      const next = [convo, ...all.filter((c) => c.id !== activeId)].slice(0, MAX_SAVED);
      localStorage.setItem(STORE_KEY, JSON.stringify(next));
      return next;
    });
  }, [messages, focus, activeId]);

  useEffect(() => {
    const el = chatRef.current;
    if (el && messages.length && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const focusPeople = useMemo(
    () => focus.map((slug) => people.find((p) => p.slug === slug) ?? { slug, name: slug, avatar: null }),
    [focus, people],
  );

  const suggestions = useMemo(() => {
    const first = focusPeople[0]?.name ?? people[0]?.name;
    const general = ["Who in my circle should I reach out to this week, and why?"];
    if (!first) return general;
    const short = first.split(/\s+/)[0];
    return [
      `What would ${short} love as a small, thoughtful gift right now?`,
      `How can I reconnect with ${short} if we've drifted apart?`,
      `Help me write a warm message to ${short}.`,
      `What should I avoid when talking with ${short}?`,
      `Summarize what I know about ${short} — and what I don't know yet.`,
      ...general,
    ];
  }, [focusPeople, people]);

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || streaming) return;
      const history: ChatMessage[] = [...messages, { role: "user", content }];
      stickRef.current = true;
      setMessages([...history, { role: "assistant", content: "", tools: [] }]);
      setInput("");
      setStreaming(true);
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const patchLast = (fn: (m: ChatMessage) => ChatMessage) =>
        setMessages((cur) => {
          if (!cur.length) return cur;
          const copy = cur.slice();
          copy[copy.length - 1] = fn(copy[copy.length - 1]);
          return copy;
        });
      try {
        const payload = history
          .filter((m) => m.content.trim())
          .map((m) => ({ role: m.role, content: m.content }));
        for await (const ev of streamChat({ messages: payload, focus }, ctrl.signal)) {
          if (ev.type === "text") patchLast((m) => ({ ...m, content: m.content + ev.delta }));
          else if (ev.type === "tool") patchLast((m) => ({ ...m, tools: [...(m.tools ?? []), ev.label] }));
          else if (ev.type === "error") patchLast((m) => ({ ...m, error: ev.message }));
        }
      } catch (e) {
        if ((e as Error).name === "AbortError") patchLast((m) => ({ ...m, content: m.content + "\n\n_(stopped)_" }));
        else patchLast((m) => ({ ...m, error: errMsg(e) }));
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [messages, focus, streaming],
  );

  function onSubmit(e?: FormEvent) {
    e?.preventDefault();
    void send(input);
  }

  function newChat() {
    abortRef.current?.abort();
    setActiveId(uid());
    setMessages([]);
    setShowHistory(false);
    inputRef.current?.focus();
  }

  function openConversation(c: Conversation) {
    abortRef.current?.abort();
    setActiveId(c.id);
    setMessages(c.messages);
    setFocus(c.focus);
    setShowHistory(false);
  }

  function deleteConversation(id: string) {
    setConversations((all) => {
      const next = all.filter((c) => c.id !== id);
      localStorage.setItem(STORE_KEY, JSON.stringify(next));
      return next;
    });
    if (id === activeId) newChat();
  }

  const onCite = useCallback(
    (id: string) => {
      if (focus.length === 1) navigate(`/people/${encodeURIComponent(focus[0])}?tab=sources&source=${id}`);
    },
    [focus, navigate],
  );

  const available = people.filter((p) => !focus.includes(p.slug));

  return (
    <div className="page advisor">
      <header className="advisor-head">
        <div>
          <h1>Advisor</h1>
          <p className="sub">Ask anything about the people in your circle. Answers are grounded in their profiles.</p>
        </div>
        <div className="head-actions">
          <div className="popover-wrap">
            <button type="button" className="btn ghost" onClick={() => setShowHistory((s) => !s)}>
              <Icon name="history" size={16} /> History
            </button>
            {showHistory && (
              <div className="popover">
                {!conversations.length && <p className="muted small pad">No saved conversations yet.</p>}
                {conversations.map((c) => (
                  <div key={c.id} className={`history-row ${c.id === activeId ? "active" : ""}`}>
                    <button type="button" className="grow min0 text-left" onClick={() => openConversation(c)}>
                      <span className="truncate block">{c.title}</span>
                      <span className="muted small">
                        {timeAgo(c.updated)}
                        {c.focus.length ? ` · ${c.focus.map((s) => people.find((p) => p.slug === s)?.name ?? s).join(", ")}` : ""}
                      </span>
                    </button>
                    <button type="button" className="icon-btn tiny" title="Delete" onClick={() => deleteConversation(c.id)}>
                      <Icon name="trash" size={13} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <button type="button" className="btn" onClick={newChat}>
            <Icon name="plus" size={16} /> New chat
          </button>
        </div>
      </header>

      <div className="focus-bar">
        <span className="focus-label">Focus</span>
        {focusPeople.map((p) => (
          <span className="chip" key={p.slug}>
            <Avatar person={p} size={20} />
            {p.name}
            <button type="button" title="Remove" onClick={() => setFocus((f) => f.filter((x) => x !== p.slug))}>
              <Icon name="x" size={12} />
            </button>
          </span>
        ))}
        {available.length > 0 && (
          <select
            className="chip-select"
            value=""
            onChange={(e) => e.target.value && setFocus((f) => [...f, e.target.value])}
          >
            <option value="">+ person</option>
            {available.map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.name}
              </option>
            ))}
          </select>
        )}
        {!focus.length && <span className="muted small">none — the advisor looks people up as needed</span>}
      </div>

      <div
        className="chat"
        ref={chatRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        {!messages.length && (
          <div className="chat-empty">
            <div className="enso small" aria-hidden="true" />
            <h2>How can I help with your relationships?</h2>
            <p className="muted">
              {people.length
                ? "I know what you've consumed about each person — and I'll tell you when I'm inferring."
                : "Your circle is empty. Consume some information about someone first, then ask me about them."}
            </p>
            <div className="suggestions">
              {suggestions.map((s) => (
                <button key={s} type="button" className="suggestion" onClick={() => void send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => {
          const isLast = i === messages.length - 1;
          return (
            <div key={i} className={`msg ${m.role}`}>
              {m.role === "assistant" && (m.tools?.length ?? 0) > 0 && (
                <div className="tool-trail">
                  {m.tools!.map((t, j) => (
                    <span key={j} className="tool">
                      <Icon name="search" size={12} /> {t}
                    </span>
                  ))}
                </div>
              )}
              {m.role === "user" ? (
                <div className="bubble">{m.content}</div>
              ) : (
                <div className="answer">
                  {m.content ? (
                    <Markdown text={m.content} onCite={onCite} />
                  ) : (
                    streaming && isLast && !m.error && <p className="thinking">Thinking…</p>
                  )}
                  {streaming && isLast && m.content && <span className="caret" />}
                </div>
              )}
              {m.error && <div className="banner error small">{m.error}</div>}
            </div>
          );
        })}
      </div>

      <form className="composer" onSubmit={onSubmit}>
        <textarea
          ref={inputRef}
          rows={1}
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = Math.min(e.target.scrollHeight, 220) + "px";
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              onSubmit();
            }
          }}
          placeholder={focusPeople[0] ? `Ask about ${focusPeople[0].name}…` : "Ask about anyone in your circle…"}
        />
        {streaming ? (
          <button type="button" className="btn icon-only stop" title="Stop" onClick={() => abortRef.current?.abort()}>
            <Icon name="stop" size={18} />
          </button>
        ) : (
          <button type="submit" className="btn primary icon-only" title="Send" disabled={!input.trim()}>
            <Icon name="send" size={18} />
          </button>
        )}
      </form>
    </div>
  );
}

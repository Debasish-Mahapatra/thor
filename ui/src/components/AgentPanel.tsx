import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { currentActivity, groupEvents, usageText } from "../agentEvents";
import {
  ApiError,
  api,
  type AgentEvent,
  type Chat,
  type ChatSummary,
  type ContextItem,
  type EditorContext,
  type Provider,
} from "../api";
import { IconClose, IconDockBottom, IconDockRight } from "../icons";
import { useLayoutValue } from "../layout";
import { basename } from "../paths";

// Inline `code` and **bold**, which models use in short answers. Nothing else is interpreted.
function inline(text: string): ReactNode[] {
  return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g).map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
    return part;
  });
}

function AgentText({ text }: { text: string }) {
  const blocks = text.split(/(```[^\n]*\n[\s\S]*?(?:```|$))/g);
  return (
    <div className="agent-text">
      {blocks.map((block, i) =>
        block.startsWith("```") ? (
          <pre key={i}>{block.replace(/^```[^\n]*\n/, "").replace(/```$/, "")}</pre>
        ) : (
          block.trim() && <p key={i}>{inline(block.trim())}</p>
        ),
      )}
    </div>
  );
}

const QUICK = [
  { label: "Proofread this", text: "Proofread the selected text, or the open file if nothing is selected. Fix grammar and spelling only." },
  { label: "Tighten this", text: "Tighten the selected text without changing its meaning or any number." },
  { label: "Check citations", text: "Check that every \\cite in this paper has a bibliography entry, and that the entries look like real, correctly described works. Confirm the doubtful ones." },
  { label: "Explain this", text: "Explain what the selected LaTeX does. Do not change anything." },
];

export interface AgentRequest {
  text: string;
  /** Changes with each request, so asking the same thing twice is two requests. */
  token: number;
}

interface Props {
  projectId: string;
  /** What the editor holds now: saved before a message goes out. */
  prepare: () => Promise<boolean>;
  context: () => EditorContext;
  openFile: string | null;
  selectedLines: { from: number; to: number } | null;
  /** Passages added for the next message. */
  items: ContextItem[];
  onRemoveItem: (id: string) => void;
  onClearItems: () => void;
  onJumpToItem: (item: ContextItem) => void;
  /** A message another part of the window wants sent, such as "fix these errors". */
  request: AgentRequest | null;
  focusToken: number;
  onChatChanged: (chat: string | null) => void;
  /** The agent finished a turn, so its waiting edits may have changed. */
  onTurnDone: () => void;
  onReview: () => void;
  onSettings: () => void;
  onError: (e: unknown) => void;
  /** Collapsed: still running, so a turn keeps reporting and a draft is not lost. */
  hidden: boolean;
  /** Edits still waiting for the user, so an old turn's card does not claim more than is true. */
  waiting: number;
  dock: "right" | "bottom";
  onDock: (dock: "right" | "bottom") => void;
  onHide: () => void;
  splitter: ReactNode;
}

export function AgentPanel(props: Props) {
  const { projectId, prepare, context, openFile, selectedLines, items: contextItems, onRemoveItem, onClearItems, onJumpToItem, request, focusToken, onChatChanged, onTurnDone, onReview, onSettings, onError, hidden, waiting, dock, onDock, onHide, splitter } = props;
  // The parent's callbacks change as its state does. Reading them through a ref keeps a change of chat there from
  // restarting what happens here, such as the stream of a turn in progress.
  const handlers = useRef({ onTurnDone, onChatChanged, onError });
  handlers.current = { onTurnDone, onChatChanged, onError };
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [providerId, setProviderId] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chat, setChat] = useState<Chat | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [localFast, setLocalFast] = useLayoutValue("localFast", false);

  const box = useRef<HTMLTextAreaElement>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const source = useRef<EventSource | null>(null);
  const handledRequest = useRef(0);

  const reloadProviders = useCallback(() => {
    api.providers().then((found) => {
      setProviders(found.providers);
      // A chat that names a provider keeps it; otherwise the first choice that works.
      setProviderId((id) => id || found.current);
    }, onError);
  }, [onError]);

  useEffect(reloadProviders, [reloadProviders]);

  const closeStream = () => {
    source.current?.close();
    source.current = null;
  };

  const follow = useCallback(
    (chatId: string, after: number) => {
      closeStream();
      const stream = new EventSource(api.chatEventsUrl(projectId, chatId, after));
      source.current = stream;
      stream.onmessage = (message) => {
        const event = JSON.parse(message.data) as AgentEvent;
        setChat((current) => {
          if (!current || current.id !== chatId || current.events.some((e) => e.seq === event.seq)) return current;
          return { ...current, events: [...current.events, event], running: event.type !== "done" };
        });
        if (event.type === "done") {
          handlers.current.onTurnDone();
          // A provider may have changed the model during the turn, and said so.
          api.chat(projectId, chatId).then((c) => setModel(c.model), () => undefined);
        }
      };
      stream.addEventListener("end", () => {
        closeStream();
        setChat((current) => (current && current.id === chatId ? { ...current, running: false } : current));
      });
    },
    [projectId],
  );

  const openChat = useCallback(
    async (chatId: string | null) => {
      closeStream();
      if (!chatId) {
        setChat(null);
        handlers.current.onChatChanged(null);
        return;
      }
      try {
        const opened = await api.chat(projectId, chatId);
        setChat(opened);
        pinned.current = true;
        handlers.current.onChatChanged(opened.id);
        if (opened.provider) {
          setProviderId(opened.provider);
          setModel(opened.model);
        }
        if (opened.running) follow(opened.id, opened.events[opened.events.length - 1]?.seq ?? 0);
      } catch (e) {
        handlers.current.onError(e);
      }
    },
    [projectId, follow],
  );

  const reloadChats = useCallback(
    () => api.chats(projectId).then((found) => setChats(found.chats), (e) => handlers.current.onError(e)),
    [projectId],
  );

  // The latest chat opens with the project, so the agent's waiting edits are never out of reach.
  useEffect(() => {
    let live = true;
    api.chats(projectId).then((found) => {
      if (!live) return;
      setChats(found.chats);
      void openChat(found.chats[0]?.id ?? null);
    }, (e) => handlers.current.onError(e));
    return () => {
      live = false;
      closeStream();
    };
  }, [projectId, openChat]);

  const provider = providers?.find((p) => p.id === providerId) ?? null;
  const currentModel = model ?? provider?.model ?? "";

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message || sending || chat?.running) return;
      setSending(true);
      try {
        if (!(await prepare())) return;
        const target = chat ?? (await api.newChat(projectId));
        if (!chat) {
          setChat(target);
          handlers.current.onChatChanged(target.id);
        }
        pinned.current = true;
        const fast = providers?.find((p) => p.id === providerId)?.kind === "local" && localFast;
        await api.sendMessage(projectId, target.id, message, context(), providerId, model, fast);
        setDraft("");
        onClearItems();
        setChat((current) => (current?.id === target.id ? { ...current, running: true } : current));
        follow(target.id, target.events[target.events.length - 1]?.seq ?? 0);
        void reloadChats();
      } catch (e) {
        handlers.current.onError(e);
      } finally {
        setSending(false);
      }
    },
    [sending, chat, prepare, projectId, context, providerId, model, follow, reloadChats, providers, localFast],
  );

  useEffect(() => {
    if (request && request.token !== handledRequest.current && providers) {
      handledRequest.current = request.token;
      void send(request.text);
    }
  }, [request, providers, send]);

  useEffect(() => {
    if (focusToken) box.current?.focus();
  }, [focusToken]);

  // A local model takes seconds to load, so it starts as soon as it is chosen.
  useEffect(() => {
    if (provider?.kind === "local") api.warmProvider(provider.id).catch(() => undefined);
  }, [provider?.id, provider?.kind]);

  const items = useMemo(() => groupEvents(chat?.events ?? []), [chat?.events]);

  // Stays at the bottom while new text arrives, unless the reader has scrolled up. The jump is instant.
  useLayoutEffect(() => {
    const node = transcript.current;
    if (node && pinned.current) node.scrollTop = node.scrollHeight;
  }, [items, dock, hidden]);

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(draft);
    }
  };

  const answer = async (id: string, allow: boolean) => {
    if (!chat) return;
    try {
      await api.decide(projectId, chat.id, id, allow);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) onError(e);
    }
  };

  const removeChat = async () => {
    if (!chat) return;
    try {
      await api.deleteChat(projectId, chat.id);
      const found = await api.chats(projectId);
      setChats(found.chats);
      await openChat(found.chats[0]?.id ?? null);
      onTurnDone();
    } catch (e) {
      onError(e);
    }
  };

  const running = !!chat?.running;
  const ready = providers?.some((p) => p.ready) ?? true;
  const lastChanges = [...items].reverse().find((item) => item.kind === "changes");

  return (
    <aside className={`agent ${dock}`} aria-label="Agent" hidden={hidden}>
      {splitter}
      <div className="agent-chat">
      <div className="agent-head">
        <select
          className="agent-chats"
          value={chat?.id ?? ""}
          aria-label="Chat"
          onChange={(e) => void openChat(e.target.value || null)}
        >
          {!chat && <option value="">New chat</option>}
          {chat && !chats.some((c) => c.id === chat.id) && <option value={chat.id}>{chat.title}</option>}
          {chats.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
        <button className="link small" onClick={() => void openChat(null)} title="Start a new chat. The agent starts from your files as they are.">
          New
        </button>
        {chat && !running && (
          <button className="link small" onClick={() => void removeChat()} title="Delete this chat and any edits still waiting in it">
            Delete
          </button>
        )}
        <button
          className="icon"
          onClick={() => onDock(dock === "right" ? "bottom" : "right")}
          title={dock === "right" ? "Move the agent to a bar along the bottom" : "Move the agent to the right side"}
          aria-label={dock === "right" ? "Move the agent to a bar along the bottom" : "Move the agent to the right side"}
        >
          {dock === "right" ? <IconDockBottom /> : <IconDockRight />}
        </button>
        <button className="icon" onClick={onHide} title="Hide the agent (⌘⇧\)" aria-label="Hide the agent">
          <IconClose />
        </button>
      </div>

      <div
        className="agent-transcript"
        ref={transcript}
        onScroll={(e) => {
          const node = e.currentTarget;
          pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 40;
        }}
      >
        {items.length === 0 && (
          <div className="agent-empty">
            <p className="muted">
              Ask for an edit, a fix or an explanation. The agent works in a private copy of your paper, and you accept or reject each change.
            </p>
            {ready ? (
              <div className="agent-quick">
                {QUICK.map((q) => (
                  <button key={q.label} onClick={() => void send(q.text)}>
                    {q.label}
                  </button>
                ))}
              </div>
            ) : (
              <p>
                No model is set up yet. <button className="link" onClick={onSettings}>Choose one in Settings</button>.
              </p>
            )}
          </div>
        )}
        {items.map((item) => {
          switch (item.kind) {
            case "user":
              return (
                <div className="agent-user" key={item.event.seq}>
                  <div>{item.event.text}</div>
                  <div className="agent-meta">
                    {item.event.provider_name}
                    {item.event.model ? ` · ${item.event.model}` : ""}
                    {(() => {
                      const added = item.event.context?.selections?.length ?? 0;
                      if (added) return ` · ${added} passage${added === 1 ? "" : "s"} attached`;
                      const selected = item.event.context?.selection;
                      return selected ? ` · lines ${selected.from}-${selected.to} selected` : "";
                    })()}
                  </div>
                </div>
              );
            case "text":
              return <AgentText key={item.key} text={item.text} />;
            case "activity":
              return (
                <div className={`agent-activity ${item.state}`} key={item.key}>
                  {item.summary}
                  {item.state === "failed" && <span> (failed{item.detail ? `: ${item.detail}` : ""})</span>}
                </div>
              );
            case "compile":
              return (
                <div className={`agent-compile ${item.event.ok ? "ok" : "failed"}`} key={item.event.seq}>
                  Its copy: {item.event.summary}
                </div>
              );
            case "changes": {
              const hunks = item.event.hunks ?? 0;
              const files = item.event.files ?? 0;
              const proposed = `${hunks} edit${hunks === 1 ? "" : "s"} in ${files} file${files === 1 ? "" : "s"}`;
              // Only the latest list can still be waiting; the rest is history.
              if (item !== lastChanges || waiting === 0) {
                return (
                  <div className="agent-notice" key={item.event.seq}>
                    Proposed {proposed}.
                  </div>
                );
              }
              return (
                <div className="agent-changes" key={item.event.seq}>
                  <span>{waiting} edit{waiting === 1 ? "" : "s"} waiting for you</span>
                  <button className="primary" onClick={onReview}>
                    Review
                  </button>
                </div>
              );
            }
            case "approval":
              return (
                <div className="agent-approval" key={item.event.seq}>
                  <div>{item.event.summary}</div>
                  {item.event.detail && <code>{item.event.detail}</code>}
                  {item.answered === null ? (
                    running && (
                      <div className="row">
                        <button className="primary" onClick={() => void answer(item.event.id!, true)}>
                          Allow
                        </button>
                        <button onClick={() => void answer(item.event.id!, false)}>Do not allow</button>
                      </div>
                    )
                  ) : (
                    <div className="muted small">{item.answered ? "Allowed." : "Not allowed."}</div>
                  )}
                </div>
              );
            case "notice":
              return (
                <div className="agent-notice" key={item.event.seq}>
                  {item.event.text}
                </div>
              );
            case "error":
              return (
                <div className="agent-error" role="alert" key={item.event.seq}>
                  {item.event.text}
                </div>
              );
            case "usage":
              return (
                <div className="agent-usage" key={item.key}>
                  {usageText(item.input, item.output, item.cost)}
                </div>
              );
          }
        })}
      </div>

      {running && chat && (
        <div className="agent-status">
          <span>{currentActivity(items)}...</span>
          <button className="link small" onClick={() => void api.stopChat(projectId, chat.id).catch(onError)}>
            Stop
          </button>
        </div>
      )}
      </div>

      <div className="agent-compose">
        <div className="agent-context muted small">
          {openFile ? basename(openFile) : "No file open"}
          {selectedLines ? `, lines ${selectedLines.from}-${selectedLines.to} selected (⌘L adds it)` : ""}
        </div>
        {contextItems.length > 0 && (
          <div className="context-chips" aria-label="Passages attached to this message">
            {contextItems.map((item) => (
              <span className="context-chip" key={item.id} title={item.text}>
                <button className="chip-label" onClick={() => onJumpToItem(item)}>
                  {basename(item.file)}:{item.from === item.to ? item.from : `${item.from}-${item.to}`}
                </button>
                <button className="chip-remove" aria-label={`Remove ${basename(item.file)}, line ${item.from}`} onClick={() => onRemoveItem(item.id)}>
                  ×
                </button>
              </span>
            ))}
            {contextItems.length > 1 && (
              <button className="link small" onClick={onClearItems}>
                Clear
              </button>
            )}
          </div>
        )}
        <textarea
          ref={box}
          value={draft}
          rows={3}
          placeholder="Ask the agent. Return sends; Shift-Return starts a new line."
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
        />
        <div className="agent-send">
          <select
            value={providerId}
            aria-label="Model provider"
            onChange={(e) => {
              setProviderId(e.target.value);
              setModel(null);
            }}
          >
            {providers?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.ready ? "" : " (not set up)"}
              </option>
            ))}
          </select>
          {provider && provider.models.length > 0 && (
            <select value={currentModel} aria-label="Model" onChange={(e) => setModel(e.target.value)}>
              {!provider.models.some((m) => m.id === currentModel) && <option value={currentModel}>{currentModel || "Its default"}</option>}
              {provider.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          )}
          {provider?.kind === "local" && (
            <label className="check small" title="Answers about ten times faster, without the model's hidden thinking, but makes more mistakes. In a test it missed a wrong apostrophe in 2 of 3 runs.">
              <input type="checkbox" checked={localFast} onChange={(e) => setLocalFast(e.target.checked)} />
              Fast
            </label>
          )}
          <span className="spacer" />
          <button className="primary" disabled={!draft.trim() || running || sending || !provider} onClick={() => void send(draft)}>
            Send
          </button>
        </div>
        {provider && !provider.ready && (
          <div className="agent-notready small">
            {provider.fix ?? provider.detail}{" "}
            <button className="link small" onClick={onSettings}>
              Settings
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ReplyEntry, type ReplyState, type Tone } from "../api";

const POLL_MS = 2000;
const TONES: { id: Tone; label: string; title: string }[] = [
  { id: "formal", label: "Formal", title: "For an editor or reviewers" },
  { id: "plain", label: "Plain", title: "For co-authors" },
];
const FORMATS: { id: "md" | "docx" | "pdf"; label: string }[] = [
  { id: "pdf", label: "PDF" },
  { id: "docx", label: "Word" },
  { id: "md", label: "Markdown" },
];

interface Props {
  projectId: string;
  choice: () => { provider: string; model: string | null };
  onChat: (chat: string) => void;
  onError: (e: unknown) => void;
}

/** The letter answering the comments, in two tones, which the author edits before making any file of it. */
export function ReplyView({ projectId, choice, onChat, onError }: Props) {
  const [state, setState] = useState<ReplyState | null>(null);
  const [tone, setTone] = useState<Tone>("formal");
  const [opening, setOpening] = useState("");
  const [closing, setClosing] = useState("");
  const [entries, setEntries] = useState<Record<string, string>>({});
  const [made, setMade] = useState<{ path: string; name: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const dirty = useRef(false);

  const reload = useCallback(() => api.reply(projectId).then(setState, onError), [projectId, onError]);
  useEffect(() => void reload(), [reload]);
  const running = !!state?.running;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void reload(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, reload]);

  const reply = state?.reply ?? null;
  useEffect(() => {
    if (!reply) return;
    setOpening(reply.opening[tone]);
    setClosing(reply.closing[tone]);
    setEntries(Object.fromEntries(reply.entries.map((e: ReplyEntry) => [e.id, e[tone]])));
    dirty.current = false;
  }, [reply, tone]);

  const save = async () => {
    if (!dirty.current) return;
    dirty.current = false;
    const next = await api.editReply(projectId, tone, opening, closing, entries);
    setState((s) => (s ? { ...s, reply: next.reply, text: next.text } : s));
  };

  const act = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    try {
      await action();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  const draft = () =>
    act(async () => {
      const { provider, model } = choice();
      const { chat } = await api.draftReply(projectId, provider, model);
      onChat(chat);
      await reload();
    });

  const edited = (setter: (value: string) => void) => (value: string) => {
    dirty.current = true;
    setter(value);
    setCopied(false);
    setMade(null);
  };

  if (!state) return <div className="check-view"><div className="empty">Reading the reply...</div></div>;
  if (!state.active) {
    return (
      <div className="check-view">
        <div className="empty">Start a revision from the menu in the toolbar. The reply belongs to the active revision.</div>
      </div>
    );
  }
  return (
    <div className="check-view">
      <div className="changes-head">
        <strong>Reply</strong>
        {reply && (
          <div className="plan-choices" role="group" aria-label="Tone">
            {TONES.map((t) => (
              <button
                key={t.id}
                className={tone === t.id ? "chosen" : ""}
                aria-pressed={tone === t.id}
                title={t.title}
                onClick={() => void act(async () => {
                  await save();
                  setTone(t.id);
                  setMade(null);
                  setCopied(false);
                })}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}
        <span className="spacer" />
        {made && (
          <>
            <span className="muted small">Saved {made.name}.</span>
            <button className="link small" onClick={() => void act(async () => void (await api.reveal(projectId, made.path)))}>
              Show in Finder
            </button>
          </>
        )}
        {reply && (
          <>
            <button
              disabled={busy}
              onClick={() => void act(async () => {
                await save();
                const text = (await api.reply(projectId)).text?.[tone] ?? "";
                await navigator.clipboard.writeText(text);
                setCopied(true);
              })}
            >
              {copied ? "Copied" : "Copy text"}
            </button>
            {FORMATS.map((f) => (
              <button
                key={f.id}
                disabled={busy}
                onClick={() => void act(async () => {
                  await save();
                  setMade(await api.exportReply(projectId, tone, f.id));
                })}
              >
                {f.label}
              </button>
            ))}
            <button disabled={busy || running} title="Replaces this draft, including your edits to it" onClick={() => void draft()}>
              Draft again
            </button>
          </>
        )}
      </div>
      <div className="check-body">
        {running && <div className="notice-line">The agent is drafting the reply. You can follow it in the agent panel.</div>}
        {!reply && !running && (
          <div className="plan-start">
            <p>
              {state.comments > 0
                ? `The agent answers the ${state.comments} comments from the plan and from what really changed since the baseline, in a formal and a plain version. You edit both here.`
                : "No comments have been imported into this revision, so there is nothing to reply to yet."}
            </p>
            <div>
              <button className="primary" disabled={busy || state.comments === 0} onClick={() => void draft()}>
                Draft the reply
              </button>
            </div>
          </div>
        )}
        {reply && (
          <div className="reply-letter" onBlur={() => void save().catch(onError)}>
            {state.unanswered.length > 0 && <div className="flag small">No reply yet for {state.unanswered.join(", ")}.</div>}
            <textarea rows={3} aria-label="Opening" value={opening} onChange={(e) => edited(setOpening)(e.target.value)} />
            {reply.entries.map((entry) => (
              <section key={entry.id} className="reply-entry">
                {entry.quotes.map((q) => (
                  <blockquote key={q.ref} className="plan-comment small">
                    <span className="muted">{q.person || "Comment"}, {q.where}: </span>
                    {q.words}
                  </blockquote>
                ))}
                <textarea
                  rows={Math.max(3, Math.ceil((entries[entry.id] ?? "").length / 90))}
                  aria-label={`Reply ${entry.id}`}
                  value={entries[entry.id] ?? ""}
                  onChange={(e) => edited((value) => setEntries((all) => ({ ...all, [entry.id]: value })))(e.target.value)}
                />
              </section>
            ))}
            <textarea rows={2} aria-label="Closing" value={closing} onChange={(e) => edited(setClosing)(e.target.value)} />
          </div>
        )}
      </div>
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { api, type Decision, type PlanComment, type PlanItem, type PlanState } from "../api";

const POLL_MS = 2000;

const CHOICES: { value: Decision; label: string; title: string }[] = [
  { value: "approve", label: "Approve", title: "Do this as proposed." },
  { value: "change", label: "Change", title: "Do something else. Say what in the note, then draft the plan again." },
  { value: "explain", label: "Explain", title: "Leave the paper as it is. The reply says why, in your words." },
];

const NOTE_ASKS: Partial<Record<Decision, string>> = {
  approve: "Anything to add (optional)",
  change: "What should happen instead?",
  explain: "Why does the paper stay as it is? The reply uses this.",
};

function commentWords(c: PlanComment): string {
  const who = c.person || c.author || "Unknown";
  const where = c.page ? `page ${c.page}${c.line ? `, line ${c.line}` : ""}` : "email";
  const words = c.note || (c.marked ? `Marked: "${c.marked}"` : c.insert ? `Insert: "${c.insert}"` : c.kind);
  return `${who}, ${where}: ${words}`;
}

function ItemCard(props: {
  item: PlanItem;
  comments: PlanState["comments"];
  locked: boolean;
  onDecide: (item: PlanItem, decision: Decision, note: string) => void;
}) {
  const { item, comments, locked, onDecide } = props;
  const [note, setNote] = useState(item.note);
  useEffect(() => setNote(item.note), [item.note]);
  const needsNote = item.decision === "change" || item.decision === "explain";
  return (
    <section className={`plan-item ${item.decision || "open"}`}>
      <div className="plan-item-head">
        <strong>{item.id}</strong>
        <span className="plan-request">{item.request}</span>
      </div>
      <div className="muted small">{item.location}</div>
      {item.comments.map((ref) => (
        <blockquote key={ref} className="plan-comment small">
          {comments[ref] ? commentWords(comments[ref]) : ref}
        </blockquote>
      ))}
      <p className="plan-proposal">{item.proposal}</p>
      {item.touches_science && <div className="flag small">Touches a number, a result or a claim. Read this one closely.</div>}
      {item.earlier.length > 0 && (
        <div className="muted small">
          In version {item.earlier[item.earlier.length - 1].version} you said:{" "}
          {item.earlier[item.earlier.length - 1].note || item.earlier[item.earlier.length - 1].decision || "nothing yet"}
        </div>
      )}
      <div className="plan-choices" role="group" aria-label={`Decision for ${item.id}`}>
        {CHOICES.map((choice) => (
          <button
            key={choice.value}
            className={item.decision === choice.value ? "chosen" : ""}
            aria-pressed={item.decision === choice.value}
            title={choice.title}
            disabled={locked}
            onClick={() => onDecide(item, item.decision === choice.value ? "" : choice.value, note)}
          >
            {choice.label}
          </button>
        ))}
      </div>
      {item.decision && !locked && (
        <textarea
          className="plan-note"
          rows={needsNote ? 2 : 1}
          placeholder={NOTE_ASKS[item.decision]}
          aria-label={NOTE_ASKS[item.decision]}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => note !== item.note && onDecide(item, item.decision, note)}
        />
      )}
      {locked && item.note && <div className="small">Your note: {item.note}</div>}
    </section>
  );
}

interface Props {
  projectId: string;
  choice: () => { provider: string; model: string | null };
  /** A turn started, so the agent panel should show it. */
  onChat: (chat: string) => void;
  onReview: () => void;
  onError: (e: unknown) => void;
}

/** The plan comes before any edit: the agent proposes, the author decides each item, and only then does it edit. */
export function PlanView({ projectId, choice, onChat, onReview, onError }: Props) {
  const [state, setState] = useState<PlanState | null>(null);
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => api.plan(projectId).then(setState, onError), [projectId, onError]);
  useEffect(() => void reload(), [reload]);
  const running = !!state?.running;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void reload(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, reload]);

  const act = async (action: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    try {
      await action();
      await reload();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  const draft = () =>
    act(async () => {
      const { provider, model } = choice();
      const { chat } = await api.draftPlan(projectId, brief, provider, model);
      onChat(chat);
    });
  const approve = () =>
    act(async () => {
      const { provider, model } = choice();
      const { chat } = await api.approvePlan(projectId, provider, model);
      onChat(chat);
    });
  const decide = (item: PlanItem, decision: Decision, note: string) =>
    act(() => api.decidePlan(projectId, item.id, decision, note));

  if (!state) return <div className="check-view"><div className="empty">Reading the plan...</div></div>;
  if (!state.active) {
    return (
      <div className="check-view">
        <div className="empty">Start a revision from the menu in the toolbar. The plan belongs to the active revision.</div>
      </div>
    );
  }
  const { plan } = state;
  const commentCount = Object.keys(state.comments).length;
  const locked = plan?.status === "approved";
  return (
    <div className="check-view">
      <div className="changes-head">
        <strong>Revision plan</strong>
        {plan && (
          <span className="muted small">
            Version {plan.version}, {locked ? `approved ${plan.approved}` : "draft"}. {plan.items.length} items.
          </span>
        )}
        <span className="spacer" />
        {plan && (
          <a className="link small" href={api.planPdfUrl(projectId)} target="_blank" rel="noreferrer">
            PDF
          </a>
        )}
        {locked && (
          <>
            <button onClick={onReview}>Review the edits</button>
            <button disabled={busy || running} onClick={() => void act(() => api.reopenPlan(projectId))}>
              Reopen
            </button>
          </>
        )}
        {plan && !locked && (
          <>
            <button disabled={busy || running} onClick={() => void draft()} title="Asks the agent for a new draft with your decisions recorded">
              Draft again with my decisions
            </button>
            <button
              className="primary"
              disabled={busy || running || !!state.blocked}
              title={state.blocked ?? "Approves the plan, and the agent starts on the approved items in its own copy"}
              onClick={() => void approve()}
            >
              Approve and start editing
            </button>
          </>
        )}
      </div>
      <div className="check-body">
        {running && <div className="notice-line">The agent is working on this. You can follow it in the agent panel.</div>}
        {!plan && !running && (
          <div className="plan-start">
            <p>
              {commentCount > 0
                ? `The agent reads the ${commentCount} comments in this revision and the paper, and proposes what to do about each. It can only read until you approve.`
                : "No comments have been imported into this revision. You can still plan: say what it should do."}
            </p>
            <textarea
              rows={3}
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              placeholder="What should this revision do? (optional when there are comments)"
              aria-label="What should this revision do?"
            />
            <div>
              <button className="primary" disabled={busy} onClick={() => void draft()}>
                Draft the plan
              </button>
            </div>
          </div>
        )}
        {plan && (
          <>
            <p>{plan.summary}</p>
            {state.blocked && !locked && <div className="muted small plan-blocked">To approve: {state.blocked}</div>}
            {plan.dropped.length > 0 && (
              <div className="flag small">The new draft has no item for {plan.dropped.join(", ")}, which the last draft had.</div>
            )}
            {locked && (
              <div className="notice-line">
                Approved. The edits wait for you in Changes, and nothing reaches your files until you accept it there.
              </div>
            )}
            {plan.items.map((item) => (
              <ItemCard key={item.id} item={item} comments={state.comments} locked={locked} onDecide={(i, d, n) => void decide(i, d, n)} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

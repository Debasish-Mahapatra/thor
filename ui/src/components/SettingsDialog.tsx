import { useEffect, useState } from "react";
import { api, type AgentSettings, type AgentStatus, type SettingsResponse } from "../api";
import { Modal } from "./Modal";

interface Props {
  onClose: () => void;
  onSaved: () => void;
  onSystemCheck: () => void;
  onError: (e: unknown) => void;
}

function describeClaude(status: AgentStatus | null): string {
  const c = status?.claude;
  if (!status) return "Checking Claude Code...";
  if (!c?.path) return c?.detail || "Claude Code was not found.";
  if (c.logged_in === false) return c.detail;
  const who = [c.email, c.plan && `Claude ${c.plan[0].toUpperCase()}${c.plan.slice(1)}`].filter(Boolean).join(", ");
  return `${who ? `Signed in as ${who}. ` : ""}Claude Code ${c.version ?? ""} at ${c.path}`;
}

export function SettingsDialog({ onClose, onSaved, onSystemCheck, onError }: Props) {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [agent, setAgent] = useState<AgentSettings | null>(null);
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [key, setKey] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.settings().then((s) => {
      setData(s);
      setAgent(s.agent);
    }, onError);
    api.agentStatus().then(setStatus, onError);
  }, [onError]);

  if (!data || !agent) {
    return (
      <Modal title="Settings" onClose={onClose}>
        <p className="muted">Loading...</p>
      </Modal>
    );
  }

  const set = <K extends keyof AgentSettings>(k: K, v: AgentSettings[K]) => setAgent({ ...agent, [k]: v });
  const knownModel = data.models.some((m) => m.id === agent.model);

  const save = async () => {
    setSaving(true);
    try {
      const saved = await api.saveSettings(agent);
      setData(saved);
      setAgent(saved.agent);
      setStatus(await api.agentStatus());
      setNote("Saved.");
      onSaved();
    } catch (e) {
      onError(e);
    } finally {
      setSaving(false);
    }
  };

  const saveKey = async () => {
    try {
      const r = await api.saveApiKey(key);
      setData({ ...data, api_key_saved: r.api_key_saved });
      setKey("");
      setNote("The API key is in your Keychain.");
    } catch (e) {
      onError(e);
    }
  };

  const removeKey = async () => {
    try {
      const r = await api.deleteApiKey();
      setData({ ...data, api_key_saved: r.api_key_saved });
      setNote("The API key was removed from your Keychain.");
    } catch (e) {
      onError(e);
    }
  };

  return (
    <Modal title="Settings" onClose={onClose}>
      <h3>How the agent signs in</h3>
      <label className="radio">
        <input
          type="radio"
          name="auth"
          checked={agent.auth === "claude_plan"}
          onChange={() => set("auth", "claude_plan")}
        />
        <span>
          <strong>Your Claude plan, through Claude Code</strong>
          <span className="muted small block">{describeClaude(status)}</span>
        </span>
      </label>
      {agent.auth === "claude_plan" && (
        <div className="field indent">
          <label htmlFor="claude-path">Claude Code location</label>
          <div>
            <input
              id="claude-path"
              value={agent.claude_path}
              placeholder="Found automatically"
              spellCheck={false}
              onChange={(e) => set("claude_path", e.target.value)}
            />
          </div>
        </div>
      )}
      <label className="radio">
        <input type="radio" name="auth" checked={agent.auth === "api_key"} onChange={() => set("auth", "api_key")} />
        <span>
          <strong>An Anthropic API key</strong>
          <span className="muted small block">
            Billed per token by Anthropic.{" "}
            {data.api_key_saved ? "A key is saved in your Keychain." : "No key saved."}
          </span>
        </span>
      </label>
      {agent.auth === "api_key" && (
        <>
          <div className="field indent">
            <label htmlFor="api-key">API key</label>
            <div className="row">
              <input
                id="api-key"
                type="password"
                value={key}
                placeholder="sk-ant-..."
                autoComplete="off"
                onChange={(e) => setKey(e.target.value)}
              />
              <button onClick={saveKey} disabled={!key.trim()}>
                Save to Keychain
              </button>
              {data.api_key_saved && (
                <button className="ghost" onClick={removeKey}>
                  Remove
                </button>
              )}
            </div>
          </div>
          <div className="field indent">
            <label htmlFor="budget">Spending cap</label>
            <div>
              <span className="row">
                $
                <input
                  id="budget"
                  type="number"
                  min={0.5}
                  step={0.5}
                  value={agent.budget_usd}
                  onChange={(e) => set("budget_usd", Number(e.target.value))}
                  className="narrow"
                />
                per agent run
              </span>
              <span className="help">A run stops when its estimated cost reaches this.</span>
            </div>
          </div>
        </>
      )}

      <h3>Model</h3>
      <div className="field">
        <label htmlFor="model">Model</label>
        <div className="row">
          <select
            id="model"
            value={knownModel ? agent.model : "custom"}
            onChange={(e) => set("model", e.target.value === "custom" ? "" : e.target.value)}
          >
            {data.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
            <option value="custom">Other model ID...</option>
          </select>
          {!knownModel && (
            <input
              aria-label="Model ID"
              value={agent.model}
              placeholder="claude-..."
              spellCheck={false}
              onChange={(e) => set("model", e.target.value)}
            />
          )}
        </div>
      </div>
      <div className="field">
        <label htmlFor="effort">Effort</label>
        <div>
          <select id="effort" value={agent.effort} onChange={(e) => set("effort", e.target.value)}>
            {data.efforts.map((e) => (
              <option key={e} value={e}>
                {e}
              </option>
            ))}
          </select>
          <span className="help">How much the model thinks. Higher is slower and uses more tokens.</span>
        </div>
      </div>

      <h3>System</h3>
      <div className="field">
        <label>Installed tools</label>
        <div>
          <button className="select-start" onClick={onSystemCheck}>
            System check...
          </button>
          <span className="help">MacTeX, Python packages and Claude Code: what is installed, and where.</span>
        </div>
      </div>

      <div className="modal-foot">
        <span className="muted">{note}</span>
        <button className="primary" onClick={save} disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
    </Modal>
  );
}

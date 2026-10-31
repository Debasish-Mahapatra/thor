import { useCallback, useEffect, useState } from "react";
import { api, type AgentSettings, type Provider, type SettingsResponse } from "../api";
import { useLayoutValue } from "../layout";
import type { EditorKeys } from "./Editor";
import { Modal } from "./Modal";

interface Props {
  onClose: () => void;
  onSaved: () => void;
  onSystemCheck: () => void;
  onError: (e: unknown) => void;
}

// Servers that speak the OpenAI chat API. The list only fills in the address; any server can be typed in.
const SERVER_PRESETS = [
  { name: "Ollama (on this Mac)", url: "http://127.0.0.1:11434/v1" },
  { name: "LM Studio (on this Mac)", url: "http://127.0.0.1:1234/v1" },
  { name: "OpenRouter", url: "https://openrouter.ai/api/v1" },
  { name: "Google Gemini", url: "https://generativelanguage.googleapis.com/v1beta/openai" },
  { name: "Groq", url: "https://api.groq.com/openai/v1" },
  { name: "DeepSeek", url: "https://api.deepseek.com/v1" },
  { name: "Mistral", url: "https://api.mistral.ai/v1" },
  { name: "Together", url: "https://api.together.xyz/v1" },
];

const newId = () => Math.random().toString(36).slice(2, 10);

/** The key field for one provider. It never shows a saved key: the key stays in the Keychain. */
function KeyField({ provider, placeholder, onChanged, onError }: { provider: Provider; placeholder: string; onChanged: () => void; onError: (e: unknown) => void }) {
  const [key, setKey] = useState("");
  const save = async () => {
    try {
      await api.saveKey(provider.id, key);
      setKey("");
      onChanged();
    } catch (e) {
      onError(e);
    }
  };
  const remove = async () => {
    try {
      await api.deleteKey(provider.id);
      onChanged();
    } catch (e) {
      onError(e);
    }
  };
  return (
    <div className="field indent">
      <label>API key</label>
      <div className="row">
        <input type="password" value={key} placeholder={provider.has_key ? "Saved in your Keychain" : placeholder} autoComplete="off" onChange={(e) => setKey(e.target.value)} />
        <button onClick={() => void save()} disabled={!key.trim()}>
          Save to Keychain
        </button>
        {provider.has_key && (
          <button className="ghost" onClick={() => void remove()}>
            Remove
          </button>
        )}
      </div>
    </div>
  );
}

/** A model ID, with what the provider itself offers when asked. */
function ModelField({ provider, value, onChange, models, onError }: { provider: Provider; value: string; onChange: (v: string) => void; models: { id: string; name: string }[]; onError: (e: unknown) => void }) {
  const [found, setFound] = useState<{ id: string; name: string }[]>([]);
  const [asking, setAsking] = useState(false);
  const known = [...models, ...found.filter((m) => !models.some((k) => k.id === m.id))];
  const listId = `models-${provider.id}`;
  const ask = async () => {
    setAsking(true);
    try {
      setFound((await api.providerModels(provider.id)).models);
    } catch (e) {
      onError(e);
    } finally {
      setAsking(false);
    }
  };
  return (
    <div className="field indent">
      <label>Model</label>
      <div className="row">
        <input list={listId} value={value} placeholder={provider.placeholder ?? "Model ID"} spellCheck={false} onChange={(e) => onChange(e.target.value)} />
        <datalist id={listId}>
          {known.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </datalist>
        {provider.kind !== "cli" && (
          <button onClick={() => void ask()} disabled={asking}>
            {asking ? "Asking..." : "List its models"}
          </button>
        )}
      </div>
    </div>
  );
}

export function SettingsDialog({ onClose, onSaved, onSystemCheck, onError }: Props) {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [agent, setAgent] = useState<AgentSettings | null>(null);
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [editorKeys, setEditorKeys] = useLayoutValue<EditorKeys>("editorKeys", "default");
  const [adding, setAdding] = useState<"" | "server" | "command">("");
  const [draftServer, setDraftServer] = useState({ name: "", url: "", model: "", key: "" });
  const [draftCommand, setDraftCommand] = useState({ name: "", command: "", edits: false, sandboxed: true });

  const reloadProviders = useCallback(() => {
    api.providers().then((found) => setProviders(found.providers), onError);
  }, [onError]);

  useEffect(() => {
    // Shows the model the agent will really use, which is another one if the chosen one is not set up.
    Promise.all([api.settings(), api.providers()]).then(([s, found]) => {
      setData(s);
      setAgent({ ...s.agent, provider: found.current });
      setProviders(found.providers);
    }, onError);
  }, [onError]);

  if (!data || !agent) {
    return (
      <Modal title="Settings" onClose={onClose}>
        <p className="muted">Loading...</p>
      </Modal>
    );
  }

  const set = <K extends keyof AgentSettings>(k: K, v: AgentSettings[K]) => setAgent({ ...agent, [k]: v });

  const persist = async (next: AgentSettings, message: string) => {
    setSaving(true);
    try {
      const saved = await api.saveSettings(next);
      setData(saved);
      setAgent(saved.agent);
      reloadProviders();
      setNote(message);
      onSaved();
      return true;
    } catch (e) {
      onError(e);
      return false;
    } finally {
      setSaving(false);
    }
  };

  const addServer = async () => {
    const id = newId();
    const next = {
      ...agent,
      provider: `endpoint:${id}`,
      endpoints: [...agent.endpoints, { id, name: draftServer.name.trim() || draftServer.url, base_url: draftServer.url.trim(), model: draftServer.model.trim() }],
    };
    if (!(await persist(next, "Added."))) return;
    if (draftServer.key.trim()) await api.saveKey(`endpoint:${id}`, draftServer.key).catch(onError);
    setDraftServer({ name: "", url: "", model: "", key: "" });
    setAdding("");
    reloadProviders();
  };

  const addCommand = async () => {
    const id = newId();
    const next = {
      ...agent,
      provider: `command:${id}`,
      commands: [...agent.commands, { id, name: draftCommand.name.trim() || draftCommand.command.split(" ")[0], command: draftCommand.command.trim(), edits_files: draftCommand.edits, sandboxed: draftCommand.sandboxed }],
    };
    if (!(await persist(next, "Added."))) return;
    setDraftCommand({ name: "", command: "", edits: false, sandboxed: true });
    setAdding("");
  };

  const removeProvider = (p: Provider) => {
    const ident = p.id.split(":")[1];
    const next = {
      ...agent,
      provider: agent.provider === p.id ? "claude_code" : agent.provider,
      endpoints: agent.endpoints.filter((e) => e.id !== ident),
      commands: agent.commands.filter((c) => c.id !== ident),
    };
    if (p.kind === "endpoint") void api.deleteKey(p.id).catch(() => undefined);
    void persist(next, "Removed.");
  };

  const modelValue = (p: Provider): string => {
    if (p.id === "claude_code" || p.id === "anthropic_api") return agent.model;
    if (p.id === "codex") return agent.codex_model;
    if (p.id === "openai_api") return agent.openai_model;
    if (p.id === "ollama") return agent.ollama_model || p.model;
    if (p.id === "lmstudio") return agent.lmstudio_model || p.model;
    return agent.endpoints.find((e) => `endpoint:${e.id}` === p.id)?.model ?? "";
  };

  const setModelValue = (p: Provider, value: string) => {
    if (p.id === "claude_code" || p.id === "anthropic_api") set("model", value);
    else if (p.id === "codex") set("codex_model", value);
    else if (p.id === "openai_api") set("openai_model", value);
    else if (p.id === "ollama") set("ollama_model", value);
    else if (p.id === "lmstudio") set("lmstudio_model", value);
    else set("endpoints", agent.endpoints.map((e) => (`endpoint:${e.id}` === p.id ? { ...e, model: value } : e)));
  };

  return (
    <Modal title="Settings" onClose={onClose}>
      <h3>The agent's model</h3>
      {!providers && <p className="muted">Checking what is installed...</p>}
      {providers?.map((p) => {
        const chosen = agent.provider === p.id;
        return (
          <div key={p.id}>
            <label className="radio">
              <input type="radio" name="provider" checked={chosen} onChange={() => set("provider", p.id)} />
              <span>
                <strong>{p.name}</strong> <span className="muted small">{p.billing}</span>
                <span className={`small block ${p.ready ? "muted" : "warn-text"}`}>
                  {p.detail}
                  {p.version ? ` Version ${p.version}.` : ""}
                </span>
              </span>
            </label>
            {chosen && (
              <>
                {!p.ready && p.fix && <div className="field indent warn-text small">{p.fix}</div>}
                {p.id === "claude_code" && (
                  <div className="field indent">
                    <label htmlFor="claude-path">Location</label>
                    <div>
                      <input id="claude-path" value={agent.claude_path} placeholder={p.path ?? "Found automatically"} spellCheck={false} onChange={(e) => set("claude_path", e.target.value)} />
                    </div>
                  </div>
                )}
                {p.id === "codex" && (
                  <div className="field indent">
                    <label htmlFor="codex-path">Location</label>
                    <div>
                      <input id="codex-path" value={agent.codex_path} placeholder={p.path ?? "Found automatically"} spellCheck={false} onChange={(e) => set("codex_path", e.target.value)} />
                    </div>
                  </div>
                )}
                {p.id === "ollama" && (
                  <label className="check small field indent">
                    <input type="checkbox" checked={agent.local_keep_loaded} onChange={(e) => set("local_keep_loaded", e.target.checked)} />
                    Keep the model loaded for 30 minutes, so messages do not wait for it. A 6 GB model holds about 15 GB of memory while loaded.
                  </label>
                )}
                {p.kind === "command" && (
                  <label className="check small field indent">
                    <input
                      type="checkbox"
                      checked={agent.commands.find((c) => `command:${c.id}` === p.id)?.sandboxed ?? true}
                      onChange={(e) => set("commands", agent.commands.map((c) => (`command:${c.id}` === p.id ? { ...c, sandboxed: e.target.checked } : c)))}
                    />
                    Keep it in a sandbox
                  </label>
                )}
                {p.kind !== "command" && <ModelField provider={p} value={modelValue(p)} onChange={(v) => setModelValue(p, v)} models={p.models} onError={onError} />}
                {(p.kind === "api" || p.kind === "endpoint") && (
                  <KeyField provider={p} placeholder={p.id === "anthropic_api" ? "sk-ant-..." : p.kind === "endpoint" ? "Leave empty if it needs none" : "sk-..."} onChanged={reloadProviders} onError={onError} />
                )}
                {p.id === "anthropic_api" && (
                  <div className="field indent">
                    <label htmlFor="budget">Spending cap</label>
                    <div>
                      <span className="row">
                        $
                        <input id="budget" type="number" min={0.5} step={0.5} value={agent.budget_usd} onChange={(e) => set("budget_usd", Number(e.target.value))} className="narrow" />
                        per turn
                      </span>
                      <span className="help">A turn stops when its estimated cost reaches this.</span>
                    </div>
                  </div>
                )}
                {(p.kind === "endpoint" || p.kind === "command") && (
                  <div className="field indent">
                    <label />
                    <div>
                      <button className="ghost" onClick={() => removeProvider(p)}>
                        Remove {p.name}
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        );
      })}

      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={() => setAdding(adding === "server" ? "" : "server")}>Add a model server...</button>
        <button onClick={() => setAdding(adding === "command" ? "" : "command")}>Add a command...</button>
      </div>
      {adding === "server" && (
        <div className="add-provider">
          <p className="muted small">Any server that speaks the OpenAI chat API, on this Mac or elsewhere.</p>
          <div className="field">
            <label>Kind</label>
            <div>
              <select
                value=""
                onChange={(e) => {
                  const preset = SERVER_PRESETS.find((s) => s.name === e.target.value);
                  if (preset) setDraftServer({ ...draftServer, name: preset.name.replace(/ \(.*$/, ""), url: preset.url });
                }}
              >
                <option value="">Choose to fill in the address...</option>
                {SERVER_PRESETS.map((s) => (
                  <option key={s.name}>{s.name}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="field">
            <label>Name</label>
            <div>
              <input value={draftServer.name} onChange={(e) => setDraftServer({ ...draftServer, name: e.target.value })} />
            </div>
          </div>
          <div className="field">
            <label>Address</label>
            <div>
              <input value={draftServer.url} placeholder="https://host/v1" spellCheck={false} onChange={(e) => setDraftServer({ ...draftServer, url: e.target.value })} />
            </div>
          </div>
          <div className="field">
            <label>Model</label>
            <div>
              <input value={draftServer.model} placeholder="Model ID" spellCheck={false} onChange={(e) => setDraftServer({ ...draftServer, model: e.target.value })} />
            </div>
          </div>
          <div className="field">
            <label>API key</label>
            <div>
              <input type="password" value={draftServer.key} placeholder="Leave empty if it needs none" autoComplete="off" onChange={(e) => setDraftServer({ ...draftServer, key: e.target.value })} />
            </div>
          </div>
          <button className="primary" disabled={!draftServer.url.trim() || saving} onClick={() => void addServer()}>
            Add
          </button>
        </div>
      )}
      {adding === "command" && (
        <div className="add-provider">
          <p className="muted small">
            Any command-line program, such as <code>ollama run llama4</code> or <code>gemini -p {"{prompt}"}</code>. It runs in the agent's copy of the paper. The prompt goes to its input, or replaces {"{prompt}"}.
          </p>
          <div className="field">
            <label>Name</label>
            <div>
              <input value={draftCommand.name} onChange={(e) => setDraftCommand({ ...draftCommand, name: e.target.value })} />
            </div>
          </div>
          <div className="field">
            <label>Command</label>
            <div>
              <input value={draftCommand.command} spellCheck={false} onChange={(e) => setDraftCommand({ ...draftCommand, command: e.target.value })} />
            </div>
          </div>
          <label className="check small" style={{ margin: "6px 0" }}>
            <input type="checkbox" checked={draftCommand.edits} onChange={(e) => setDraftCommand({ ...draftCommand, edits: e.target.checked })} />
            It edits files itself. Otherwise it is shown the open files and proposes edits as text.
          </label>
          <label className="check small" style={{ margin: "6px 0" }}>
            <input type="checkbox" checked={draftCommand.sandboxed} onChange={(e) => setDraftCommand({ ...draftCommand, sandboxed: e.target.checked })} />
            Keep it in a sandbox. It can write only in its copy of the paper and in its own settings, and cannot read your ssh keys or other sign-ins. Turn this off only for a program you trust that needs more.
          </label>
          <div>
            <button className="primary" disabled={!draftCommand.command.trim() || saving} onClick={() => void addCommand()}>
              Add
            </button>
          </div>
        </div>
      )}

      <h3>Reading open copies of references</h3>
      <div className="field">
        <label htmlFor="unpaywall-email">Email for Unpaywall</label>
        <div>
          <input id="unpaywall-email" type="email" value={agent.unpaywall_email} placeholder="Not set" spellCheck={false} onChange={(e) => set("unpaywall_email", e.target.value)} />
          <div className="muted small">Unpaywall asks each caller for an address. It is sent only to Unpaywall, and only when the agent looks for a legal open copy of a paper. Leave it empty to turn this off.</div>
        </div>
      </div>

      <h3>Effort</h3>
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
          <span className="help">How much the model thinks. Higher is slower and uses more tokens. Models without this setting ignore it.</span>
        </div>
      </div>

      <h3>Editor</h3>
      <div className="field">
        <label htmlFor="editor-keys">Keys</label>
        <div>
          <select id="editor-keys" value={editorKeys} onChange={(e) => setEditorKeys(e.target.value as EditorKeys)}>
            <option value="default">Standard</option>
            <option value="vim">Vim</option>
            <option value="emacs">Emacs</option>
          </select>
          <span className="help">Takes effect at once. Thor's cursor never blinks, in any of them.</span>
        </div>
      </div>

      <h3>System</h3>
      <div className="field">
        <label>Installed tools</label>
        <div>
          <button className="select-start" onClick={onSystemCheck}>
            System check...
          </button>
          <span className="help">MacTeX, Python packages and the agent's tools: what is installed, and where.</span>
        </div>
      </div>

      <div className="modal-foot">
        <span className="muted">{note}</span>
        <button className="primary" onClick={() => void persist(agent, "Saved.")} disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
    </Modal>
  );
}

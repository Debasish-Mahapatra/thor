// The bridge to the Swift shell. In a plain browser (development, tests) these fall back gracefully.

interface ShellHandler {
  postMessage(message: unknown): Promise<unknown>;
}

declare global {
  interface Window {
    webkit?: { messageHandlers?: { thor?: ShellHandler } };
  }
}

export interface ShellCommand {
  cmd: "openProject" | "openSettings";
  arg?: string;
}

function shell(): ShellHandler | undefined {
  return window.webkit?.messageHandlers?.thor;
}

export function inShell(): boolean {
  return shell() !== undefined;
}

/** Shows the macOS folder picker; null when cancelled or outside the app. */
export async function pickFolder(): Promise<string | null> {
  const handler = shell();
  if (!handler) return null;
  const path = await handler.postMessage({ cmd: "pickFolder" });
  return typeof path === "string" && path ? path : null;
}

/** Shows the macOS file picker for a PDF; null when cancelled or outside the app. */
export async function pickPdf(): Promise<string | null> {
  const handler = shell();
  if (!handler) return null;
  const path = await handler.postMessage({ cmd: "pickPdf" });
  return typeof path === "string" && path ? path : null;
}

/** Menu commands from the shell (File > Open Project..., the app menu's Settings...). */
export function onShellCommand(handler: (command: ShellCommand) => void): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<ShellCommand>).detail);
  window.addEventListener("thor-shell", listener);
  return () => window.removeEventListener("thor-shell", listener);
}

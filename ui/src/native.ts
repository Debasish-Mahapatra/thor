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

/** The window has no title bar, so pressing on an empty part of a toolbar moves the window, and a
 * double-click zooms it, as on a title bar. */
export function installTitlebarBehaviour(): void {
  const handler = shell();
  if (!handler) return;
  const onBar = (e: MouseEvent) => {
    const target = e.target as HTMLElement;
    return (
      e.button === 0 &&
      !!target.closest(".toolbar, .topbar, .tabs") &&
      !target.closest("button, select, input, textarea, a, label, [contenteditable]")
    );
  };
  document.addEventListener("mousedown", (e) => {
    if (e.detail === 1 && onBar(e)) void handler.postMessage({ cmd: "dragWindow" });
  });
  document.addEventListener("dblclick", (e) => {
    if (onBar(e)) void handler.postMessage({ cmd: "titlebarDoubleClick" });
  });
}

/** Menu commands from the shell (File > Open Project..., the app menu's Settings...). */
export function onShellCommand(handler: (command: ShellCommand) => void): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<ShellCommand>).detail);
  window.addEventListener("thor-shell", listener);
  return () => window.removeEventListener("thor-shell", listener);
}

import type { Check } from "./api";

export interface CheckGroup {
  group: string;
  checks: Check[];
  missingRequired: number;
}

/** Checks grouped in the order the backend sends them, with a count of missing required items. */
export function groupChecks(checks: Check[]): CheckGroup[] {
  const groups = new Map<string, Check[]>();
  for (const check of checks) {
    const list = groups.get(check.group) ?? [];
    list.push(check);
    groups.set(check.group, list);
  }
  return [...groups].map(([group, list]) => ({
    group,
    checks: list,
    missingRequired: list.filter((c) => c.required && !c.ok).length,
  }));
}

/** The fix shared by two or more unmet checks of a group, shown once above them. */
export function sharedFix(checks: Check[]): string | null {
  const counts = new Map<string, number>();
  for (const c of checks) {
    if (!c.ok && c.fix) counts.set(c.fix, (counts.get(c.fix) ?? 0) + 1);
  }
  let best: string | null = null;
  for (const [fix, n] of counts) {
    if (n >= 2 && (best === null || n > (counts.get(best) ?? 0))) best = fix;
  }
  return best;
}

export function formatSize(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "3 hours ago" style text for a Unix time in seconds. */
export function ago(seconds: number, now: number = Date.now() / 1000): string {
  const d = Math.max(0, now - seconds);
  if (d < 60) return "just now";
  const units: [number, string][] = [
    [86400 * 365, "year"],
    [86400 * 30, "month"],
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
  ];
  for (const [size, name] of units) {
    if (d >= size) {
      const n = Math.floor(d / size);
      return `${n} ${name}${n === 1 ? "" : "s"} ago`;
    }
  }
  return "just now";
}

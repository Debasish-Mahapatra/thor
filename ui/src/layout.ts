// The window's layout choices, kept by the backend: the app's port changes with each launch, and browser storage
// goes with the port. Loaded once before the first render, so the window opens as it was left.

import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

type Value = boolean | number | string;

let values: Record<string, Value> = {};
let pending: Record<string, Value> = {};
let timer: number | undefined;
// Every use of a key stays the same: Settings can change what the editor reads.
const listeners = new Map<string, Set<(value: Value) => void>>();

export async function loadLayout(): Promise<void> {
  try {
    values = await api.layout();
  } catch {
    // Not signed in yet, or the backend is starting: the defaults do.
  }
}

function save(key: string, value: Value): void {
  values[key] = value;
  pending[key] = value;
  window.clearTimeout(timer);
  timer = window.setTimeout(() => {
    const changes = pending;
    pending = {};
    api.saveLayout(changes).catch(() => undefined);
  }, 300);
}

export function useLayoutValue(key: string, fallback: boolean): [boolean, (value: boolean) => void];
export function useLayoutValue(key: string, fallback: number): [number, (value: number) => void];
export function useLayoutValue<T extends string>(key: string, fallback: T): [T, (value: T) => void];
export function useLayoutValue<T extends Value>(key: string, fallback: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => (typeof values[key] === typeof fallback ? (values[key] as T) : fallback));
  useEffect(() => {
    const heard = (next: Value) => setValue(next as T);
    const group = listeners.get(key) ?? new Set();
    group.add(heard);
    listeners.set(key, group);
    return () => {
      group.delete(heard);
    };
  }, [key]);
  const set = useCallback(
    (next: T) => {
      save(key, next);
      for (const heard of listeners.get(key) ?? []) heard(next);
    },
    [key],
  );
  return [value, set];
}

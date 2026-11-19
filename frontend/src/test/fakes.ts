import type { RunEvent } from "../types/revival";

/** Minimal EventSource double that records connections and lets a test push frames. */
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, ((m: MessageEvent<string>) => void)[]>();
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (m: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  open() { this.readyState = 1; this.onopen?.(); }
  emit(event: RunEvent) {
    for (const fn of this.listeners.get(event.type) ?? [])
      fn(new MessageEvent(event.type, { data: JSON.stringify(event) }));
  }
  emitRaw(type: string, data: string) {
    for (const fn of this.listeners.get(type) ?? []) fn(new MessageEvent(type, { data }));
  }
  static reset() { FakeEventSource.instances = []; }
  static get last() { return FakeEventSource.instances.at(-1)!; }
}

export function ev(sequence: number, type: RunEvent["type"], payload: Record<string, unknown> = {}, runId = "run-1"): RunEvent {
  return { event_id: `${runId}:${sequence}`, run_id: runId, sequence, type, timestamp: "2026-11-19T10:00:00Z", payload };
}

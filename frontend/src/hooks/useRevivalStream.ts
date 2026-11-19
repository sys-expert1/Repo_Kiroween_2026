import { useEffect, useRef, useState } from "react";

import { api } from "../services/api";
import { applyEvent, emptyView, type RunView } from "../state/runState";
import type { EventType, RunEvent } from "../types/revival";

export type ConnectionState = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "failed";

const EVENT_TYPES: EventType[] = [
  "run_started", "stage_changed", "health_score_updated", "roadmap_ready", "hitl_required",
  "approval_received", "diff_generated", "terminal_log", "verification_completed", "run_completed",
  "run_failed", "run_cancelled", "cancel_requested", "patch_applied", "rollback_completed",
];
const TERMINAL_EVENTS = new Set<EventType>(["run_completed", "run_failed", "run_cancelled"]);
const MAX_ATTEMPTS = 8;

/**
 * Subscribes to a run's SSE stream. Replays from the backend log on connect, resumes from the last
 * seen sequence on reconnect, ignores duplicate sequences, and closes on a terminal event (the
 * server closes terminal streams, which would otherwise make EventSource reconnect forever).
 */
export function useRevivalStream(runId: string | null) {
  const [view, setView] = useState<RunView>(() => emptyView(null));
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const lastSeq = useRef(0);

  useEffect(() => {
    lastSeq.current = 0;
    setView(emptyView(runId));
    if (!runId) {
      setConnection("idle");
      return;
    }
    let stopped = false;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;

    const onEvent = (message: MessageEvent<string>) => {
      let event: RunEvent;
      try {
        event = JSON.parse(message.data) as RunEvent;
      } catch {
        return; // a malformed frame must not break the stream
      }
      lastSeq.current = Math.max(lastSeq.current, event.sequence);
      setView((v) => applyEvent(v, event));
      if (TERMINAL_EVENTS.has(event.type)) {
        stopped = true;
        source?.close();
        setConnection("closed");
      }
    };

    const connect = () => {
      setConnection(attempts === 0 ? "connecting" : "reconnecting");
      source = new EventSource(api.streamUrl(runId, lastSeq.current));
      source.onopen = () => {
        attempts = 0;
        setConnection("open");
      };
      for (const type of EVENT_TYPES) source.addEventListener(type, onEvent as EventListener);
      source.onerror = () => {
        if (stopped) return;
        if (source?.readyState === EventSource.CLOSED) {
          // The browser gave up (for example an HTTP error). Retry with backoff, then stop.
          source.close();
          attempts += 1;
          if (attempts > MAX_ATTEMPTS) {
            setConnection("failed");
            return;
          }
          setConnection("reconnecting");
          timer = setTimeout(connect, Math.min(1000 * 2 ** attempts, 15000));
        } else {
          setConnection("reconnecting"); // the browser reconnects and sends Last-Event-ID
        }
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      source?.close();
    };
  }, [runId]);

  return { view, connection };
}

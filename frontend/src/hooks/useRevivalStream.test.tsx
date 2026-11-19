import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ev, FakeEventSource } from "../test/fakes";
import { useRevivalStream } from "./useRevivalStream";

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useRevivalStream", () => {
  it("does nothing without a run id", () => {
    const { result } = renderHook(() => useRevivalStream(null));
    expect(result.current.connection).toBe("idle");
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("applies events in order and drops replayed duplicates", () => {
    const { result } = renderHook(() => useRevivalStream("run-1"));
    expect(FakeEventSource.last.url).toBe("/api/revive/stream/run-1");
    act(() => FakeEventSource.last.open());
    act(() => {
      FakeEventSource.last.emit(ev(1, "run_started"));
      FakeEventSource.last.emit(ev(2, "stage_changed", { stage: "AUTOPSY", status: "RUNNING" }));
      FakeEventSource.last.emit(ev(2, "stage_changed", { stage: "AUTOPSY", status: "RUNNING" })); // duplicate
    });
    expect(result.current.connection).toBe("open");
    expect(result.current.view.events.map((e) => e.sequence)).toEqual([1, 2]);
    expect(result.current.view.stage).toBe("AUTOPSY");
  });

  it("ignores malformed frames without breaking the stream", () => {
    const { result } = renderHook(() => useRevivalStream("run-1"));
    act(() => {
      FakeEventSource.last.emitRaw("stage_changed", "{not json");
      FakeEventSource.last.emit(ev(1, "run_started"));
    });
    expect(result.current.view.events).toHaveLength(1);
  });

  it("shows reconnecting while the browser retries on its own", () => {
    const { result } = renderHook(() => useRevivalStream("run-1"));
    act(() => FakeEventSource.last.open());
    act(() => {
      FakeEventSource.last.readyState = 0; // CONNECTING: the browser will reconnect
      FakeEventSource.last.onerror?.();
    });
    expect(result.current.connection).toBe("reconnecting");
  });

  it("resumes from the last seen sequence when it has to open a new connection", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useRevivalStream("run-1"));
    act(() => {
      FakeEventSource.last.open();
      FakeEventSource.last.emit(ev(1, "run_started"));
      FakeEventSource.last.emit(ev(2, "stage_changed", { stage: "AUTOPSY", status: "RUNNING" }));
    });
    act(() => {
      FakeEventSource.last.readyState = 2; // CLOSED: the browser gave up
      FakeEventSource.last.onerror?.();
    });
    expect(result.current.connection).toBe("reconnecting");
    act(() => { vi.advanceTimersByTime(2500); });
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.last.url).toBe("/api/revive/stream/run-1?after=2");
    act(() => FakeEventSource.last.emit(ev(2, "stage_changed", { stage: "AUTOPSY", status: "RUNNING" }))); // replayed
    act(() => FakeEventSource.last.emit(ev(3, "stage_changed", { stage: "CHARACTERIZATION", status: "RUNNING" })));
    expect(result.current.view.events.map((e) => e.sequence)).toEqual([1, 2, 3]);
  });

  it("gives up after bounded retries", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useRevivalStream("run-1"));
    for (let i = 0; i < 12; i++) {
      act(() => {
        FakeEventSource.last.readyState = 2;
        FakeEventSource.last.onerror?.();
      });
      act(() => { vi.advanceTimersByTime(20000); });
    }
    expect(result.current.connection).toBe("failed");
    expect(FakeEventSource.instances.length).toBeLessThanOrEqual(10);
  });

  it("closes the stream on a terminal event so it does not reconnect forever", () => {
    const { result } = renderHook(() => useRevivalStream("run-1"));
    act(() => {
      FakeEventSource.last.emit(ev(1, "run_started"));
      FakeEventSource.last.emit(ev(2, "run_completed", { status: "COMPLETED" }));
    });
    expect(FakeEventSource.last.closed).toBe(true);
    expect(result.current.connection).toBe("closed");
    act(() => FakeEventSource.last.onerror?.()); // the server closing the stream triggers onerror
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("closes the connection when the component unmounts or the run changes", () => {
    const { rerender, unmount } = renderHook(({ id }) => useRevivalStream(id), { initialProps: { id: "run-1" } });
    const first = FakeEventSource.last;
    rerender({ id: "run-2" });
    expect(first.closed).toBe(true);
    expect(FakeEventSource.last.url).toContain("run-2");
    unmount();
    expect(FakeEventSource.last.closed).toBe(true);
  });
});

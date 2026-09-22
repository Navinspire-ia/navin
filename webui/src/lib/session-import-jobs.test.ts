// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { importExternalSessions } from "./api";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("polls the same import beyond the old 30 second deadline", async () => {
  vi.useFakeTimers();
  const result = { workspace: "/tmp/workspace", sources: [{ name: "codex", imported: 700 }] };
  let polls = 0;
  const fetcher = vi.fn(async () => new Response(JSON.stringify(
    ++polls <= 40 ? { job_id: "job-1", status: "running" }
      : { job_id: "job-1", status: "done", result },
  ), { headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetcher);
  const importing = importExternalSessions("test-token", { source: "codex" });
  await vi.runAllTimersAsync();
  expect(await importing).toEqual(result);
  expect(fetcher).toHaveBeenCalledTimes(41);
  expect(String(fetcher.mock.calls[1][0])).toContain("job_id=job-1");
});

it("accepts results from older gateways", async () => {
  const result = { workspace: "/tmp/workspace", sources: [] };
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(result), {
    headers: { "content-type": "application/json" },
  })));
  expect(await importExternalSessions("test-token")).toEqual(result);
});

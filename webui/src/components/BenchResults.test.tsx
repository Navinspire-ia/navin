// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BenchResults } from "@/components/BenchResults";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));
const fetchMock = vi.fn();
vi.mock("@/lib/bench-api", () => ({ fetchBenchReport: (...a: unknown[]) => fetchMock(...a) }));

describe("BenchResults", () => {
  beforeEach(() => {
    cleanup();
    fetchMock.mockReset();
  });

  it("renders the empty state", async () => {
    fetchMock.mockResolvedValue({ report: null });
    render(<BenchResults token="t" />);
    await waitFor(() => expect(screen.getByText("bench.empty")).toBeTruthy());
  });

  it("renders a report and refreshes", async () => {
    fetchMock.mockResolvedValue({
      report: {
        repo: "r",
        dry_run: true,
        generated_at: "now",
        matrix: [
          { model: "m1", strategy: "s1", missions: 2, successes: 1, tokens: 0, usd: 0.5, seconds: 1, tests_failed: 0, lint_delta: 0, usd_per_success: 0.5 },
        ],
      },
    });
    render(<BenchResults token="t" />);
    await waitFor(() => expect(screen.getByText("m1")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "bench.refresh" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});

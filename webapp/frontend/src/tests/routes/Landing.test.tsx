import { describe, expect, it, vi, beforeEach } from "vitest";
import type { Mock } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Landing } from "../../routes/Landing";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: vi.fn(),
}));

vi.mock("../../api", () => ({
  fetchRepoOverview: vi.fn(),
}));

import { useAuth } from "../../auth/AuthContext";
import { fetchRepoOverview } from "../../api";

const mockUseAuth = useAuth as unknown as Mock;

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="vibeviewer" element={<div>vibeviewer sentinel</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Landing", () => {
  beforeEach(() => {
    mockUseAuth.mockReset();
    mockUseAuth.mockReturnValue({ loading: false, user: null });
    (fetchRepoOverview as Mock).mockReset();
    // Degrade to skeleton; no state update fires on rejection.
    (fetchRepoOverview as Mock).mockRejectedValue(new Error("no network in test"));
  });

  it("leads with team-collaboration messaging", () => {
    renderPage();
    expect(
      screen.getByText(/Your team.?s work, finally legible/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Review starts from intent/i)).toBeInTheDocument();
    expect(screen.getByText(/Searchable team history/i)).toBeInTheDocument();
  });

  it("shows the AI digest rows in the PR-comment mock", () => {
    renderPage();
    expect(screen.getByText("Ask")).toBeInTheDocument();
    expect(screen.getByText("Key decisions")).toBeInTheDocument();
    expect(screen.getByText("Dead ends")).toBeInTheDocument();
    expect(
      screen.getByText(/Reuse digest anchors as the nav spine/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/IntersectionObserver thrashed, switched to scroll math/i),
    ).toBeInTheDocument();
  });

  it("drops the solo brag-post framing from the front page", () => {
    renderPage();
    expect(screen.queryByText(/Brag posts/i)).not.toBeInTheDocument();
  });

  it("offers solo visitors a subtle pointer to the vibeviewer", () => {
    renderPage();
    expect(
      screen.getByText(/just want to show off a session/i),
    ).toBeInTheDocument();
  });

  it("advertises trace porting between Claude Code and Codex", () => {
    const { container } = renderPage();

    expect(screen.getByText(/Portable sessions/i)).toBeInTheDocument();
    expect(container).toHaveTextContent("/handoff");
    expect(container).toHaveTextContent("codex resume");
    expect(container).toHaveTextContent("/import-trace");
  });

  it("lists cursor as a supported platform, not a contribution ask", () => {
    const { container } = renderPage();

    expect(screen.getByText("cursor")).toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("contribute");
  });

  it("advertises plugin version 0.6.1 across landing install surfaces", () => {
    const { container } = renderPage();

    expect(screen.getByText("0.6.1")).toBeInTheDocument();
    expect(container).toHaveTextContent("USER_AGENT=vibeshub/0.6");
    expect(container).not.toHaveTextContent("0.4.0");
    expect(container).not.toHaveTextContent("vibeshub/0.4");
  });
});

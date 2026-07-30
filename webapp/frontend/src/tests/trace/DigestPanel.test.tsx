import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { DigestPanel } from "../../components/trace/DigestPanel";
import type { TraceDigest } from "../../types";

const sampleDigest: TraceDigest = {
  ask: "Add /healthcheck",
  decisions: [
    "Chose an inline route in main.py over a new router because YAGNI",
    "Chose starlette TestClient over httpx.AsyncClient because sync tests",
  ],
  dead_ends: ["Tried overflow-x first, abandoned because it broke the header"],
  learnings: ["TestClient needs raise_server_exceptions=False"],
  tests: "test_health.py",
  chapters: [
    { anchor_uuid: "u1", title: "Frame", caption: "User asks." },
    { anchor_uuid: "u2", title: "Land", caption: "Patch shipped." },
  ],
};

describe("DigestPanel", () => {
  it("renders the ask row and all three item groups", () => {
    render(<DigestPanel digest={sampleDigest} />);
    expect(screen.getByText(/^Ask$/i)).toBeInTheDocument();
    expect(screen.getByText("Add /healthcheck")).toBeInTheDocument();
    expect(screen.getByText(/Key decisions/i)).toBeInTheDocument();
    expect(screen.getByText(/Dead ends/i)).toBeInTheDocument();
    expect(screen.getByText(/Learnings/i)).toBeInTheDocument();
    expect(
      screen.getByText("TestClient needs raise_server_exceptions=False"),
    ).toBeInTheDocument();
  });

  it("renders multi-item groups as bullets and never shows tests", () => {
    render(<DigestPanel digest={sampleDigest} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByText("test_health.py")).not.toBeInTheDocument();
  });

  it("omits empty groups", () => {
    render(
      <DigestPanel digest={{ ...sampleDigest, dead_ends: [], learnings: [] }} />,
    );
    expect(screen.queryByText(/dead ends/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/learnings/i)).not.toBeInTheDocument();
    expect(screen.getByText("Add /healthcheck")).toBeInTheDocument();
  });

  it("does not render chapter jump chips (owned by the rail)", () => {
    render(<DigestPanel digest={sampleDigest} />);
    expect(screen.queryByText("Frame")).not.toBeInTheDocument();
    expect(screen.queryByText("Land")).not.toBeInTheDocument();
  });

  it("trims surrounding whitespace on group items", () => {
    render(
      <DigestPanel
        digest={{
          ...sampleDigest,
          decisions: ["  padded item  "],
          dead_ends: [],
          learnings: [],
        }}
      />,
    );
    const value = screen.getByText("padded item");
    // Raw textContent, not the RTL-normalized match, proves the trim.
    expect(value.textContent).toBe("padded item");
  });

  it("renders nothing when the ask is blank and every list is empty", () => {
    const { container } = render(
      <DigestPanel
        digest={{
          ...sampleDigest,
          ask: " ",
          decisions: [],
          dead_ends: [],
          learnings: [],
        }}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});

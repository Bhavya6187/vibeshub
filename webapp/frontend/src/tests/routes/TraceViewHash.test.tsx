import { describe, expect, it, vi } from "vitest";
import { scrollToHashAnchor } from "../../components/trace/chapterLink";

describe("scrollToHashAnchor", () => {
  it("scrolls the element matching the hash into view", () => {
    const el = document.createElement("div");
    el.id = "chapter-u1";
    el.scrollIntoView = vi.fn();
    document.body.appendChild(el);
    scrollToHashAnchor("#chapter-u1");
    expect(el.scrollIntoView).toHaveBeenCalledWith({ block: "start" });
    el.remove();
  });

  it("ignores empty and unknown hashes", () => {
    expect(() => scrollToHashAnchor("")).not.toThrow();
    expect(() => scrollToHashAnchor("#chapter-ghost")).not.toThrow();
  });
});

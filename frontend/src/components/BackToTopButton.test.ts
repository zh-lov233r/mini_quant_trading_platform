import { afterEach, expect, it, vi } from "vitest";
import { scrollToPageTop } from "./BackToTopButton";

afterEach(() => vi.unstubAllGlobals());

it("reads the current motion preference on every back-to-top action", () => {
  const scrollTo = vi.fn();
  const matchMedia = vi.fn().mockReturnValue({ matches: false });
  vi.stubGlobal("window", { scrollTo, matchMedia });
  scrollToPageTop();
  expect(scrollTo).toHaveBeenLastCalledWith({ top: 0, behavior: "smooth" });
  matchMedia.mockReturnValue({ matches: true });
  scrollToPageTop();
  expect(matchMedia).toHaveBeenLastCalledWith("(prefers-reduced-motion: reduce)");
  expect(scrollTo).toHaveBeenLastCalledWith({ top: 0, behavior: "instant" });
});

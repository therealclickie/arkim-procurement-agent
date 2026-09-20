// @vitest-environment jsdom
/**
 * T1 smoke + characterisation of GoferLoader — the branded loading indicator
 * both public pages (portal/[token], quote/[token]) render in their loading
 * states. Doubles as the proof that the jsdom + Testing Library environment
 * works alongside the node-environment unit tests.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { GoferLoader } from "../gofer-loader";

describe("GoferLoader", () => {
  it("exposes the spinner as an accessible image with a default label", () => {
    render(<GoferLoader />);
    // The public pages rely on the aria-label for screen-reader users; without
    // it the loading state would be a silent blank.
    expect(screen.getByRole("img", { name: "Loading" })).toBeTruthy();
  });

  it("carries a custom aria-label through to the accessible name", () => {
    render(<GoferLoader aria-label="Loading your supplier profile" />);
    expect(
      screen.getByRole("img", { name: "Loading your supplier profile" }),
    ).toBeTruthy();
  });

  it("reserves an explicit size box so loading causes no layout shift", () => {
    render(<GoferLoader size={120} aria-label="x" />);
    const svg = screen.getByRole("img", { name: "x" });
    expect(svg.getAttribute("width")).toBe("120");
    expect(svg.getAttribute("height")).toBe("120");
  });
});

// @vitest-environment jsdom
/**
 * PH-01 — the three hygienic fitment rows the asset panel gained (6bab036), so the
 * chat's "review in the panel and confirm" is true: the buyer can see the connection
 * size, the connection type and the certification the confirm gate reads.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AssetPanel } from "../asset-panel";
import type { AssetSpecs } from "@/types";

const specs = {
  manufacturer: "Ashcroft",
  model: "1032",
  detected_type: "pressure gauge",
  manufacturer_confidence: 95,
  connection_size: "1.5 inch",
  process_connection: "Tri-Clamp",
  hygienic_certification: "3-A",
} as AssetSpecs;

function row(label: string): string | null {
  const lbl = screen.queryByText(label, { selector: ".lbl" });
  return lbl?.parentElement?.querySelector(".val")?.textContent ?? null;
}

describe("AssetPanel hygienic rows", () => {
  it("renders Connection, Process conn. and Hygienic cert. with their values", () => {
    render(<AssetPanel specs={specs} defaultExpanded />);
    expect(row("Connection")).toBe("1.5 inch");
    expect(row("Process conn.")).toBe("Tri-Clamp");
    expect(row("Hygienic cert.")).toBe("3-A");
  });

  it("shows a 'not required' certification as an answer", () => {
    render(<AssetPanel specs={{ ...specs, hygienic_certification: "not required" }} defaultExpanded />);
    expect(row("Hygienic cert.")).toBe("not required");
  });

  it("omits the rows on a run that carries none of them", () => {
    const plain = { ...specs } as Record<string, unknown>;
    delete plain.connection_size;
    delete plain.process_connection;
    delete plain.hygienic_certification;
    render(<AssetPanel specs={plain as AssetSpecs} defaultExpanded />);
    expect(row("Connection")).toBeNull();
    expect(row("Process conn.")).toBeNull();
    expect(row("Hygienic cert.")).toBeNull();
  });
});

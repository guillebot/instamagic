import { expect, it } from "vitest";
import { defaultEdgeMode } from "./blurEdgeMode";

it("defaults rectangles to hard and ellipses to soft", () => {
  expect(defaultEdgeMode("rectangle")).toBe("hard");
  expect(defaultEdgeMode("ellipse")).toBe("soft");
});

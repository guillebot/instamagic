import { beforeEach, describe, expect, it } from "vitest";
import { BLUR_SHAPE_STORAGE_KEY, defaultEdgeMode, initialBlurShape, rememberBlurShape } from "./blurEdgeMode";

it("defaults rectangles to hard and ellipses to soft", () => {
  expect(defaultEdgeMode("rectangle")).toBe("hard");
  expect(defaultEdgeMode("ellipse")).toBe("soft");
});

describe("initialBlurShape", () => {
  beforeEach(() => window.localStorage.clear());
  it("defaults to rectangle when nothing is saved", () => {
    expect(initialBlurShape()).toBe("rectangle");
  });
  it("uses the last saved shape", () => {
    rememberBlurShape("ellipse");
    expect(initialBlurShape()).toBe("ellipse");
    rememberBlurShape("rectangle");
    expect(initialBlurShape()).toBe("rectangle");
  });
  it("ignores junk values", () => {
    window.localStorage.setItem(BLUR_SHAPE_STORAGE_KEY, "triangle");
    expect(initialBlurShape()).toBe("rectangle");
  });
});

import { describe, expect, it } from "vitest";
import { middleEllipsis } from "./middleEllipsis";

describe("middleEllipsis", () => {
  it("keeps short names whole", () => {
    expect(middleEllipsis("IMG_0698.jpg", 30)).toBe("IMG_0698.jpg");
  });
  it("keeps at least 10 chars at each end", () => {
    const out = middleEllipsis("2019-05-12 Long session name here_Shoot_Ana", 12);
    expect(out).toBe("2019-05-12…_Shoot_Ana");
  });
  it("uses the available budget", () => {
    const name = "2019-05-12 Long session name here_Shoot_Ana";
    const out = middleEllipsis(name, 30);
    expect(out.length).toBe(30);
    expect(out.startsWith("2019-05-12")).toBe(true);
    expect(out.endsWith("_Shoot_Ana")).toBe(true);
  });
});

export type BlurEdgeMode = "hard" | "soft";

export function defaultEdgeMode(shape: string): BlurEdgeMode {
  return shape === "rectangle" ? "hard" : "soft";
}

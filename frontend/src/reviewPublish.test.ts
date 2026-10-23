import { describe, expect, it } from "vitest";
import { canPublishNowFromReadiness, instagramConnectivity } from "./reviewPublish";
import type { ImagePublishReadiness } from "./types";

const base: ImagePublishReadiness = {
  publishing_allowed: true,
  publishing_reasons: [],
  caption_generation_allowed: true,
  caption_generation_reasons: [],
  library_attested: true,
  queue_item_id: null,
  queue_state: null,
  scheduled_at: null,
  published_at: null,
  instagram_configured: true,
  instagram_target_username: "foto.sch",
  feed_preview_url: null,
  uses_approved_crop: false,
};
const connected = { ok: true, loading: false, reason: "" };

describe("instagramConnectivity", () => {
  it("is loading (not ok) until status arrives", () => {
    expect(instagramConnectivity(undefined, undefined)).toMatchObject({ ok: false, loading: true });
  });
  it("flags missing token as disconnected", () => {
    const c = instagramConnectivity({ configured: true, token_configured: false }, []);
    expect(c.ok).toBe(false);
    expect(c.reason).toMatch(/Instagram disconnected — reconnect to post/);
  });
  it("flags health instagram=down as disconnected", () => {
    const c = instagramConnectivity({ configured: true, token_configured: true }, [
      { id: "instagram", status: "down", detail: "token expired" },
    ]);
    expect(c.ok).toBe(false);
    expect(c.reason).toContain("token expired");
  });
  it("treats a status fetch error as disconnected", () => {
    expect(instagramConnectivity(undefined, undefined, { statusError: true }).ok).toBe(false);
  });
  it("is ok when configured and health ok", () => {
    expect(
      instagramConnectivity({ configured: true, token_configured: true, business_account_id_configured: true }, [
        { id: "instagram", status: "ok", detail: "" },
      ]).ok,
    ).toBe(true);
  });
});

describe("canPublishNowFromReadiness", () => {
  it("allows an un-queued safe image with a caption (queue+approve happen in the confirmed flow)", () => {
    expect(canPublishNowFromReadiness(base, "cap1", connected).ok).toBe(true);
  });
  it("requires a caption and says so", () => {
    expect(canPublishNowFromReadiness(base, null, connected)).toEqual({
      ok: false,
      reason: "Pick or generate a caption before posting.",
    });
  });
  it("never bypasses server safety gates (DO_NOT_POST / blur)", () => {
    const blocked = {
      ...base,
      publishing_allowed: false,
      publishing_reasons: ["DO_NOT_POST requires verified blur unlock before publishing."],
    };
    const gate = canPublishNowFromReadiness(blocked, "cap1", connected);
    expect(gate.ok).toBe(false);
    expect(gate.reason).toContain("DO_NOT_POST");
  });
  it("is disabled when Instagram is disconnected", () => {
    const gate = canPublishNowFromReadiness(base, "cap1", {
      ok: false,
      loading: false,
      reason: "Instagram disconnected — reconnect to post.",
    });
    expect(gate).toEqual({ ok: false, reason: "Instagram disconnected — reconnect to post." });
  });
  it("blocks already published and in-flight states", () => {
    expect(canPublishNowFromReadiness({ ...base, published_at: "x" }, "c", connected).ok).toBe(false);
    expect(canPublishNowFromReadiness({ ...base, queue_item_id: "q", queue_state: "PUBLISHING" }, "c", connected).ok).toBe(
      false,
    );
    expect(canPublishNowFromReadiness({ ...base, queue_item_id: "q", queue_state: "FAILED" }, "c", connected).ok).toBe(true);
  });
});

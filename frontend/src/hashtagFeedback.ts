export function normalizeHashtag(raw: string): string {
  return raw.trim().replace(/^#/, "").toLowerCase();
}

export function countNewHashtags(existing: readonly string[], incoming: readonly string[]): number {
  const seen = new Set(existing.map(normalizeHashtag));
  let added = 0;
  for (const raw of incoming) {
    const name = normalizeHashtag(raw);
    if (!name || seen.has(name)) continue;
    seen.add(name);
    added += 1;
  }
  return added;
}

export function hashtagSuccessMessage(added: number): string {
  return added > 0 ? `Added ${added} hashtag${added === 1 ? "" : "s"}` : "No new suggestions";
}

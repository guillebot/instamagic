import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PendingButtonLabel } from "../actionButton";
import { ApiError, api } from "../api";
import type { MusicAutorefill, MusicLibrary, MusicSourceResult, MusicSourceSearch, MusicTrack } from "../types";

const ACCEPT = ".mp3,.m4a,.aac,.wav,.ogg,.flac,audio/*";
const MAX_BYTES = 40 * 1024 * 1024;

function formatDuration(seconds: number): string {
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

const MOODS = ["ambient", "lofi", "chill electronic", "cinematic piano", "downtempo", "indie electronic"];

export function LicenseBadge({ label, license, url }: { label?: string | null; license?: string | null; url?: string | null }) {
  if (!license) return <span className="badge">Own upload</span>;
  const text = label || license.toUpperCase();
  const cls = license === "cc0" ? "badge badge-ok" : "badge badge-info";
  return url ? (
    <a className={cls} href={url} target="_blank" rel="noreferrer noopener" title="Licence deed">
      {text}
    </a>
  ) : (
    <span className={cls}>{text}</span>
  );
}

function FindTracks({ onImported }: { onImported: () => void }) {
  const [query, setQuery] = useState("ambient");
  const [minS, setMinS] = useState(30);
  const [maxS, setMaxS] = useState(300);
  const [submitted, setSubmitted] = useState<{ q: string; min: number; max: number } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const results = useQuery({
    queryKey: ["music-sources", submitted],
    enabled: submitted !== null,
    retry: false,
    queryFn: () =>
      api<MusicSourceSearch>(
        `/api/music/sources/search?q=${encodeURIComponent(submitted!.q)}&min_s=${submitted!.min}&max_s=${submitted!.max}`,
      ),
  });
  const importer = useMutation({
    mutationFn: (row: MusicSourceResult) =>
      api<MusicTrack>("/api/music/sources/import", {
        method: "POST",
        body: JSON.stringify({ source: row.source, id: row.id }),
      }),
    onSuccess: (track) => {
      setFailure(null);
      setNotice(`Imported ${track.title} (${track.license_label ?? "licensed"}).`);
      onImported();
      void results.refetch();
    },
    onError: (err) => {
      setNotice(null);
      setFailure(err instanceof ApiError ? err.message : "Import failed.");
    },
  });
  const run = (q: string) => {
    const clean = q.trim();
    if (!clean) return;
    setQuery(clean);
    setSubmitted({ q: clean, min: Math.max(20, minS), max: Math.min(300, Math.max(minS, maxS)) });
  };
  return (
    <section className="card">
      <h2>Find royalty-free tracks</h2>
      <p className="help">
        Searches Openverse (Jamendo, Freesound, …) for music licensed <strong>CC0</strong> or <strong>CC BY</strong>{" "}
        only — commercial use allowed. NonCommercial, NoDerivatives and ShareAlike tracks are never shown. CC BY tracks
        add a credit line to the Reel caption automatically.
      </p>
      <form
        className="row"
        onSubmit={(event) => {
          event.preventDefault();
          run(query);
        }}
      >
        <input aria-label="Mood or genre" value={query} maxLength={120} onChange={(e) => setQuery(e.target.value)} />
        <label>
          Min s{" "}
          <input aria-label="Minimum seconds" type="number" min={20} max={300} value={minS} onChange={(e) => setMinS(Number(e.target.value))} />
        </label>
        <label>
          Max s{" "}
          <input aria-label="Maximum seconds" type="number" min={20} max={300} value={maxS} onChange={(e) => setMaxS(Number(e.target.value))} />
        </label>
        <button className="button" type="submit" disabled={results.isFetching}>
          <PendingButtonLabel pending={results.isFetching} pendingLabel="Searching…" label="Search" />
        </button>
      </form>
      <div className="row">
        {MOODS.map((mood) => (
          <button key={mood} type="button" className="ghost" onClick={() => run(mood)}>
            {mood}
          </button>
        ))}
      </div>
      {results.isError ? (
        <p className="error">{results.error instanceof ApiError ? results.error.message : "Search failed."}</p>
      ) : null}
      {notice ? <p className="muted">{notice}</p> : null}
      {failure ? <p className="error">{failure}</p> : null}
      {results.data && results.data.results.length === 0 ? <p className="muted">No matching tracks.</p> : null}
      <ul className="music-tracks">
        {(results.data?.results ?? []).map((row) => (
          <li key={row.id} className="music-track">
            <div>
              <strong>{row.title}</strong> <span className="muted">— {row.artist}</span>{" "}
              <LicenseBadge label={row.license_label} license={row.license} url={row.license_url} />{" "}
              {row.vocals ? <span className="badge badge-warn">vocals</span> : null}
              {row.instrumental ? <span className="badge">instrumental</span> : null}{" "}
              <span className="muted">
                {formatDuration(row.duration_s)}
                {row.genres.length ? ` · ${row.genres.slice(0, 3).join(", ")}` : ""} ·{" "}
                <a href={row.source_url} target="_blank" rel="noreferrer noopener">
                  source
                </a>
              </span>
            </div>
            <audio controls preload="none" src={row.preview_url} aria-label={`Preview ${row.title}`} />
            <button
              className="button"
              type="button"
              disabled={row.imported || importer.isPending}
              onClick={() => importer.mutate(row)}
            >
              {row.imported ? "Imported" : "Import"}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function AutoRefill() {
  const queryClient = useQueryClient();
  const view = useQuery({ queryKey: ["music-autorefill"], queryFn: () => api<MusicAutorefill>("/api/music/autorefill") });
  const [draft, setDraft] = useState<{ enabled: boolean; target: number; queries: string } | null>(null);
  const loaded = view.data && Array.isArray(view.data.queries) ? view.data : null;
  const current =
    draft ?? (loaded ? { enabled: Boolean(loaded.enabled), target: loaded.target ?? 15, queries: loaded.queries.join(", ") } : null);
  const save = useMutation({
    mutationFn: (next: { enabled: boolean; target: number; queries: string }) =>
      api<MusicAutorefill>("/api/music/autorefill", {
        method: "PUT",
        body: JSON.stringify({
          enabled: next.enabled,
          target: next.target,
          queries: next.queries.split(",").map((q) => q.trim()).filter(Boolean),
        }),
      }),
    onSuccess: () => {
      setDraft(null);
      void queryClient.invalidateQueries({ queryKey: ["music-autorefill"] });
    },
  });
  if (!current) return null;
  return (
    <section className="card">
      <h2>Auto-refill</h2>
      <p className="help">
        Weekly, import up to 5 new CC0 / CC BY instrumental tracks from these searches until the library has this many
        enabled tracks. Off by default.
      </p>
      <div className="row">
        <label>
          <input
            type="checkbox"
            checked={current.enabled}
            onChange={(e) => setDraft({ ...current, enabled: e.target.checked })}
          />{" "}
          Enabled
        </label>
        <label>
          Keep{" "}
          <input
            aria-label="Target tracks"
            type="number"
            min={1}
            max={100}
            value={current.target}
            onChange={(e) => setDraft({ ...current, target: Number(e.target.value) })}
          />{" "}
          tracks
        </label>
      </div>
      <label>
        Searches (comma separated){" "}
        <input
          aria-label="Auto-refill searches"
          value={current.queries}
          onChange={(e) => setDraft({ ...current, queries: e.target.value })}
        />
      </label>
      <button className="button" type="button" disabled={!draft || save.isPending} onClick={() => draft && save.mutate(draft)}>
        Save
      </button>
      {view.data?.last_run_at ? <p className="muted">Last run {new Date(view.data.last_run_at).toLocaleString()}</p> : null}
      {save.isError ? <p className="error">{save.error instanceof ApiError ? save.error.message : "Save failed."}</p> : null}
    </section>
  );
}

export function MusicPage() {
  const queryClient = useQueryClient();
  const library = useQuery({ queryKey: ["music"], queryFn: () => api<MusicLibrary>("/api/music") });
  const fileInput = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["music"] });

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const done: string[] = [];
      for (const file of files) {
        if (file.size > MAX_BYTES) throw new ApiError(413, `${file.name} is larger than 40 MB.`);
        const track = await api<MusicTrack>(`/api/music?filename=${encodeURIComponent(file.name)}`, {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file,
        });
        done.push(track.title);
      }
      return done;
    },
    onSuccess: (titles) => {
      setError(null);
      setMessage(`Added ${titles.join(", ")}.`);
      if (fileInput.current) fileInput.current.value = "";
      refresh();
    },
    onError: (err) => {
      setMessage(null);
      setError(err instanceof ApiError ? err.message : "Upload failed.");
      refresh();
    },
  });

  const toggle = useMutation({
    mutationFn: (track: MusicTrack) =>
      api<MusicTrack>(`/api/music/${track.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !track.enabled }) }),
    onSuccess: refresh,
  });

  const remove = useMutation({
    mutationFn: (track: MusicTrack) => api(`/api/music/${track.id}`, { method: "DELETE" }),
    onSuccess: refresh,
    onError: (err) => setError(err instanceof ApiError ? err.message : "Could not delete the track."),
  });

  const tracks = library.data?.tracks ?? [];
  return (
    <main className="page">
      <h1>Music library</h1>
      <p className="help">
        {library.data?.license_note ??
          "Only upload music you have the rights to use (royalty-free or properly licensed)."}{" "}
        Tracks are baked into each Reel video (looped or trimmed to length, faded in/out, loudness normalised). Pick mode
        (rotation / random) is under <Link to="/queue/schedule">Schedule → Reel schedule</Link>; each Reel can pick its
        own track in the <Link to="/queue">Queue</Link>.
      </p>
      {library.data && library.data.enabled_count === 0 ? (
        <p className="error">No enabled tracks: Reels render silent (with a warning badge) until you add one.</p>
      ) : null}
      <FindTracks onImported={refresh} />
      <section className="card">
        <h2>Upload</h2>
        <div className="row">
          <input ref={fileInput} type="file" accept={ACCEPT} multiple aria-label="Music files" />
          <button
            className="button"
            type="button"
            disabled={upload.isPending}
            onClick={() => {
              const files = Array.from(fileInput.current?.files ?? []);
              if (files.length) upload.mutate(files);
            }}
          >
            <PendingButtonLabel pending={upload.isPending} pendingLabel="Uploading…" label="Upload" />
          </button>
        </div>
        <p className="muted">mp3, m4a, aac, wav, ogg or flac · up to 40 MB · 3 s to 20 min · LAN only.</p>
        {message ? <p className="muted">{message}</p> : null}
        {error ? <p className="error">{error}</p> : null}
      </section>
      <section className="card">
        <h2>Tracks ({tracks.length})</h2>
        {library.isLoading ? <p className="muted">Loading…</p> : null}
        {!library.isLoading && tracks.length === 0 ? <p className="muted">No tracks yet.</p> : null}
        <ul className="music-tracks">
          {tracks.map((track) => (
            <li key={track.id} className="music-track">
              <div>
                <strong>{track.title}</strong>
                {track.artist ? <span className="muted"> — {track.artist}</span> : null}{" "}
                <LicenseBadge label={track.license_label} license={track.license} url={track.license_url} />{" "}
                {track.source_url ? (
                  <a className="muted" href={track.source_url} target="_blank" rel="noreferrer noopener">
                    source
                  </a>
                ) : null}{" "}
                <span className="muted">
                  {formatDuration(track.duration_s)} · {(track.size_bytes / 1024 / 1024).toFixed(1)} MB
                  {track.last_used_at ? ` · last used ${new Date(track.last_used_at).toLocaleString()}` : " · not used yet"}
                </span>
              </div>
              {track.license === "by" && track.attribution ? <p className="muted">{track.attribution}</p> : null}
              <audio controls preload="none" src={track.preview_url} />
              <label>
                <input
                  type="checkbox"
                  checked={track.enabled}
                  disabled={toggle.isPending}
                  onChange={() => toggle.mutate(track)}
                />{" "}
                Enabled
              </label>
              <button
                className="ghost"
                type="button"
                disabled={remove.isPending}
                onClick={() => {
                  if (window.confirm(`Delete "${track.title}"? Reels that chose it will go back to Auto.`)) {
                    remove.mutate(track);
                  }
                }}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      </section>
      <AutoRefill />
    </main>
  );
}

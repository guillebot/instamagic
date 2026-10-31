import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PendingButtonLabel } from "../actionButton";
import { ApiError, api } from "../api";
import type { MusicLibrary, MusicTrack } from "../types";

const ACCEPT = ".mp3,.m4a,.aac,.wav,.ogg,.flac,audio/*";
const MAX_BYTES = 40 * 1024 * 1024;

function formatDuration(seconds: number): string {
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
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
                <strong>{track.title}</strong>{" "}
                <span className="muted">
                  {formatDuration(track.duration_s)} · {(track.size_bytes / 1024 / 1024).toFixed(1)} MB
                  {track.last_used_at ? ` · last used ${new Date(track.last_used_at).toLocaleString()}` : " · not used yet"}
                </span>
              </div>
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
    </main>
  );
}

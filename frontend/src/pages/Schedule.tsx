import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { PostingScheduleConfig, QueueItem } from "../types";

export function SchedulePage() {
  const queryClient = useQueryClient();
  const config = useQuery({
    queryKey: ["posting-schedule-config"],
    queryFn: () => api<PostingScheduleConfig>("/api/posting-schedule/config"),
  });
  const upcoming = useQuery({
    queryKey: ["posting-schedule-upcoming"],
    queryFn: () => api<QueueItem[]>("/api/posting-schedule/upcoming"),
    refetchInterval: 30_000,
  });
  const [timezone, setTimezone] = useState("");
  const [maxPosts, setMaxPosts] = useState(2);
  const [minGap, setMinGap] = useState(4);
  const [maxStories, setMaxStories] = useState(2);
  const [storyDefault, setStoryDefault] = useState(false);
  const [blurCoverageThreshold, setBlurCoverageThreshold] = useState(95);
  const [blurRegionPadding, setBlurRegionPadding] = useState(6);
  const [detectorOverrules, setDetectorOverrules] = useState(true);

  const saveConfig = useMutation({
    mutationFn: () =>
      api<PostingScheduleConfig>("/api/posting-schedule/config", {
        method: "PUT",
        body: JSON.stringify({
          timezone: timezone || config.data?.timezone,
          max_posts_per_day: maxPosts,
          max_stories_per_day: maxStories,
          min_gap_hours: minGap,
          weekday_slots: config.data?.weekday_slots,
          paused: config.data?.paused,
          story_share_default: storyDefault,
          blur_coverage_threshold: blurCoverageThreshold,
          blur_region_padding_px: blurRegionPadding,
          blur_detector_overrules_coverage: detectorOverrules,
        }),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["posting-schedule-config"] }),
  });

  const togglePause = useMutation({
    mutationFn: (paused: boolean) =>
      api<PostingScheduleConfig>("/api/posting-schedule/config", {
        method: "PUT",
        body: JSON.stringify({ paused }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["posting-schedule-config"] });
      void queryClient.invalidateQueries({ queryKey: ["posting-schedule-upcoming"] });
    },
  });

  const fillSlots = useMutation({
    mutationFn: () => api<{ assigned: number }>("/api/posting-schedule/fill-slots", { method: "POST" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["posting-schedule-upcoming"] }),
  });

  useEffect(() => {
    if (!config.data) return;
    setTimezone(config.data.timezone);
    setMaxPosts(config.data.max_posts_per_day);
    setMinGap(config.data.min_gap_hours);
    setMaxStories(config.data.max_stories_per_day ?? 2);
    setStoryDefault(config.data.story_share_default ?? false);
    setBlurCoverageThreshold(config.data.blur_coverage_threshold ?? 95);
    setBlurRegionPadding(config.data.blur_region_padding_px ?? 6);
    setDetectorOverrules(config.data.blur_detector_overrules_coverage ?? true);
  }, [config.data]);

  const paused = config.data?.paused ?? false;

  return (
    <main className="page">
      <h1>Posting schedule</h1>
      <p>
        Human-approved queue items only. Scheduling does not bypass safety gates — publish re-validates attestation,
        rating, and DO_NOT_POST. <Link to="/queue">Queue</Link>
      </p>
      {paused ? (
        <p className="error banner-pause">
          <strong>Schedule paused</strong> — no automatic publishes until you resume.
        </p>
      ) : null}
      <section className="card schedule-config">
        <h2>Calendar settings</h2>
        <div className="row">
          <label>
            Timezone
            <input value={timezone} onChange={(e) => setTimezone(e.target.value)} />
          </label>
          <label>
            Max posts / day
            <input
              type="number"
              min={1}
              max={24}
              value={maxPosts}
              onChange={(e) => setMaxPosts(Number(e.target.value))}
            />
          </label>
          <label title="Standalone Stories use the same slots but have their own daily cap (they don't count against feed posts).">
            Max stories / day
            <input
              type="number"
              min={1}
              max={10}
              value={maxStories}
              onChange={(e) => setMaxStories(Number(e.target.value))}
            />
          </label>
          <label>
            Min gap (hours)
            <input
              type="number"
              min={0.5}
              step={0.5}
              value={minGap}
              onChange={(e) => setMinGap(Number(e.target.value))}
            />
          </label>
        </div>
        <div className="row">
          <label>
            <input
              type="checkbox"
              checked={storyDefault}
              onChange={(e) => setStoryDefault(e.target.checked)}
            />
            Story default (also share new queue items to Story)
          </label>
        </div>
        <section className="blur-gate-settings">
          <h3>Blur unlock gates</h3>
          <p className="help">
            Each flagged area on the original must be at least the coverage threshold under the blur (grown by
            padding). The re-check on the blurred image is always required when approving covers.
          </p>
          <div className="row">
            <label>
              Coverage threshold (%)
              <input
                type="number"
                min={60}
                max={100}
                value={blurCoverageThreshold}
                onChange={(e) => setBlurCoverageThreshold(Number(e.target.value))}
              />
            </label>
            <label>
              Flagged-area padding (px)
              <input
                type="number"
                min={0}
                max={20}
                value={blurRegionPadding}
                onChange={(e) => setBlurRegionPadding(Number(e.target.value))}
              />
            </label>
            <label title="When the detector re-check on the blurred render finds nothing, the blur is verified even if coverage is below the threshold (shown as a warning). A detector hit always blocks.">
              <input
                type="checkbox"
                checked={detectorOverrules}
                onChange={(e) => setDetectorOverrules(e.target.checked)}
              />{" "}
              Detector pass overrules coverage
            </label>
          </div>
        </section>
        <div className="row">
          <button className="button" type="button" onClick={() => saveConfig.mutate()} disabled={saveConfig.isPending}>
            Save settings
          </button>
          <button
            className={paused ? "button" : "ghost"}
            type="button"
            onClick={() => togglePause.mutate(!paused)}
            disabled={togglePause.isPending}
          >
            {paused ? "Resume schedule" : "Pause all scheduled posts"}
          </button>
          <button className="ghost" type="button" onClick={() => fillSlots.mutate()} disabled={fillSlots.isPending}>
            Fill slots from approved queue
          </button>
        </div>
      </section>
      <section>
        <h2>Upcoming</h2>
        <ul className="schedule-list">
          {upcoming.data?.map((item) => (
            <ScheduleRow key={item.id} item={item} onChanged={() => void upcoming.refetch()} />
          ))}
          {upcoming.data?.length === 0 ? <p className="muted">No scheduled posts.</p> : null}
        </ul>
      </section>
    </main>
  );
}

function ScheduleRow({ item, onChanged }: { item: QueueItem; onChanged: () => void }) {
  const [when, setWhen] = useState(item.scheduled_at?.slice(0, 16) ?? "");

  async function reschedule() {
    if (!when) return;
    await api(`/api/queue/${item.id}/schedule`, {
      method: "POST",
      body: JSON.stringify({ mode: "explicit", scheduled_at: new Date(when).toISOString() }),
    });
    onChanged();
  }

  async function unschedule() {
    await api(`/api/queue/${item.id}/unschedule`, { method: "POST" });
    onChanged();
  }

  async function publishNow() {
    if (!item.caption_id) {
      window.alert("Select a caption on the Queue page first.");
      return;
    }
    if (!window.confirm("Publish now (bypass schedule time)?")) return;
    await api(`/api/queue/${item.id}/publish`, {
      method: "POST",
      body: JSON.stringify({ caption_id: item.caption_id }),
    });
    onChanged();
  }

  return (
    <li className="card schedule-row">
      {item.image.thumbnail_url ? <img src={item.image.thumbnail_url} alt="" /> : <span />}
      <div>
        <strong>{item.image.filename}</strong>
        <p className="muted">{item.scheduled_at ? new Date(item.scheduled_at).toLocaleString() : "—"}</p>
        <div className="row">
          <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
          <button className="ghost" type="button" onClick={() => void reschedule()}>
            Set time
          </button>
          <button className="ghost" type="button" onClick={() => void unschedule()}>
            Unschedule
          </button>
          <button className="button" type="button" onClick={() => void publishNow()}>
            Publish now
          </button>
        </div>
      </div>
    </li>
  );
}

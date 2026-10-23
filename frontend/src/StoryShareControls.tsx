import type { QueueItem } from "./types";

export type StoryShareValue = {
  story_share_enabled: boolean;
  story_render_mode: string;
  story_border_color: string;
  story_timing_mode: string;
  story_delay_hours: number;
};

export const STORY_RENDER_MODES: { value: string; label: string }[] = [
  { value: "blur_backdrop", label: "Fit with blurred backdrop" },
  { value: "crop_fill", label: "Crop to fill 9:16" },
  { value: "fit_border", label: "Fit on solid border" },
];

export function defaultStoryShareValue(partial?: Partial<StoryShareValue>): StoryShareValue {
  return {
    story_share_enabled: partial?.story_share_enabled ?? false,
    story_render_mode: partial?.story_render_mode ?? "blur_backdrop",
    story_border_color: partial?.story_border_color ?? "black",
    story_timing_mode: partial?.story_timing_mode ?? "with_post",
    story_delay_hours: partial?.story_delay_hours ?? 3,
  };
}

export function storyShareFromQueueItem(item: QueueItem): StoryShareValue {
  return defaultStoryShareValue({
    story_share_enabled: item.story_share_enabled,
    story_render_mode: item.story_render_mode,
    story_border_color: item.story_border_color,
    story_timing_mode: item.story_timing_mode,
    story_delay_hours: item.story_delay_hours ?? 3,
  });
}

export function storyPreviewUrl(imageId: string, value: StoryShareValue): string {
  const params = new URLSearchParams({
    render_mode: value.story_render_mode,
    border_color: value.story_border_color,
  });
  return `/api/images/${imageId}/story-preview?${params.toString()}`;
}

export function formatStoryStatus(item: QueueItem): string | null {
  if (!item.story_share_enabled || item.media_type === "CAROUSEL") return null;
  const status = item.story_status ?? "off";
  if (status === "published") return "Story: published";
  if (status === "failed") return `Story: failed — ${item.story_last_error ?? "unknown error"}`;
  if (status === "scheduled" && item.story_scheduled_at) {
    const when = new Date(item.story_scheduled_at).toLocaleString();
    return `Story: scheduled ${when}`;
  }
  if (status === "pending") return "Story: pending publish";
  if (status === "enabled") return "Story: will publish with feed";
  return null;
}

type Props = {
  value: StoryShareValue;
  onChange: (next: StoryShareValue) => void;
  disabled?: boolean;
  compact?: boolean;
};

export function StoryShareControls({ value, onChange, disabled, compact }: Props) {
  const patch = (partial: Partial<StoryShareValue>) => onChange({ ...value, ...partial });

  return (
    <fieldset className={`story-share-controls${compact ? " compact" : ""}`} disabled={disabled}>
      <label className="story-share-toggle">
        <input
          type="checkbox"
          checked={value.story_share_enabled}
          onChange={(e) => patch({ story_share_enabled: e.target.checked })}
        />
        Also share to Story
      </label>
      <p className="help muted">
        API stories cannot include link, mention, or &quot;new post&quot; stickers.
      </p>
      {value.story_share_enabled ? (
        <>
          <label>
            Story layout
            <select
              value={value.story_render_mode}
              onChange={(e) => patch({ story_render_mode: e.target.value })}
            >
              {STORY_RENDER_MODES.map((mode) => (
                <option key={mode.value} value={mode.value}>
                  {mode.label}
                </option>
              ))}
            </select>
          </label>
          {value.story_render_mode === "fit_border" ? (
            <label>
              Border
              <select
                value={value.story_border_color}
                onChange={(e) => patch({ story_border_color: e.target.value })}
              >
                <option value="black">Black</option>
                <option value="white">White</option>
              </select>
            </label>
          ) : null}
          <label>
            Story timing
            <select
              value={value.story_timing_mode}
              onChange={(e) => patch({ story_timing_mode: e.target.value })}
            >
              <option value="with_post">With the post</option>
              <option value="delay_hours">Delay (hours)</option>
            </select>
          </label>
          {value.story_timing_mode === "delay_hours" ? (
            <label>
              Delay (1–12 h)
              <input
                type="number"
                min={1}
                max={12}
                value={value.story_delay_hours}
                onChange={(e) => patch({ story_delay_hours: Number(e.target.value) })}
              />
            </label>
          ) : null}
        </>
      ) : null}
    </fieldset>
  );
}

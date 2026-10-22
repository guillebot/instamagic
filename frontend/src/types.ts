export type SafetyState =
  | "SAFE"
  | "PUBLISHABLE"
  | "SAFE_AFTER_CROP"
  | "SAFE_AFTER_BLUR"
  | "REVIEW"
  | "REJECTED"
  | "DO_NOT_POST"
  | "UNREVIEWED";

export type ImageItem = {
  id: string;
  filename: string;
  relative_path: string;
  width: number | null;
  height: number | null;
  orientation: string | null;
  captured_at: string | null;
  camera: string | null;
  session_name: string | null;
  model_state: string | null;
  human_rating: string | null;
  effective_state: SafetyState | string;
  aesthetic_score: number | null;
  queued: boolean;
  queue_state: string | null;
  duplicate_count: number;
  has_gps: boolean;
  has_approved_crop?: boolean;
  has_approved_covering_blur?: boolean;
  thumbnail_url: string | null;
  preview_url: string | null;
};

export type CropItem = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  aspect_ratio: string;
  reason: string;
  contains_face: boolean;
  excludes_unsafe: boolean;
  approved: boolean;
  preview_url: string | null;
};

export type BlurItem = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  shape: string;
  feather: number;
  radius: number;
  strength: string;
  reason: string;
  covers_unsafe: boolean;
  approved: boolean;
  preview_url: string | null;
};

export type CaptionItem = {
  id: string;
  style: string;
  language: string;
  text: string;
  status: string;
};

export type JobItem = {
  id: string;
  kind: string;
  status: string;
  detail?: string | null;
  error?: string | null;
  library_id?: string | null;
  files_seen?: number;
  files_processed?: number;
  files_skipped?: number;
  files_failed?: number;
  files_total?: number | null;
  current_path?: string | null;
  current_stage?: string | null;
  classified_count?: number;
  pending_count?: number;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

export type FolderEntry = {
  name: string;
  path: string;
  image_count: number;
  thumbnail_url: string | null;
};

export type FolderListing = {
  path: string;
  parent: string | null;
  breadcrumbs: { name: string; path: string }[];
  folders: FolderEntry[];
  image_count: number;
  source: "db" | "filesystem" | string;
};

export type QueueJobItem = {
  id: string;
  func_name?: string | null;
  library_id?: string | null;
  job_id?: string | null;
  status: string;
};

export type QueueItemStatus = {
  name: string;
  queued: number;
  started: number;
  failed: number;
  workers: number;
  jobs?: QueueJobItem[];
  available: boolean;
  detail?: string | null;
};

export type QueuesStatus = {
  available: boolean;
  detail?: string | null;
  queues: QueueItemStatus[];
};

export type LibraryItem = {
  id: string;
  name: string;
  root_path: string;
  attested: boolean;
  image_count: number;
};

export type CandidateItem = {
  image: ImageItem;
  score: number;
  reasons: string[];
  held_back: number;
  crop_aspect: string | null;
  model_state: string | null;
};

export type CurationPick = {
  id: string;
  batch_id: string;
  sort_order: number;
  gemini_rank: number;
  local_score: number;
  reasons: string[];
  suggested_tags: string[];
  status: string;
  preferred_caption_id: string | null;
  image: ImageItem;
  captions: CaptionItem[];
};

export type CurationBatchSummary = {
  id: string;
  library_id: string | null;
  folder: string | null;
  status: string;
  limit: number;
  detail: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  pick_count: number;
};

export type CurationBatch = CurationBatchSummary & {
  picks: CurationPick[];
};

export type QueueItem = {
  id: string;
  state: string;
  crop_id?: string | null;
  caption_id?: string | null;
  published_at?: string | null;
  last_error?: string | null;
  image: ImageItem;
};

export type RelatedAccountItem = {
  id: string;
  handle: string;
  display_name: string | null;
  profile_url: string | null;
  notes: string | null;
  why_relevant: string | null;
};

export type CommentDraftItem = {
  id: string;
  image_id: string | null;
  related_account_id: string | null;
  target_context: string;
  text: string;
  status: string;
};

export type AppConfig = {
  version?: string;
  gemini_policy: string;
  /** True when GEMINI_IMAGE_POLICY ceiling allows derivative preview bytes. */
  gemini_pixels_allowed?: boolean;
  gemini_configured: boolean;
  auto_publish: boolean;
  instagram_configured?: boolean;
  instagram_provider?: string;
  account_voice?: string;
  detector_available: boolean;
  suggested_library_root: string;
  attestation_statement: string;
  safety_classifier: string;
};

export type InstagramAccountDetails = {
  id: string;
  username: string | null;
  name: string | null;
  profile_picture_url: string | null;
  followers_count: number | null;
  follows_count: number | null;
  media_count: number | null;
  account_type: string | null;
  biography: string | null;
  website: string | null;
  linked_facebook_page_id: string | null;
};

export type InstagramConnectionStatus = {
  configured: boolean;
  provider: string;
  credential_source?: string;
  auto_publish: boolean;
  public_base_url?: string;
  graph_version?: string;
  token_configured: boolean;
  token_masked: string | null;
  business_account_id: string | null;
  business_account_id_configured: boolean;
  oauth_available?: boolean;
  oauth_redirect_uri?: string;
  oauth_scopes?: string;
  meta_docs_url?: string;
  meta_apps_dashboard_url?: string;
  meta_create_app_docs_url?: string;
  meta_app_id?: string | null;
  meta_app_secret_configured?: boolean;
  meta_app_source?: string;
  can_edit_meta_app?: boolean;
  account?: InstagramAccountDetails | null;
  account_fetched_at?: string | null;
  can_edit_database_credentials?: boolean;
  env_overrides_database?: boolean;
};

export type InstagramRefreshResult = InstagramConnectionStatus & {
  ok: boolean;
  error: string | null;
  account: InstagramAccountDetails | null;
};

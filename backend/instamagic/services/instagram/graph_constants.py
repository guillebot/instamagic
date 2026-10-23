"""Shared Meta Graph API defaults for Instagram Content Publishing."""

DEFAULT_INSTAGRAM_GRAPH_VERSION = "v25.0"

# Facebook Login for Business → Page-linked IG professional account.
OAUTH_SCOPES = (
    "instagram_basic,instagram_content_publish,instagram_manage_insights,"
    "pages_show_list,pages_read_engagement,business_management"
)

# Permissions the Login for Business configuration must grant (documented in README).
REQUIRED_GRAPH_PERMISSIONS: tuple[str, ...] = (
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_insights",
    "pages_show_list",
    "pages_read_engagement",
    "business_management",
)

INSIGHTS_PERMISSION = "instagram_manage_insights"
COMMENTS_PERMISSION = "instagram_manage_comments"

# Instagram feed image (Graph Content Publishing).
IG_FEED_MIN_ASPECT = 4 / 5  # 0.8 (4:5 portrait)
IG_FEED_MAX_ASPECT = 1.91
IG_FEED_MAX_BYTES = 8 * 1024 * 1024
IG_FEED_LONG_EDGE = 1440
IG_FEED_JPEG_QUALITY = 85

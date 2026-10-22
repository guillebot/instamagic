import { FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import type { InstagramAccountDetails, InstagramConnectionStatus, InstagramRefreshResult } from "../types";

const DEFAULT_META_DOCS =
  "https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login";
const DEFAULT_META_APPS = "https://developers.facebook.com/apps/";
const DEFAULT_CREATE_DOCS =
  "https://developers.facebook.com/docs/instagram-platform/create-an-instagram-app";
const STAGE_REDIRECT = "https://instamagic.schimmel.homes/api/instagram/oauth/callback";

export function InstagramPage() {
  const queryClient = useQueryClient();
  const [tokenInput, setTokenInput] = useState("");
  const [accountIdInput, setAccountIdInput] = useState("");
  const [metaAppIdInput, setMetaAppIdInput] = useState("");
  const [metaAppSecretInput, setMetaAppSecretInput] = useState("");
  const [banner, setBanner] = useState<string | null>(null);
  const [bannerKind, setBannerKind] = useState<"ok" | "error">("ok");

  const oauthParams = useMemo(() => new URLSearchParams(window.location.search), []);

  const status = useQuery({
    queryKey: ["instagram-status"],
    queryFn: () => api<InstagramConnectionStatus>("/api/instagram/status"),
  });

  useEffect(() => {
    const oauth = oauthParams.get("oauth");
    if (!oauth) return;
    if (oauth === "ok") {
      setBannerKind("ok");
      setBanner("Connected via Meta — credentials sealed and account fetched.");
      void queryClient.invalidateQueries({ queryKey: ["instagram-status"] });
    } else {
      setBannerKind("error");
      setBanner(oauthParams.get("detail") || "Meta OAuth failed.");
    }
    window.history.replaceState({}, "", "/instagram");
  }, [oauthParams, queryClient]);

  const applyStatus = async (body: InstagramRefreshResult) => {
    await queryClient.setQueryData(["instagram-status"], {
      configured: body.configured,
      provider: body.provider,
      credential_source: body.credential_source,
      auto_publish: body.auto_publish,
      public_base_url: body.public_base_url,
      graph_version: body.graph_version,
      token_configured: body.token_configured,
      token_masked: body.token_masked,
      business_account_id: body.business_account_id,
      business_account_id_configured: body.business_account_id_configured,
      oauth_available: body.oauth_available,
      oauth_redirect_uri: body.oauth_redirect_uri,
      oauth_scopes: body.oauth_scopes,
      meta_docs_url: body.meta_docs_url,
      meta_apps_dashboard_url: body.meta_apps_dashboard_url,
      meta_create_app_docs_url: body.meta_create_app_docs_url,
      meta_app_id: body.meta_app_id,
      meta_app_secret_configured: body.meta_app_secret_configured,
      meta_app_source: body.meta_app_source,
      can_edit_meta_app: body.can_edit_meta_app,
      account: body.account,
      account_fetched_at: body.account_fetched_at,
      can_edit_database_credentials: body.can_edit_database_credentials,
      env_overrides_database: body.env_overrides_database,
    } satisfies InstagramConnectionStatus);
  };

  const refresh = useMutation({
    mutationFn: () => api<InstagramRefreshResult>("/api/instagram/account", { method: "POST" }),
    onSuccess: async (body) => {
      await applyStatus(body);
      if (body.ok) {
        setBannerKind("ok");
        setBanner("Connection ok — account details saved.");
      } else {
        setBannerKind("error");
        setBanner(body.error || "Connection test failed.");
      }
    },
    onError: (err) => {
      setBannerKind("error");
      setBanner(err instanceof ApiError ? err.message : "Could not reach Instagram helper.");
    },
  });

  const saveCreds = useMutation({
    mutationFn: () =>
      api<InstagramRefreshResult>("/api/instagram/credentials", {
        method: "POST",
        body: JSON.stringify({
          access_token: tokenInput,
          business_account_id: accountIdInput,
        }),
      }),
    onSuccess: async (body) => {
      setTokenInput("");
      await applyStatus(body);
      if (body.ok) {
        setBannerKind("ok");
        setBanner("Credentials saved (encrypted) and account fetched.");
      } else {
        setBannerKind("error");
        setBanner(body.error || "Saved credentials but Graph fetch failed.");
      }
    },
    onError: (err) => {
      setBannerKind("error");
      setBanner(err instanceof ApiError ? err.message : "Could not save credentials.");
    },
  });

  const clearCreds = useMutation({
    mutationFn: () => api<InstagramRefreshResult>("/api/instagram/credentials", { method: "DELETE" }),
    onSuccess: async (body) => {
      await applyStatus(body);
      setBannerKind("ok");
      setBanner("Database Instagram credentials cleared.");
    },
    onError: (err) => {
      setBannerKind("error");
      setBanner(err instanceof ApiError ? err.message : "Could not clear credentials.");
    },
  });

  const saveMetaApp = useMutation({
    mutationFn: () =>
      api<InstagramRefreshResult>("/api/instagram/meta-app", {
        method: "POST",
        body: JSON.stringify({
          app_id: metaAppIdInput,
          app_secret: metaAppSecretInput,
        }),
      }),
    onSuccess: async (body) => {
      setMetaAppSecretInput("");
      setMetaAppIdInput("");
      await applyStatus(body);
      setBannerKind("ok");
      setBanner("Meta App ID/Secret saved (secret sealed). You can Connect with Meta now.");
    },
    onError: (err) => {
      setBannerKind("error");
      setBanner(err instanceof ApiError ? err.message : "Could not save Meta app credentials.");
    },
  });

  const clearMetaApp = useMutation({
    mutationFn: () => api<InstagramRefreshResult>("/api/instagram/meta-app", { method: "DELETE" }),
    onSuccess: async (body) => {
      await applyStatus(body);
      setBannerKind("ok");
      setBanner("Database Meta App credentials cleared.");
    },
    onError: (err) => {
      setBannerKind("error");
      setBanner(err instanceof ApiError ? err.message : "Could not clear Meta app credentials.");
    },
  });

  const startOauth = useMutation({
    mutationFn: () => api<{ authorize_url: string }>("/api/instagram/oauth/start"),
    onSuccess: (body) => {
      window.location.assign(body.authorize_url);
    },
    onError: (err) => {
      setBannerKind("error");
      setBanner(err instanceof ApiError ? err.message : "Could not start Meta OAuth.");
    },
  });

  const connection = status.data;
  const account = refresh.data?.account ?? saveCreds.data?.account ?? connection?.account ?? null;
  const statusError =
    status.error instanceof ApiError
      ? status.error.message
      : status.isError
        ? "Could not load Instagram status."
        : null;

  const redirectUri =
    connection?.oauth_redirect_uri ||
    `${window.location.origin}/api/instagram/oauth/callback`;
  const scopes =
    connection?.oauth_scopes ||
    "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement";
  const metaDocs = connection?.meta_docs_url || DEFAULT_META_DOCS;
  const metaApps = connection?.meta_apps_dashboard_url || DEFAULT_META_APPS;
  const createDocs = connection?.meta_create_app_docs_url || DEFAULT_CREATE_DOCS;

  const onSave = (event: FormEvent) => {
    event.preventDefault();
    if (!tokenInput.trim() || !accountIdInput.trim()) {
      setBannerKind("error");
      setBanner("Paste both the long-lived access token and the Instagram business account id.");
      return;
    }
    saveCreds.mutate();
  };

  const onSaveMetaApp = (event: FormEvent) => {
    event.preventDefault();
    if (!metaAppIdInput.trim() || !metaAppSecretInput.trim()) {
      setBannerKind("error");
      setBanner("Paste both the Meta App ID and App Secret from the Meta Developer Console.");
      return;
    }
    saveMetaApp.mutate();
  };

  return (
    <main className="page">
      <h1>Instagram connection</h1>
      <p>
        Connect a Professional Instagram account (Business/Creator linked to a Facebook Page) via Meta login.
        Credentials are sealed in the database. This page never shows the full access token or App Secret and
        never publishes.
      </p>

      {banner ? <p className={bannerKind === "ok" ? "ok" : "error"}>{banner}</p> : null}
      {statusError ? <p className="error">{statusError}</p> : null}

      <section className="panel ig-connect-panel">
        <h2>1. Meta Developer App</h2>
        <MetaSetupChecklist
          redirectUri={redirectUri}
          scopes={scopes}
          metaDocs={metaDocs}
          metaApps={metaApps}
          createDocs={createDocs}
        />
        {connection?.can_edit_meta_app !== false ? (
          <form className="form-grid ig-meta-form" onSubmit={onSaveMetaApp}>
            <label>
              Meta App ID
              <input
                value={metaAppIdInput}
                onChange={(e) => setMetaAppIdInput(e.target.value)}
                placeholder={connection?.meta_app_id || "from App Dashboard → Settings → Basic"}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <label>
              Meta App Secret
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={metaAppSecretInput}
                onChange={(e) => setMetaAppSecretInput(e.target.value)}
                placeholder="Show → copy from Settings → Basic (never shown after save)"
              />
            </label>
            <div className="row span-2">
              <button type="submit" className="button" disabled={saveMetaApp.isPending}>
                {saveMetaApp.isPending ? "Saving…" : "Save Meta App ID & Secret"}
              </button>
              {connection?.meta_app_source === "database" ? (
                <button
                  type="button"
                  className="ghost"
                  disabled={clearMetaApp.isPending}
                  onClick={() => {
                    if (window.confirm("Clear sealed Meta App credentials from the database?")) {
                      clearMetaApp.mutate();
                    }
                  }}
                >
                  {clearMetaApp.isPending ? "Clearing…" : "Clear Meta App"}
                </button>
              ) : null}
            </div>
            <p className="muted span-2">
              Sealed with <code>APP_SECRET</code> in Postgres. After save, the secret is never shown again.
              Host <code>META_*</code> env remains an optional fallback (DB wins when both are set).
            </p>
          </form>
        ) : null}
      </section>

      <section className="panel ig-connect-panel">
        <h2>2. Connect with Instagram / Meta</h2>
        {connection?.oauth_available ? (
          <>
            <p className="muted">
              Sign in with Facebook, grant Page + Instagram permissions, and Instamagic exchanges the code for a
              long-lived Page token, discovers the IG business account id, and seals both in Postgres.
            </p>
            <button
              type="button"
              className="button button-lg ig-connect-btn"
              disabled={startOauth.isPending || connection.env_overrides_database}
              onClick={() => startOauth.mutate()}
            >
              {startOauth.isPending ? "Opening Meta…" : "Connect with Instagram / Meta"}
            </button>
            {connection.env_overrides_database ? (
              <p className="muted">
                Host <code>.env</code> already sets Instagram tokens — those win until cleared and the API is
                restarted. OAuth save would be ignored while env is set.
              </p>
            ) : null}
            <p className="muted ig-redirect-hint">
              Redirect URI registered in Meta: <code className="break-all">{redirectUri}</code>
            </p>
          </>
        ) : (
          <p className="error">
            Save Meta App ID and App Secret above (or set <code>META_APP_ID</code> /{" "}
            <code>META_APP_SECRET</code> on the host) before Connect is available.
          </p>
        )}
      </section>

      <section className="panel">
        <h2>Status</h2>
        {status.isLoading ? <p className="muted">Loading…</p> : null}
        {connection ? (
          <dl className="detail-list">
            <div>
              <dt>Configured</dt>
              <dd>{connection.configured ? "Yes" : "No — connect with Meta above"}</dd>
            </div>
            <div>
              <dt>Credential source</dt>
              <dd>{connection.credential_source ?? "none"}</dd>
            </div>
            <div>
              <dt>Provider</dt>
              <dd>{connection.provider}</dd>
            </div>
            <div>
              <dt>Access token</dt>
              <dd>
                {connection.token_configured
                  ? connection.token_masked ?? "set (masked)"
                  : "Not set"}
              </dd>
            </div>
            <div>
              <dt>Business account id</dt>
              <dd>
                {connection.business_account_id_configured
                  ? connection.business_account_id ?? "(fake provider)"
                  : "Not set"}
              </dd>
            </div>
            <div>
              <dt>Meta App ID</dt>
              <dd>{connection.meta_app_id || "Not set"}</dd>
            </div>
            <div>
              <dt>Meta App Secret</dt>
              <dd>
                {connection.meta_app_secret_configured
                  ? `Configured (${connection.meta_app_source ?? "unknown"})`
                  : "Not set"}
              </dd>
            </div>
            <div>
              <dt>Meta OAuth</dt>
              <dd>{connection.oauth_available ? "Ready" : "Not configured"}</dd>
            </div>
            <div>
              <dt>Graph version</dt>
              <dd>{connection.graph_version ?? "—"}</dd>
            </div>
            <div>
              <dt>Public base URL</dt>
              <dd className="break-all">{connection.public_base_url || "—"}</dd>
            </div>
            <div>
              <dt>Auto publish</dt>
              <dd>{connection.auto_publish ? "On (ack required at boot)" : "Off (human-gated)"}</dd>
            </div>
            <div>
              <dt>Profile last fetched</dt>
              <dd>{connection.account_fetched_at ? new Date(connection.account_fetched_at).toLocaleString() : "—"}</dd>
            </div>
          </dl>
        ) : null}

        {!connection?.configured ? (
          <p className="error">
            Instagram is not configured yet. Save the Meta App above, then Connect — or expand Advanced to paste
            a long-lived token.
          </p>
        ) : null}

        <div className="row">
          <button
            type="button"
            className="button"
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
          >
            {refresh.isPending ? "Checking…" : "Test connection / Refresh account details"}
          </button>
          {connection?.credential_source === "database" ? (
            <button
              type="button"
              className="ghost"
              disabled={clearCreds.isPending}
              onClick={() => {
                if (window.confirm("Clear sealed Instagram credentials from the database?")) {
                  clearCreds.mutate();
                }
              }}
            >
              {clearCreds.isPending ? "Clearing…" : "Clear saved credentials"}
            </button>
          ) : null}
        </div>
      </section>

      {account ? <AccountPanel account={account} /> : null}

      {connection?.can_edit_database_credentials !== false ? (
        <details className="panel ig-advanced">
          <summary>Advanced — paste long-lived token manually</summary>
          {connection?.env_overrides_database ? (
            <p className="muted">
              Host <code>.env</code> already sets Instagram tokens — those win until cleared and the API is
              restarted. Database paste is disabled while env is set.
            </p>
          ) : (
            <>
              <p className="muted">
                Prefer <strong>Connect with Instagram / Meta</strong> above. Paste only if you already have a{" "}
                <strong>long-lived Page access token</strong> and the{" "}
                <strong>Instagram business user id</strong> (not the Page id). The token is encrypted at rest
                with <code>APP_SECRET</code> and never returned to the UI in full.
              </p>
              <form className="form-grid" onSubmit={onSave}>
                <label className="span-2">
                  Long-lived access token
                  <input
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    value={tokenInput}
                    onChange={(e) => setTokenInput(e.target.value)}
                    placeholder="EAAB… (never commit this)"
                  />
                </label>
                <label className="span-2">
                  Instagram business account id
                  <input
                    value={accountIdInput}
                    onChange={(e) => setAccountIdInput(e.target.value)}
                    placeholder="1784…"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </label>
                <div className="row span-2">
                  <button type="submit" className="button" disabled={saveCreds.isPending}>
                    {saveCreds.isPending ? "Saving…" : "Save & fetch account"}
                  </button>
                </div>
              </form>
            </>
          )}
        </details>
      ) : null}
    </main>
  );
}

function MetaSetupChecklist({
  redirectUri,
  scopes,
  metaDocs,
  metaApps,
  createDocs,
}: {
  redirectUri: string;
  scopes: string;
  metaDocs: string;
  metaApps: string;
  createDocs: string;
}) {
  const copyRedirect = async () => {
    try {
      await navigator.clipboard.writeText(redirectUri);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="ig-setup">
      <p className="muted">
        Create a free Meta app once (~5 minutes). You do <strong>not</strong> paste Instagram tokens here — only
        the App ID and App Secret from the Meta console, then Connect.
      </p>
      <ol className="howto">
        <li>
          Open{" "}
          <a href={metaApps} target="_blank" rel="noreferrer">
            developers.facebook.com/apps
          </a>{" "}
          and sign in. Click <strong>Create App</strong>.
        </li>
        <li>
          Use case <strong>Other</strong> → app type <strong>Business</strong>. Name it (e.g. Instamagic) and
          add a contact email. Details:{" "}
          <a href={createDocs} target="_blank" rel="noreferrer">
            Create a Meta app
          </a>
          .
        </li>
        <li>
          Add product <strong>Facebook Login</strong> (Facebook Login for Business). Skip Instagram-only
          “business login” embed URLs — Instamagic uses the classic Facebook Login dialog.
        </li>
        <li>
          Facebook Login → <strong>Settings</strong> → <strong>Valid OAuth Redirect URIs</strong>. Paste
          exactly (Strict Mode requires an exact match):
          <br />
          <code className="break-all">{redirectUri}</code>{" "}
          <button type="button" className="ghost" onClick={() => void copyRedirect()}>
            Copy
          </button>
          {redirectUri !== STAGE_REDIRECT ? (
            <span className="muted">
              {" "}
              (stage expected: <code className="break-all">{STAGE_REDIRECT}</code>)
            </span>
          ) : null}
        </li>
        <li>
          Permissions Instamagic requests (Development mode: your admin/tester account is enough; Live needs
          App Review for others): <code>{scopes}</code>. Confirm names against{" "}
          <a href={metaDocs} target="_blank" rel="noreferrer">
            Instagram API with Facebook Login
          </a>
          .
        </li>
        <li>
          Convert the Instagram account to <strong>Professional</strong> and link it to a Facebook Page you
          admin.
        </li>
        <li>
          App Dashboard → <strong>Settings → Basic</strong>: copy <strong>App ID</strong> and{" "}
          <strong>App Secret</strong> (Show), then paste them in the form below and Save. No host{" "}
          <code>.env</code> restart required.
        </li>
      </ol>
      <p className="muted">
        LAN Traefik already proxies <code>/api/*</code> on the same host — Meta redirects the browser back here;
        no public tunnel is required for login (only for later publish image fetch via{" "}
        <code>PUBLIC_BASE_URL</code>).
      </p>
    </div>
  );
}

function AccountPanel({ account }: { account: InstagramAccountDetails }) {
  return (
    <section className="panel">
      <h2>Account details</h2>
      <div className="ig-account-head">
        {account.profile_picture_url ? (
          <img src={account.profile_picture_url} alt="" className="ig-avatar" referrerPolicy="no-referrer" />
        ) : (
          <div className="ig-avatar ig-avatar-empty" />
        )}
        <div>
          <p className="ig-handle">@{account.username ?? "unknown"}</p>
          <p className="muted">{account.name ?? "—"}</p>
        </div>
      </div>
      <dl className="detail-list">
        <div>
          <dt>IG user / business account id</dt>
          <dd className="break-all">{account.id}</dd>
        </div>
        <div>
          <dt>Account type</dt>
          <dd>{account.account_type ?? "—"}</dd>
        </div>
        <div>
          <dt>Followers</dt>
          <dd>{formatCount(account.followers_count)}</dd>
        </div>
        <div>
          <dt>Following</dt>
          <dd>{formatCount(account.follows_count)}</dd>
        </div>
        <div>
          <dt>Media count</dt>
          <dd>{formatCount(account.media_count)}</dd>
        </div>
        <div>
          <dt>Linked Facebook Page id</dt>
          <dd className="break-all">{account.linked_facebook_page_id ?? "Not returned by token"}</dd>
        </div>
        <div>
          <dt>Website</dt>
          <dd className="break-all">{account.website ?? "—"}</dd>
        </div>
        <div className="span-2">
          <dt>Biography</dt>
          <dd>{account.biography ?? "—"}</dd>
        </div>
      </dl>
    </section>
  );
}

function formatCount(value: number | null | undefined): string {
  if (value == null) return "—";
  return value.toLocaleString();
}

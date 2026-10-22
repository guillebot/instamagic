import { FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import type { InstagramAccountDetails, InstagramConnectionStatus, InstagramRefreshResult } from "../types";

export function InstagramPage() {
  const queryClient = useQueryClient();
  const [tokenInput, setTokenInput] = useState("");
  const [accountIdInput, setAccountIdInput] = useState("");
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
      setBanner("Meta OAuth succeeded — credentials saved and account fetched.");
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
      setBanner("Database credentials cleared.");
    },
    onError: (err) => {
      setBannerKind("error");
      setBanner(err instanceof ApiError ? err.message : "Could not clear credentials.");
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

  const onSave = (event: FormEvent) => {
    event.preventDefault();
    if (!tokenInput.trim() || !accountIdInput.trim()) {
      setBannerKind("error");
      setBanner("Paste both the long-lived access token and the Instagram business account id.");
      return;
    }
    saveCreds.mutate();
  };

  return (
    <main className="page">
      <h1>Instagram connection</h1>
      <p>
        Connect a Professional Instagram account (Business/Creator linked to a Facebook Page). Credentials are
        sealed in the database (or read from host <code>.env</code>). This page never shows the full access
        token and never publishes.
      </p>

      {banner ? <p className={bannerKind === "ok" ? "ok" : "error"}>{banner}</p> : null}
      {statusError ? <p className="error">{statusError}</p> : null}

      <section className="panel">
        <h2>Status</h2>
        {status.isLoading ? <p className="muted">Loading…</p> : null}
        {connection ? (
          <dl className="detail-list">
            <div>
              <dt>Configured</dt>
              <dd>{connection.configured ? "Yes" : "No — add credentials below"}</dd>
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
            Instagram is not configured. Paste a long-lived Page token and IG business user id below
            {connection?.oauth_available ? ", or connect with Meta OAuth" : ""}, then use Test connection.
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
          {connection?.oauth_available ? (
            <button
              type="button"
              className="ghost"
              disabled={startOauth.isPending}
              onClick={() => startOauth.mutate()}
            >
              {startOauth.isPending ? "Opening Meta…" : "Connect with Meta OAuth"}
            </button>
          ) : null}
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

      {connection?.can_edit_database_credentials !== false ? (
        <section className="panel">
          <h2>Save credentials</h2>
          {connection?.env_overrides_database ? (
            <p className="muted">
              Host <code>.env</code> already sets Instagram tokens — those win until cleared and the API is
              restarted. Database paste is disabled while env is set.
            </p>
          ) : (
            <>
              <p className="muted">
                Paste a <strong>long-lived Page access token</strong> and the{" "}
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
        </section>
      ) : null}

      {account ? <AccountPanel account={account} /> : null}

      <section className="panel">
        <h2>How to configure</h2>
        <ol className="howto">
          <li>Convert the Instagram account to Professional (Business or Creator) and link a Facebook Page.</li>
          <li>
            In a Meta app, generate a long-lived Page access token with Instagram Graph publish permissions —
            or set <code>META_APP_ID</code> / <code>META_APP_SECRET</code> on the host and use{" "}
            <strong>Connect with Meta OAuth</strong> (redirect URI must match Traefik HTTPS).
          </li>
          <li>
            Prefer saving credentials here (encrypted in Postgres). Alternatively put{" "}
            <code>INSTAGRAM_PROVIDER=graph</code>, <code>INSTAGRAM_ACCESS_TOKEN</code>, and{" "}
            <code>INSTAGRAM_BUSINESS_ACCOUNT_ID</code> in the host <code>.env</code> (chmod 600) and restart.
            Never commit secrets.
          </li>
          <li>
            Use <strong>Test connection / Refresh account details</strong> to pull username, counts, and linked
            Page id into the saved profile.
          </li>
          <li>
            Real publish also needs a public https <code>PUBLIC_BASE_URL</code> Meta can fetch. This page only
            inspects the account — it does not post.
          </li>
        </ol>
      </section>
    </main>
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

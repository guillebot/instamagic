import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import type { InstagramAccountDetails, InstagramConnectionStatus, InstagramRefreshResult } from "../types";

export function InstagramPage() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["instagram-status"],
    queryFn: () => api<InstagramConnectionStatus>("/api/instagram/status"),
  });

  const refresh = useMutation({
    mutationFn: () => api<InstagramRefreshResult>("/api/instagram/account", { method: "POST" }),
    onSuccess: async (body) => {
      await queryClient.setQueryData(["instagram-status"], {
        configured: body.configured,
        provider: body.provider,
        auto_publish: body.auto_publish,
        public_base_url: body.public_base_url,
        graph_version: body.graph_version,
        token_configured: body.token_configured,
        token_masked: body.token_masked,
        business_account_id: body.business_account_id,
        business_account_id_configured: body.business_account_id_configured,
      } satisfies InstagramConnectionStatus);
    },
  });

  const connection = status.data;
  const account = refresh.data?.account ?? null;
  const liveError = refresh.data && !refresh.data.ok ? refresh.data.error : null;
  const requestError =
    refresh.error instanceof ApiError
      ? refresh.error.message
      : refresh.isError
        ? "Could not reach Instagram helper."
        : null;

  return (
    <main className="page">
      <h1>Instagram connection</h1>
      <p>
        Verify the Meta Graph credentials used for human-gated publish. Tokens stay in server env — this page
        never shows the full access token and never publishes.
      </p>

      <section className="panel">
        <h2>Status</h2>
        {status.isLoading ? <p className="muted">Loading…</p> : null}
        {status.isError ? <p className="error">Could not load Instagram status.</p> : null}
        {connection ? (
          <dl className="detail-list">
            <div>
              <dt>Configured</dt>
              <dd>{connection.configured ? "Yes" : "No"}</dd>
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
                  : "Not set — add INSTAGRAM_ACCESS_TOKEN to .env"}
              </dd>
            </div>
            <div>
              <dt>Business account id</dt>
              <dd>
                {connection.business_account_id_configured
                  ? connection.business_account_id ?? "(fake provider)"
                  : "Not set — add INSTAGRAM_BUSINESS_ACCOUNT_ID to .env"}
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
          </dl>
        ) : null}

        <div className="row">
          <button
            type="button"
            className="button"
            disabled={refresh.isPending || !connection?.configured}
            onClick={() => refresh.mutate()}
          >
            {refresh.isPending ? "Checking…" : "Test connection / Refresh account details"}
          </button>
        </div>
        {!connection?.configured ? (
          <p className="muted">
            Set <code>INSTAGRAM_ACCESS_TOKEN</code> and <code>INSTAGRAM_BUSINESS_ACCOUNT_ID</code> in the host{" "}
            <code>.env</code>, restart the API, then refresh. For local gate tests use{" "}
            <code>INSTAGRAM_PROVIDER=fake</code>.
          </p>
        ) : null}
        {liveError ? <p className="error">{liveError}</p> : null}
        {requestError ? <p className="error">{requestError}</p> : null}
        {refresh.data?.ok ? <p className="ok">Connection ok — account details below.</p> : null}
      </section>

      {account ? <AccountPanel account={account} /> : null}

      <section className="panel">
        <h2>How to configure</h2>
        <ol className="howto">
          <li>Convert the Instagram account to Professional (Business or Creator) and link a Facebook Page.</li>
          <li>
            In a Meta app, generate a long-lived Page access token with Instagram Graph publish permissions.
          </li>
          <li>
            Put <code>INSTAGRAM_PROVIDER=graph</code>, <code>INSTAGRAM_ACCESS_TOKEN</code>, and{" "}
            <code>INSTAGRAM_BUSINESS_ACCOUNT_ID</code> (IG user id, not the Page id) in the host{" "}
            <code>.env</code> (chmod 600). Never commit secrets.
          </li>
          <li>Restart the API / Compose stack so settings reload, then use the button above.</li>
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

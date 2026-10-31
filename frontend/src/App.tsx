import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Outlet, useNavigate } from "react-router-dom";
import { ApiError, api } from "./api";
import { SystemHealthBar } from "./SystemHealthBar";
import { VersionChip } from "./VersionChip";
import { LoginPage } from "./pages/Login";
import { CandidatesPage } from "./pages/Candidates";
import { CuratePage } from "./pages/Curate";
import { InstagramPage } from "./pages/Instagram";
import { QueuePage } from "./pages/Queue";
import { EngagePage } from "./pages/Engage";
import { InboxPage } from "./pages/Inbox";
import { InsightsPage } from "./pages/Insights";
import { SchedulePage } from "./pages/Schedule";
import { MusicPage } from "./pages/Music";
import { RelatedAccountsPage } from "./pages/RelatedAccounts";
import { ReviewPage } from "./pages/Review";
import { StatusPage } from "./pages/Status";
import { Route, Routes } from "react-router-dom";

function Shell() {
  const navigate = useNavigate();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => api<{ authenticated: boolean }>("/api/auth/session"),
    // Only a real 401 means "logged out". A 502/504 or network error while the
    // stack restarts or deploys must not bounce you to the login page.
    retry: (failureCount, error) => !(error instanceof ApiError && error.status === 401) && failureCount < 1000,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 10000),
  });

  if (session.isError && session.error instanceof ApiError && session.error.status === 401) {
    return <Navigate to="/login" replace />;
  }
  if (session.isLoading || session.isError) {
    return (
      <p className="page">
        {session.failureCount > 0 ? "Instamagic is restarting… reconnecting" : "Loading session…"}
      </p>
    );
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-row">
          <div className="brand-row">
            <div className="brand">Instamagic</div>
            <VersionChip />
          </div>
          <nav className="nav">
          <NavLink to="/" end>
            Review
          </NavLink>
          <NavLink to="/curate">Curate</NavLink>
          <NavLink to="/queue">Queue</NavLink>
          <NavLink to="/queue/schedule">Schedule</NavLink>
          <NavLink to="/music">Music</NavLink>
          <NavLink to="/candidates">Next</NavLink>
          <NavLink to="/related">Related</NavLink>
          <NavLink to="/insights">Insights</NavLink>
          <NavLink to="/inbox">Inbox</NavLink>
          <NavLink to="/engage">Engage</NavLink>
          <NavLink to="/instagram">Instagram</NavLink>
          <NavLink to="/status">Status</NavLink>
          <button
            className="ghost"
            onClick={() => {
              void api("/api/auth/logout", { method: "POST" }).then(() => navigate("/login"));
            }}
          >
            Log out
          </button>
          </nav>
        </div>
        <SystemHealthBar />
      </header>
      <Outlet />
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Shell />}>
        <Route path="/" element={<ReviewPage />} />
        <Route path="/queue" element={<QueuePage />} />
        <Route path="/queue/schedule" element={<SchedulePage />} />
        <Route path="/music" element={<MusicPage />} />
        <Route path="/curate" element={<CuratePage />} />
        <Route path="/candidates" element={<CandidatesPage />} />
        <Route path="/related" element={<RelatedAccountsPage />} />
        <Route path="/insights" element={<InsightsPage />} />
        <Route path="/inbox" element={<InboxPage />} />
        <Route path="/engage" element={<EngagePage />} />
        <Route path="/instagram" element={<InstagramPage />} />
        <Route path="/status" element={<StatusPage />} />
      </Route>
    </Routes>
  );
}

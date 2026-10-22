import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Outlet, useNavigate } from "react-router-dom";
import { api } from "./api";
import { SystemHealthBar } from "./SystemHealthBar";
import { VersionChip } from "./VersionChip";
import { LoginPage } from "./pages/Login";
import { CandidatesPage } from "./pages/Candidates";
import { CuratePage } from "./pages/Curate";
import { InstagramPage } from "./pages/Instagram";
import { QueuePage } from "./pages/Queue";
import { SchedulePage } from "./pages/Schedule";
import { RelatedAccountsPage } from "./pages/RelatedAccounts";
import { ReviewPage } from "./pages/Review";
import { Route, Routes } from "react-router-dom";

function Shell() {
  const navigate = useNavigate();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => api<{ authenticated: boolean }>("/api/auth/session"),
    retry: false,
  });

  if (session.isLoading) {
    return <p className="page">Loading session…</p>;
  }
  if (session.isError) {
    return <Navigate to="/login" replace />;
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
          <NavLink to="/candidates">Next</NavLink>
          <NavLink to="/related">Related</NavLink>
          <NavLink to="/instagram">Instagram</NavLink>
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
        <Route path="/curate" element={<CuratePage />} />
        <Route path="/candidates" element={<CandidatesPage />} />
        <Route path="/related" element={<RelatedAccountsPage />} />
        <Route path="/instagram" element={<InstagramPage />} />
      </Route>
    </Routes>
  );
}

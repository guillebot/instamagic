import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Outlet, useNavigate } from "react-router-dom";
import { api } from "./api";
import { VersionChip } from "./VersionChip";
import { LoginPage } from "./pages/Login";
import { CandidatesPage } from "./pages/Candidates";
import { QueuePage } from "./pages/Queue";
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
        <div className="brand-row">
          <div className="brand">Instamagic</div>
          <VersionChip />
        </div>
        <nav className="nav">
          <NavLink to="/" end>
            Review
          </NavLink>
          <NavLink to="/queue">Queue</NavLink>
          <NavLink to="/candidates">Next</NavLink>
          <NavLink to="/related">Related</NavLink>
          <button
            className="ghost"
            onClick={() => {
              void api("/api/auth/logout", { method: "POST" }).then(() => navigate("/login"));
            }}
          >
            Log out
          </button>
        </nav>
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
        <Route path="/candidates" element={<CandidatesPage />} />
        <Route path="/related" element={<RelatedAccountsPage />} />
      </Route>
    </Routes>
  );
}

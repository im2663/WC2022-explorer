import { BrowserRouter, Routes, Route, Link } from "react-router-dom";
import PlayersPage from "./pages/PlayersPage";

function Placeholder({ title }: { title: string }) {
  return (
    <div className="p-8 text-slate-400">
      <h1 className="text-xl font-semibold text-slate-200">{title}</h1>
      <p className="mt-2">Coming soon.</p>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-slate-950 text-slate-100">
        <nav className="border-b border-slate-800 px-6 py-4 flex gap-6 items-center">
          <span className="font-bold text-lg tracking-tight">WC22 Explorer</span>
          <Link to="/players" className="text-slate-300 hover:text-white">
            Players
          </Link>
          <Link to="/teams" className="text-slate-300 hover:text-white">
            Teams
          </Link>
        </nav>
        <Routes>
          <Route path="/" element={<PlayersPage />} />
          <Route path="/players" element={<PlayersPage />} />
          <Route path="/players/:id" element={<Placeholder title="Player Profile" />} />
          <Route path="/teams" element={<Placeholder title="Teams" />} />
          <Route path="/teams/:id" element={<Placeholder title="Team Profile" />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}

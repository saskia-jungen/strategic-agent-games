import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import PlayPage from './pages/PlayPage';
import LeaderboardPage from './pages/LeaderboardPage';
import HistoryPage from './pages/HistoryPage';
import AgentsPage from './pages/AgentsPage';
import GamesPage from './pages/GamesPage';
import { ToastProvider } from './components/Toast';

export default function App() {
  return (
    <ToastProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<PlayPage />} />
            <Route path="leaderboard" element={<LeaderboardPage />} />
            <Route path="history" element={<HistoryPage />} />
            <Route path="agents" element={<AgentsPage />} />
            <Route path="games" element={<GamesPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ToastProvider>
  );
}

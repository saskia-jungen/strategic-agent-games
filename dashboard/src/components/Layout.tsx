import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { Swords, Trophy, History, Radio, Bot, BookOpen, Menu, X } from 'lucide-react';
import GridBackground from './GridBackground';

const NAV = [
  { to: '/', icon: Radio, label: 'Arena' },
  { to: '/leaderboard', icon: Trophy, label: 'Leaderboard' },
  { to: '/history', icon: History, label: 'History' },
  { to: '/agents', icon: Bot, label: 'Agents' },
  { to: '/games', icon: BookOpen, label: 'Games' },
];

export default function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const handleNav = () => setMobileOpen(false);

  return (
    <div className="min-h-screen flex flex-col">
      <GridBackground />

      {/* Header */}
      <header className="border-b border-border px-3 sm:px-6 py-3 bg-bg/80 backdrop-blur-md sticky top-0 z-50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 sm:gap-3">
            <Swords className="w-5 h-5 text-text flex-shrink-0" />
            <span className="font-mono text-xs sm:text-sm font-semibold uppercase tracking-[0.15em]">
              Strategic Agent Games
            </span>
          </div>

          {/* Desktop nav */}
          <nav className="hidden sm:flex gap-1">
            {NAV.map(({ to, icon: Icon, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  `flex items-center gap-2 px-3 py-2 rounded-sm font-mono text-xs lowercase tracking-[0.05em] transition-colors ${
                    isActive
                      ? 'bg-accent/15 text-accent'
                      : 'text-text-muted hover:text-text hover:bg-surface-hover'
                  }`
                }
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
              </NavLink>
            ))}
          </nav>

          {/* Hamburger button */}
          <button
            onClick={() => setMobileOpen(!mobileOpen)}
            className="sm:hidden p-2 rounded-sm text-text-muted hover:text-text hover:bg-surface-hover transition-colors"
            aria-label="Toggle menu"
          >
            {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
        </div>

        {/* Mobile nav dropdown */}
        {mobileOpen && (
          <nav className="sm:hidden mt-3 pt-3 border-t border-border flex flex-col gap-1">
            {NAV.map(({ to, icon: Icon, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                onClick={handleNav}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-sm font-mono text-xs lowercase tracking-[0.05em] transition-colors ${
                    isActive
                      ? 'bg-accent/15 text-accent'
                      : 'text-text-muted hover:text-text hover:bg-surface-hover'
                  }`
                }
              >
                <Icon className="w-4 h-4" />
                {label}
              </NavLink>
            ))}
          </nav>
        )}
      </header>

      {/* Content */}
      <main className="flex-1 p-3 sm:p-6 max-w-7xl mx-auto w-full">
        <Outlet />
      </main>
    </div>
  );
}

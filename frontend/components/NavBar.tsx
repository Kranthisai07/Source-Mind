import Link from 'next/link';
import DarkModeToggle from './DarkModeToggle';

export default function NavBar() {
  return (
    <header className="sticky top-0 z-fixed bg-bg-primary border-b shadow-sm">
      <div className="container">
        <div className="flex items-center justify-between py-4">
          {/* Logo */}
          <Link href="/workspaces" className="flex items-center gap-2 group">
            <span className="text-2xl">🧠</span>
            <span className="text-xl font-bold text-primary group-hover:text-primary-700 transition-colors">
              SourceMind
            </span>
          </Link>

          {/* Center - Tagline (hidden on mobile) */}
          <div className="hidden md:block text-sm text-tertiary">
            Collaborative memory with attribution intelligence
          </div>

          {/* Right - Actions */}
          <div className="flex items-center gap-2">
            <DarkModeToggle />
            <Link href="/workspaces" className="btn btn-sm btn-ghost">
              Workspaces
            </Link>
            <Link href="/profile" className="btn btn-sm btn-outline">
              Profile
            </Link>
          </div>
        </div>
      </div>
    </header>
  );
}

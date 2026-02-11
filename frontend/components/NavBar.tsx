'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

export default function NavBar() {
  const pathname = usePathname();
  const isActive = (path: string) => pathname?.startsWith(path);

  return (
    <header className="sticky top-0 z-50 bg-white/80 backdrop-blur-xl border-b border-neutral-200">
      <div className="max-w-7xl mx-auto px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          {/* Logo */}
          <Link href="/workspaces" className="flex items-center gap-2">
            <div className="w-8 h-8 bg-gradient-to-br from-primary-600 to-secondary-600 rounded-lg flex items-center justify-center">
              <span className="text-white text-lg font-bold">S</span>
            </div>
            <span className="text-lg font-semibold text-neutral-900">SourceMind</span>
          </Link>

          {/* Navigation */}
          <nav className="hidden md:flex items-center gap-1">
            <Link
              href="/workspaces"
              className={`px-3 py-2 text-sm font-medium rounded-lg transition ${isActive('/workspaces')
                  ? 'bg-neutral-100 text-neutral-900'
                  : 'text-neutral-600 hover:text-neutral-900 hover:bg-neutral-50'
                }`}
            >
              Workspaces
            </Link>
            <Link
              href="/search"
              className={`px-3 py-2 text-sm font-medium rounded-lg transition ${isActive('/search')
                  ? 'bg-neutral-100 text-neutral-900'
                  : 'text-neutral-600 hover:text-neutral-900 hover:bg-neutral-50'
                }`}
            >
              Search
            </Link>
            <Link
              href="/analytics"
              className={`px-3 py-2 text-sm font-medium rounded-lg transition ${isActive('/analytics')
                  ? 'bg-neutral-100 text-neutral-900'
                  : 'text-neutral-600 hover:text-neutral-900 hover:bg-neutral-50'
                }`}
            >
              Analytics
            </Link>
          </nav>

          {/* Actions */}
          <div className="flex items-center gap-2">
            <Link
              href="/profile"
              className="px-3 py-2 text-sm font-medium text-neutral-600 hover:text-neutral-900 rounded-lg hover:bg-neutral-50 transition"
            >
              Profile
            </Link>
            <button
              onClick={() => {
                localStorage.removeItem('token');
                localStorage.removeItem('refreshToken');
                window.location.href = '/login';
              }}
              className="px-3 py-2 text-sm font-medium text-neutral-600 hover:text-neutral-900 rounded-lg hover:bg-neutral-50 transition"
            >
              Sign out
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}

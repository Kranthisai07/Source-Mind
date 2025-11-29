import Link from 'next/link';

export default function NavBar() {
  return (
    <header className="flex items-center justify-between px-6 py-3 border-b bg-white">
      <Link href="/workspaces" className="font-semibold">
        SourceMind
      </Link>
      <div className="text-sm text-slate-600">Collaborative memory with attribution</div>
    </header>
  );
}

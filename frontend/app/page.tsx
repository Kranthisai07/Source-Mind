import Link from 'next/link';

export default function Home() {
  return (
    <main className="flex flex-col items-center justify-center min-h-screen gap-4">
      <h1 className="text-3xl font-semibold">SourceMind</h1>
      <p className="text-slate-600">Collaborative memory with attribution intelligence.</p>
      <div className="flex gap-3">
        <Link className="px-4 py-2 rounded bg-slate-900 text-white" href="/login">
          Login
        </Link>
        <Link className="px-4 py-2 rounded border border-slate-300" href="/register">
          Register
        </Link>
      </div>
    </main>
  );
}

"use client";

import useSWR from 'swr';
import api from '../../lib/api';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

const fetcher = (url: string) =>
  api
    .get(url, { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })
    .then((res) => res.data.data);

export default function WorkspacesPage() {
  const router = useRouter();

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) router.push('/login');
  }, [router]);

  const { data, error } = useSWR('/workspaces', fetcher);

  return (
    <main className="p-8">
      <h1 className="text-2xl font-semibold mb-4">Workspaces</h1>
      {error && <p className="text-red-600">Failed to load workspaces</p>}
      <div className="grid gap-3">
        {data?.map((ws: any) => (
          <Link
            key={ws.id}
            className="card hover:border-slate-400 transition"
            href={`/workspaces/${ws.id}/projects`}
          >
            <div className="font-medium">{ws.name}</div>
            <div className="text-sm text-slate-600">Created {new Date(ws.createdAt).toDateString()}</div>
          </Link>
        ))}
      </div>
    </main>
  );
}

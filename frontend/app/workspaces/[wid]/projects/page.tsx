"use client";

import useSWR from 'swr';
import api from '../../../../lib/api';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useState } from 'react';

const fetcher = (url: string, token: string) =>
  api
    .get(url, { headers: { Authorization: `Bearer ${token}` } })
    .then((res) => res.data.data);

export default function ProjectsPage() {
  const params = useParams();
  const workspaceId = params?.wid as string;
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') ?? '' : '';
  const { data, mutate } = useSWR(
    token ? [`/workspaces/${workspaceId}/projects`, token] : null,
    ([url, t]) => fetcher(url, t),
  );
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const createProject = async () => {
    if (!name) return;
    await api.post(
      `/workspaces/${workspaceId}/projects`,
      { name, description },
      { headers: { Authorization: `Bearer ${token}` } },
    );
    setName('');
    setDescription('');
    mutate();
  };

  return (
    <main className="p-8 space-y-4">
      <h1 className="text-2xl font-semibold">Projects</h1>
      <div className="card space-y-2">
        <div className="font-medium">Create project</div>
        <input
          className="w-full border rounded px-3 py-2"
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="w-full border rounded px-3 py-2"
          placeholder="Description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <button className="bg-slate-900 text-white rounded px-3 py-2" onClick={createProject}>
          Create
        </button>
      </div>
      <div className="grid gap-3">
        {data?.map((p: any) => (
          <Link
            key={p.id}
            className="card hover:border-slate-400 transition"
            href={`/workspaces/${workspaceId}/projects/${p.id}/memories`}
          >
            <div className="font-medium">{p.name}</div>
            <div className="text-sm text-slate-600">{p.description}</div>
          </Link>
        ))}
      </div>
    </main>
  );
}

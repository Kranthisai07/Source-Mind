"use client";

import useSWR from 'swr';
import api from '../../../../../../lib/api';
import { useParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import MemoryCard from '@/components/MemoryCard';
import AttributionBar from '@/components/AttributionBar';
import EditHistoryList from '@/components/EditHistoryList';
import RelationList from '@/components/RelationList';

const fetcher = (url: string, token: string) =>
  api
    .post(url, {}, { headers: { Authorization: `Bearer ${token}` } })
    .then((res) => res.data.data);

export default function MemoriesPage() {
  const params = useParams();
  const workspaceId = params?.wid as string;
  const projectId = params?.pid as string;
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') ?? '' : '';
  const { data, mutate } = useSWR(
    token ? [`/workspaces/${workspaceId}/projects/${projectId}/memories/list`, token] : null,
    ([url, t]) => fetcher(url, t),
  );

  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [selected, setSelected] = useState<any | null>(null);
  const [relations, setRelations] = useState<any[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<any[]>([]);

  useEffect(() => {
    const fetchRelations = async () => {
      if (!selected?.id) return;
      const res = await api.get(
        `/workspaces/${workspaceId}/memories/${selected.id}/relations`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      setRelations(res.data.data);
    };
    fetchRelations();
  }, [selected?.id, token, workspaceId]);

  const createMemory = async () => {
    await api.post(
      `/workspaces/${workspaceId}/memories`,
      {
        projectId,
        type: 'note',
        source: 'human',
        title,
        content,
      },
      { headers: { Authorization: `Bearer ${token}` } },
    );
    setTitle('');
    setContent('');
    mutate();
  };

  const runSearch = async () => {
    if (!searchQuery) return;
    const res = await api.post(
      `/workspaces/${workspaceId}/search`,
      { query: searchQuery, projectId, limit: 5 },
      { headers: { Authorization: `Bearer ${token}` } },
    );
    setSearchResults(res.data.data);
  };

  return (
    <main className="p-8 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Memories</h1>
        <Link
          href={`/workspaces/${workspaceId}/projects/${projectId}/chat`}
          className="px-3 py-2 rounded bg-slate-900 text-white"
        >
          Chat
        </Link>
      </div>
      <div className="card space-y-2">
        <div className="font-medium">Create memory</div>
        <input
          className="w-full border rounded px-3 py-2"
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <textarea
          className="w-full border rounded px-3 py-2"
          placeholder="Content"
          rows={4}
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
        <button className="bg-slate-900 text-white rounded px-3 py-2" onClick={createMemory}>
          Save
        </button>
      </div>

      <div className="card space-y-2">
        <div className="font-medium">Search memories</div>
        <div className="flex gap-2">
          <input
            className="flex-1 border rounded px-3 py-2"
            placeholder="Search query"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <button className="bg-slate-900 text-white rounded px-4" onClick={runSearch}>
            Search
          </button>
        </div>
        <div className="grid gap-2">
          {searchResults.map((r: any, idx) => (
            <div key={idx} className="border rounded p-2 text-sm">
              <div className="text-xs text-slate-500">Score {r.score.toFixed(2)}</div>
              <div className="font-semibold">{r.memory.title}</div>
              <div className="text-slate-700 whitespace-pre-wrap">{r.memory.content.slice(0, 150)}</div>
            </div>
          ))}
          {!searchResults.length && <div className="text-xs text-slate-500">No search results yet.</div>}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="space-y-3 col-span-1">
          {data?.map((m: any) => (
            <MemoryCard
              key={m.id}
              title={m.title}
              content={m.content}
              type={m.type}
              importanceScore={m.importanceScore}
              onClick={() => setSelected(m)}
            />
          ))}
        </div>
        <div className="col-span-2 card space-y-3 min-h-[400px]">
          {selected ? (
            <>
              <div className="text-xs text-slate-500 uppercase">{selected.type}</div>
              <h2 className="text-xl font-semibold">{selected.title || 'Untitled'}</h2>
              <div className="whitespace-pre-wrap text-slate-800">{selected.content}</div>
              <div className="text-sm text-slate-600">
                Importance {selected.importanceScore} · {new Date(selected.createdAt).toLocaleString()}
              </div>
              <div className="space-y-2">
                <div className="font-medium text-sm">Attribution</div>
                <AttributionBar
                  segments={
                    selected.attributions?.map((a: any) => ({
                      label: a.contributorId,
                      percent: a.contributionPercent,
                      color: a.contributorType === 'ai' ? '#f59e0b' : '#0f172a',
                    })) || []
                  }
                />
                <div className="text-xs text-slate-600">
                  {selected.attributions
                    ?.map(
                      (a: any) =>
                        `${a.contributorType === 'ai' ? 'AI' : 'Human'} ${a.contributorId} (${Math.round(
                          a.contributionPercent * 100,
                        )}%)`,
                    )
                    .join(' • ')}
                </div>
              </div>
              <div>
                <div className="font-medium text-sm mb-1">Edit history</div>
                <EditHistoryList edits={selected.edits || []} />
              </div>
              <div>
                <div className="font-medium text-sm mb-1">Relations</div>
                <RelationList relations={relations} />
              </div>
            </>
          ) : (
            <div className="text-slate-500">Select a memory to view details.</div>
          )}
        </div>
      </div>
    </main>
  );
}

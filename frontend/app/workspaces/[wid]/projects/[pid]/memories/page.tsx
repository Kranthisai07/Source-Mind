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
import Skeleton from '@/components/Skeleton';
import Spinner from '@/components/Spinner';
import toast from 'react-hot-toast';
import useSWRInfinite from 'swr/infinite';

export default function MemoriesPage() {
  const params = useParams();
  const workspaceId = params?.wid as string;
  const projectId = params?.pid as string;

  // Infinite Scroll Pagination
  const { data, size, setSize, isValidating, mutate, error } = useSWRInfinite(
    (index) => workspaceId && projectId ? [`/workspaces/${workspaceId}/projects/${projectId}/memories/list`, index + 1] : null,
    async ([url, page]) => {
      const res = await api.post(url, { page, limit: 10 });
      return res.data.data;
    }
  );

  const memories = data ? ([] as any[]).concat(...data) : [];
  const isLoadingInitialData = !data && !error;
  const isLoadingMore =
    isLoadingInitialData ||
    (size > 0 && data && typeof data[size - 1] === 'undefined');
  const isEmpty = data?.[0]?.length === 0;
  const isReachingEnd =
    isEmpty || (data && data[data.length - 1]?.length < 10);

  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [selected, setSelected] = useState<any | null>(null);
  const [relations, setRelations] = useState<any[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<any[]>([]);

  // UI States
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [activeTab, setActiveTab] = useState<'list' | 'search'>('list');

  // Fetch relations when selected
  useEffect(() => {
    const fetchRelations = async () => {
      if (!selected?.id) return;
      try {
        const res = await api.get(`/workspaces/${workspaceId}/memories/${selected.id}/relations`);
        setRelations(res.data.data);
      } catch (e) {
        console.error("Failed to fetch relations", e);
        setRelations([]);
      }
    };
    fetchRelations();
  }, [selected?.id, workspaceId]);

  const createMemory = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !content.trim()) return;

    setIsCreating(true);
    try {
      await api.post(`/workspaces/${workspaceId}/memories`, {
        projectId,
        type: 'note',
        source: 'human',
        title,
        content,
      });
      setTitle('');
      setContent('');
      setIsCreateModalOpen(false);
      mutate();
      toast.success('Memory created successfully');
    } catch (err) {
      console.error('Failed to create memory', err);
      toast.error('Failed to create memory');
    } finally {
      setIsCreating(false);
    }
  };

  const runSearch = async () => {
    if (!searchQuery.trim()) return;
    try {
      const res = await api.post(`/workspaces/${workspaceId}/search`, {
        query: searchQuery, projectId, limit: 10
      });
      setSearchResults(res.data.data);
      setActiveTab('search');
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <main className="container mx-auto p-4 lg:p-8 max-w-7xl h-[calc(100vh-64px)] overflow-hidden flex flex-col">
      {/* Header */}
      <div className="flex justify-between items-center mb-6 shrink-0">
        <div>
          <Link href={`/workspaces/${workspaceId}/projects`} className="text-sm text-text-tertiary hover:text-primary mb-2 inline-flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 19l-7-7 7-7" /></svg>
            Back to Projects
          </Link>
          <h1 className="text-2xl font-bold text-primary">Project Memories</h1>
        </div>
        <div className="flex gap-3">
          <Link href={`/workspaces/${workspaceId}/projects/${projectId}/chat`} className="btn btn-secondary">
            Open Chat
          </Link>
          <button onClick={() => setIsCreateModalOpen(true)} className="btn btn-primary">
            + New Memory
          </button>
        </div>
      </div>

      {/* Content Area */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-12 gap-6 min-h-0">

        {/* Left Panel: List/Search */}
        <div className="lg:col-span-4 flex flex-col h-full bg-bg-secondary/50 rounded-xl border border-border overflow-hidden shadow-sm">
          {/* Search Bar */}
          <div className="p-4 border-b border-border space-y-3 bg-bg-card">
            <div className="relative">
              <input
                className="input w-full pl-9"
                placeholder="Search memories..."
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  if (e.target.value.length === 0) {
                    setActiveTab('list');
                  }
                }}
                onKeyDown={(e) => e.key === 'Enter' && runSearch()}
              />
              <svg className="absolute left-3 top-3 text-text-tertiary" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            </div>
            {searchQuery && (
              <button onClick={runSearch} className="btn btn-sm btn-neutral w-full">Search</button>
            )}
          </div>

          {/* List Content */}
          <div className="flex-1 overflow-y-auto p-3 space-y-3 custom-scrollbar">
            {activeTab === 'list' ? (
              isLoadingInitialData ? (
                // Loading Skeletons
                Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="card p-4 border border-border space-y-3">
                    <div className="flex justify-between">
                      <Skeleton variant="text" width={60} />
                      <Skeleton variant="text" width={80} />
                    </div>
                    <Skeleton variant="text" width="70%" height={20} />
                    <Skeleton variant="text" width="100%" />
                    <Skeleton variant="text" width="40%" />
                  </div>
                ))
              ) : memories?.length > 0 ? (
                <>
                  {memories.map((m: any) => (
                    <div key={m.id} className={selected?.id === m.id ? 'ring-2 ring-primary rounded-xl' : ''}>
                      <MemoryCard
                        {...m}
                        createdAt={new Date(m.createdAt).toLocaleDateString()}
                        onClick={() => setSelected(m)}
                      />
                    </div>
                  ))}
                  {!isReachingEnd && (
                    <button
                      onClick={() => setSize(size + 1)}
                      className="btn btn-ghost w-full py-3 text-sm text-text-secondary hover:bg-bg-tertiary transition-colors"
                      disabled={isLoadingMore}
                    >
                      {isLoadingMore ? (
                        <div className="flex items-center justify-center gap-2">
                          <Spinner size="sm" /> Loading more...
                        </div>
                      ) : 'Load More'}
                    </button>
                  )}
                </>
              ) : (
                <div className="text-center py-10 text-text-tertiary">
                  <p className="mb-2">No memories yet.</p>
                  <button onClick={() => setIsCreateModalOpen(true)} className="text-primary hover:underline text-sm">Create your first one</button>
                </div>
              )
            ) : (
              searchResults.length > 0 ? (
                searchResults.map((r: any, idx) => (
                  <div key={idx} onClick={() => setSelected(r.memory)} className={`cursor-pointer p-3 rounded-lg hover:bg-bg-tertiary border border-dashed border-border transition-colors ${selected?.id === r.memory?.id ? 'bg-primary-50 border-primary' : ''}`}>
                    <div className="flex justify-between text-xs text-text-tertiary mb-1">
                      <span>Match Score</span>
                      <span className="font-mono text-primary pt-0.5 px-1.5 bg-primary-50 rounded">{(r.score * 100).toFixed(0)}%</span>
                    </div>
                    <div className="font-medium text-text-primary mb-1">{r.memory.title || "Untitled"}</div>
                    <div className="text-xs text-text-secondary line-clamp-2">{r.memory.content}</div>
                  </div>
                ))
              ) : (
                <div className="text-center py-10 text-text-tertiary">
                  {searchQuery ? "No results found." : "Type to search..."}
                </div>
              )
            )}
          </div>
        </div>

        {/* Right Panel: Detail View */}
        <div className="lg:col-span-8 h-full bg-bg-card rounded-xl border border-border overflow-hidden flex flex-col shadow-sm">
          {selected ? (
            <div className="h-full flex flex-col overflow-y-auto custom-scrollbar">
              <div className="p-8 border-b border-border">
                <div className="flex items-center gap-2 mb-4">
                  <span className="badge badge-primary font-bold tracking-wide">{selected.type?.toUpperCase()}</span>
                  <span className="text-sm text-text-tertiary ml-auto">Created {new Date(selected.createdAt).toLocaleString()}</span>
                </div>
                <h2 className="text-3xl font-bold text-text-primary mb-6">{selected.title || 'Untitled Memory'}</h2>
                <div className="prose prose-slate max-w-none text-text-secondary whitespace-pre-wrap leading-relaxed">
                  {selected.content}
                </div>
              </div>

              <div className="p-8 grid grid-cols-1 md:grid-cols-2 gap-8 bg-bg-tertiary/30 flex-1">
                <div className="space-y-8">
                  <section>
                    <h3 className="text-xs font-bold text-text-tertiary uppercase tracking-wider mb-4">Attribution Breakdown</h3>
                    <div className="card p-4 bg-bg-card border-border/60">
                      <AttributionBar
                        contributors={selected.attributions?.map((a: any) => ({
                          id: a.contributorId,
                          name: a.contributorId,
                          type: a.contributorType,
                          score: a.contributionScore,
                        })) || []}
                      />

                      {/* Tool Assistance Badge */}
                      {selected.attributions?.some((a: any) => a.contributorType === 'tool') && (
                        <div className="mt-4 flex items-center gap-2 text-xs text-text-tertiary bg-bg-secondary/50 px-3 py-2 rounded-lg border border-border/50 w-full">
                          <span className="font-semibold text-text-secondary uppercase tracking-wider text-[10px] shrink-0">Assisted by</span>
                          <div className="flex flex-wrap gap-2">
                            {selected.attributions
                              .filter((a: any) => a.contributorType === 'tool')
                              .map((a: any) => (
                                <span key={a.contributorId} className="bg-amber-100/10 text-amber-600 dark:text-amber-400 px-2 py-0.5 rounded flex items-center gap-1.5 font-medium border border-amber-500/10">
                                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="opacity-70"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
                                  {a.contributorId}
                                </span>
                              ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </section>
                  <section>
                    <h3 className="text-xs font-bold text-text-tertiary uppercase tracking-wider mb-4">Connected Memories</h3>
                    <div className="card p-0 overflow-hidden bg-bg-card border-border/60">
                      {relations.length > 0 ? (
                        <RelationList relations={relations} />
                      ) : (
                        <div className="p-4 text-sm text-text-tertiary italic">No linked memories found.</div>
                      )}
                    </div>
                  </section>
                </div>

                <div className="space-y-8">
                  <section>
                    <h3 className="text-xs font-bold text-text-tertiary uppercase tracking-wider mb-4">Version History</h3>
                    <div className="card p-0 overflow-hidden bg-bg-card border-border/60 max-h-[400px] overflow-y-auto">
                      <EditHistoryList edits={selected.edits || []} />
                    </div>
                  </section>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-text-tertiary p-10 bg-bg-tertiary/10">
              <div className="w-24 h-24 bg-bg-secondary rounded-full flex items-center justify-center mb-6 text-neutral-300 shadow-inner">
                <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
              </div>
              <h3 className="text-xl font-medium text-text-primary mb-2">Select a Memory</h3>
              <p className="max-w-xs text-center text-sm">Click on a memory from the list on the left to view its details, history, and relations.</p>
            </div>
          )}
        </div>
      </div>

      {/* Create Modal */}
      {isCreateModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-neutral-900/50 backdrop-blur-sm">
          <div className="card w-full max-w-lg shadow-2xl animate-in fade-in zoom-in duration-200">
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-2xl font-bold">New Memory</h2>
              <button onClick={() => setIsCreateModalOpen(false)} className="text-text-tertiary hover:text-text-primary">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
              </button>
            </div>
            <form onSubmit={createMemory} className="space-y-5">
              <div>
                <label className="label">Title</label>
                <input className="input w-full" value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Meeting Notes, Project Idea" autoFocus />
              </div>
              <div>
                <label className="label">Content</label>
                <textarea className="input w-full min-h-[200px] font-mono text-sm leading-relaxed p-4" value={content} onChange={e => setContent(e.target.value)} placeholder="Type your memory content here..." />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={() => setIsCreateModalOpen(false)} className="btn btn-ghost" disabled={isCreating}>Cancel</button>
                <button type="submit" className="btn btn-primary" disabled={!title.trim() || !content.trim() || isCreating}>
                  {isCreating ? (
                    <>
                      <Spinner size="sm" light className="mr-2 inline-block" />
                      Saving...
                    </>
                  ) : 'Save Memory'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </main>
  );
}

"use client";

import useSWR from 'swr';
import Skeleton from '@/components/Skeleton';
import toast from 'react-hot-toast';
import Spinner from '@/components/Spinner';
import api from '../../lib/api';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

const fetcher = (url: string) =>
  api
    .get(url, { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })
    .then((res) => res.data.data);

export default function WorkspacesPage() {
  const router = useRouter();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [newWorkspaceName, setNewWorkspaceName] = useState('');
  const [isCreating, setIsCreating] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) router.push('/login');
  }, [router]);

  const { data: workspaces, error, mutate } = useSWR('/workspaces', fetcher);

  const handleCreateWorkspace = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newWorkspaceName.trim()) return;

    setIsCreating(true);

    try {
      const res = await api.post('/workspaces', { name: newWorkspaceName });
      const responseBody = res.data;
      const newWorkspace = responseBody.data;

      if (newWorkspace && newWorkspace.id) {
        toast.success('Workspace created successfully!');
        setNewWorkspaceName('');
        setIsModalOpen(false);
        window.location.href = `/workspaces/${newWorkspace.id}/projects`;
        return;
      }
      mutate();
    } catch (err: any) {
      console.error('Failed to create workspace', err);
      const msg = err.response?.data?.message || err.message || 'Failed to create workspace';
      toast.error(msg);
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <main className="container mx-auto p-8 max-w-5xl">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold text-primary mb-2">Workspaces</h1>
          <p className="text-text-secondary">Manage your AI-powered workspaces</p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="btn btn-primary"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
          New Workspace
        </button>
      </div>

      {error && (
        <div className="alert alert-error mb-6">
          Failed to load workspaces. Please try again.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {/* Loading State */}
        {!workspaces && !error && (
          <>
            {[1, 2, 3].map((i) => (
              <div key={`skeleton-${i}`} className="card p-6 h-full border border-border">
                <div className="flex justify-between items-start mb-4">
                  <Skeleton variant="rectangular" width={40} height={40} className="rounded-lg" />
                  <Skeleton variant="text" width={50} />
                </div>
                <Skeleton variant="text" width="60%" height={24} className="mb-2" />
                <Skeleton variant="text" width="40%" height={16} />
              </div>
            ))}
          </>
        )}

        {workspaces?.map((ws: any) => (
          <Link
            key={ws.id}
            href={`/workspaces/${ws.id}/projects`}
            className="card hover-card group block p-6 h-full border border-border hover:border-primary-300 transition-all duration-200"
          >
            <div className="flex justify-between items-start mb-4">
              <div className="p-3 rounded-lg bg-primary-50 text-primary-600 group-hover:bg-primary-100 transition-colors">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>
              </div>
              <span className="badge badge-neutral text-xs">Owner</span>
            </div>
            <h3 className="text-xl font-semibold mb-2 group-hover:text-primary-700 transition-colors">{ws.name}</h3>
            <p className="text-sm text-text-tertiary">
              Created on {new Date(ws.createdAt).toLocaleDateString()}
            </p>
          </Link>
        ))}

        {/* Empty State */}
        {workspaces?.length === 0 && (
          <div className="col-span-full py-16 text-center border-2 border-dashed border-border rounded-xl bg-bg-tertiary/30">
            <div className="mx-auto w-16 h-16 bg-neutral-100 rounded-full flex items-center justify-center text-neutral-400 mb-4">
              <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>
            </div>
            <h3 className="text-xl font-medium text-text-primary mb-2">No workspaces yet</h3>
            <p className="text-text-secondary max-w-sm mx-auto mb-6">
              Create your first workspace to start managing projects and memories.
            </p>
            <button
              onClick={() => setIsModalOpen(true)}
              className="btn btn-secondary"
            >
              Create Workspace
            </button>
          </div>
        )}
      </div>

      {/* Create Workspace Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-neutral-900/50 backdrop-blur-sm">
          <div className="card w-full max-w-md shadow-2xl animate-in fade-in zoom-in duration-200">
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-2xl font-bold">Create Workspace</h2>
              <button
                onClick={() => setIsModalOpen(false)}
                className="text-text-tertiary hover:text-text-primary transition-colors"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
              </button>
            </div>

            <form onSubmit={handleCreateWorkspace}>
              <div className="mb-6">
                <label htmlFor="workspaceName" className="label">Workspace Name</label>
                <input
                  id="workspaceName"
                  type="text"
                  className="input"
                  placeholder="e.g., Personal, Work, Project X"
                  value={newWorkspaceName}
                  onChange={(e) => setNewWorkspaceName(e.target.value)}
                  autoFocus
                  required
                />
              </div>

              <div className="flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="btn btn-ghost"
                  disabled={isCreating}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isCreating || !newWorkspaceName.trim()}
                >
                  {isCreating ? (
                    <>
                      <Spinner size="sm" light className="mr-2 inline-block" />
                      Creating...
                    </>
                  ) : (
                    'Create Workspace'
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </main>
  );
}

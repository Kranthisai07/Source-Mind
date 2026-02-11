"use client";

import useSWR from 'swr';
import Skeleton from '@/components/Skeleton';
import toast from 'react-hot-toast';
import api from '../../../../lib/api';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useState } from 'react';

const fetcher = (url: string) => api.get(url).then((res) => res.data.data);

export default function ProjectsPage() {
  const params = useParams();
  const workspaceId = params?.wid as string;

  // Use the interceptor-based api client, no need to pass token manually if logged in
  const { data: projects, error, mutate } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/projects` : null,
    fetcher
  );

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [isCreating, setIsCreating] = useState(false);

  const createProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    setIsCreating(true);
    try {
      await api.post(`/workspaces/${workspaceId}/projects`, { name, description });
      setName('');
      setDescription('');
      setIsModalOpen(false);
      mutate();
      toast.success('Project created successfully');
    } catch (err: any) {
      console.error('Failed to create project', err);
      toast.error('Failed to create project');
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <main className="container mx-auto p-8 max-w-5xl">
      <div className="flex justify-between items-center mb-8">
        <div>
          <Link
            href="/workspaces"
            className="text-sm text-text-tertiary hover:text-primary mb-2 inline-flex items-center gap-1 transition-colors"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 19l-7-7 7-7" /></svg>
            Back to Workspaces
          </Link>
          <h1 className="text-3xl font-bold text-primary">Projects</h1>
          <p className="text-text-secondary">Manage projects in this workspace</p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="btn btn-primary"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
          New Project
        </button>
      </div>

      {error && (
        <div className="alert alert-error mb-6">
          Failed to load projects. Please try again.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {/* Loading State */}
        {!projects && !error && (
          <>
            {[1, 2, 3].map((i) => (
              <div key={`skeleton-${i}`} className="card p-6 h-full border border-border">
                <div className="flex justify-between items-start mb-4">
                  <Skeleton variant="rectangular" width={40} height={40} className="rounded-lg" />
                </div>
                <Skeleton variant="text" width="60%" height={24} className="mb-2" />
                <Skeleton variant="text" width="100%" />
                <Skeleton variant="text" width="80%" className="mt-1" />
              </div>
            ))}
          </>
        )}

        {projects?.map((p: any) => (
          <Link
            key={p.id}
            href={`/workspaces/${workspaceId}/projects/${p.id}/memories`}
            className="card hover-card group block p-6 h-full border border-border hover:border-primary-300 transition-all duration-200"
          >
            <div className="flex justify-between items-start mb-4">
              <div className="p-3 rounded-lg bg-blue-50 text-blue-600 group-hover:bg-blue-100 transition-colors">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>
              </div>
            </div>
            <h3 className="text-xl font-semibold mb-2 group-hover:text-primary-700 transition-colors">{p.name}</h3>
            <p className="text-sm text-text-secondary line-clamp-2">{p.description || "No description provided."}</p>
          </Link>
        ))}

        {/* Empty State */}
        {projects?.length === 0 && (
          <div className="col-span-full py-16 text-center border-2 border-dashed border-border rounded-xl bg-bg-tertiary/30">
            <div className="mx-auto w-16 h-16 bg-neutral-100 rounded-full flex items-center justify-center text-neutral-400 mb-4">
              <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>
            </div>
            <h3 className="text-xl font-medium text-text-primary mb-2">No projects yet</h3>
            <p className="text-text-secondary max-w-sm mx-auto mb-6">Create a project to start organizing your memories.</p>
            <button
              onClick={() => setIsModalOpen(true)}
              className="btn btn-secondary"
            >
              Create Project
            </button>
          </div>
        )}
      </div>

      {/* Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-neutral-900/50 backdrop-blur-sm">
          <div className="card w-full max-w-md shadow-2xl animate-in fade-in zoom-in duration-200">
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-2xl font-bold">Create Project</h2>
              <button
                onClick={() => setIsModalOpen(false)}
                className="text-text-tertiary hover:text-text-primary transition-colors"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
              </button>
            </div>

            <form onSubmit={createProject}>
              <div className="space-y-4 mb-6">
                <div>
                  <label htmlFor="projectName" className="label">Project Name</label>
                  <input
                    id="projectName"
                    type="text"
                    className="input w-full"
                    placeholder="e.g. Website Redesign"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    autoFocus
                    required
                  />
                </div>
                <div>
                  <label htmlFor="projectDesc" className="label">Description</label>
                  <textarea
                    id="projectDesc"
                    className="input w-full min-h-[100px] py-2"
                    placeholder="Optional description..."
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </div>
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
                  disabled={isCreating || !name.trim()}
                >
                  {isCreating ? (
                    <>
                      <span className="spinner w-4 h-4 border-2"></span>
                      Creating...
                    </>
                  ) : (
                    'Create Project'
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

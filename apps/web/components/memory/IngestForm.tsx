'use client'

import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { FileText, Link2, Upload } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { useIngestMutation } from '@/lib/queries/useMemories'
import { useAppStore } from '@/lib/store/useAppStore'
import { cn } from '@/lib/utils'

const textSchema = z.object({
  content: z.string().min(10, 'Content must be at least 10 characters'),
  tags: z.string().optional(),
  source_type: z.string().optional(),
})

const urlSchema = z.object({
  url: z.string().url('Enter a valid URL'),
  tags: z.string().optional(),
  source_type: z.string().optional(),
})

type TextForm = z.infer<typeof textSchema>
type UrlForm = z.infer<typeof urlSchema>

const SOURCE_TYPES = ['text', 'url', 'pdf', 'code']

interface IngestFormProps {
  open: boolean
  onClose: () => void
  onJobCreated: (jobId: string) => void
}

export function IngestForm({ open, onClose, onJobCreated }: IngestFormProps) {
  const [tab, setTab] = useState<'text' | 'url'>('text')
  const { activeWorkspace } = useAppStore()
  const ingest = useIngestMutation()

  const textForm = useForm<TextForm>({ resolver: zodResolver(textSchema) })
  const urlForm = useForm<UrlForm>({ resolver: zodResolver(urlSchema) })

  async function submitText(values: TextForm) {
    if (!activeWorkspace) return
    const resp = await ingest.mutateAsync({
      content: values.content,
      workspace_id: activeWorkspace.id,
      tags: values.tags ? values.tags.split(',').map((t) => t.trim()).filter(Boolean) : [],
      source_type: values.source_type || 'text',
    })
    onJobCreated(resp.job_id)
    onClose()
    textForm.reset()
  }

  async function submitUrl(values: UrlForm) {
    if (!activeWorkspace) return
    const resp = await ingest.mutateAsync({
      url: values.url,
      workspace_id: activeWorkspace.id,
      tags: values.tags ? values.tags.split(',').map((t) => t.trim()).filter(Boolean) : [],
      source_type: 'url',
    })
    onJobCreated(resp.job_id)
    onClose()
    urlForm.reset()
  }

  const tabs = [
    { id: 'text', label: 'Text', icon: FileText },
    { id: 'url',  label: 'URL',  icon: Link2 },
  ] as const

  return (
    <Modal open={open} onOpenChange={(v) => !v && onClose()} title="Ingest Knowledge" size="md">
      <div className="p-6">
        {/* Tab bar */}
        <div className="flex gap-1 p-1 bg-bg rounded-lg mb-6">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={cn(
                'flex-1 flex items-center justify-center gap-2 text-sm py-2 rounded-md transition-all duration-150',
                tab === id
                  ? 'bg-elevated text-primary'
                  : 'text-secondary hover:text-primary'
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>

        {tab === 'text' && (
          <form onSubmit={textForm.handleSubmit(submitText)} className="space-y-4">
            <div>
              <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
                Content
              </label>
              <textarea
                {...textForm.register('content')}
                className="w-full bg-bg border border-border rounded-lg p-3 text-sm text-primary placeholder-muted focus:border-accent-blue focus:outline-none resize-none transition-colors"
                rows={6}
                placeholder="Paste text, notes, documentation, decisions..."
              />
              {textForm.formState.errors.content && (
                <p className="text-accent-red text-xs mt-1">{textForm.formState.errors.content.message}</p>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
                  Tags (comma-separated)
                </label>
                <input
                  {...textForm.register('tags')}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-primary placeholder-muted focus:border-accent-blue focus:outline-none transition-colors"
                  placeholder="api, backend, auth"
                />
              </div>
              <div>
                <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
                  Memory Type
                </label>
                <select
                  {...textForm.register('source_type')}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-primary focus:border-accent-blue focus:outline-none transition-colors"
                >
                  <option value="">Auto-detect</option>
                  {SOURCE_TYPES.map((t) => (
                    <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="flex gap-3 justify-end pt-2">
              <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
              <Button type="submit" loading={ingest.isPending}>Ingest Content</Button>
            </div>
          </form>
        )}

        {tab === 'url' && (
          <form onSubmit={urlForm.handleSubmit(submitUrl)} className="space-y-4">
            <div>
              <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
                URL
              </label>
              <input
                {...urlForm.register('url')}
                type="url"
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-primary placeholder-muted focus:border-accent-blue focus:outline-none transition-colors"
                placeholder="https://docs.example.com/api-reference"
              />
              {urlForm.formState.errors.url && (
                <p className="text-accent-red text-xs mt-1">{urlForm.formState.errors.url.message}</p>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
                  Tags
                </label>
                <input
                  {...urlForm.register('tags')}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-primary placeholder-muted focus:border-accent-blue focus:outline-none transition-colors"
                  placeholder="docs, v2, public"
                />
              </div>
              <div>
                <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
                  Memory Type
                </label>
                <select
                  {...urlForm.register('source_type')}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-primary focus:border-accent-blue focus:outline-none transition-colors"
                >
                  <option value="">Auto-detect</option>
                  {SOURCE_TYPES.map((t) => (
                    <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="flex gap-3 justify-end pt-2">
              <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
              <Button type="submit" loading={ingest.isPending}>Ingest URL</Button>
            </div>
          </form>
        )}
      </div>
    </Modal>
  )
}

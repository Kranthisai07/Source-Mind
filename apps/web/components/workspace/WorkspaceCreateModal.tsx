'use client'

import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { useCreateWorkspaceMutation } from '@/lib/queries/useWorkspaces'
import { useAppStore } from '@/lib/store/useAppStore'

const schema = z.object({
  name: z.string().min(2, 'Name must be at least 2 characters').max(80),
  slug: z
    .string()
    .min(2)
    .max(40)
    .regex(/^[a-z0-9-]+$/, 'Slug can only contain lowercase letters, numbers, and hyphens'),
  visibility: z.enum(['private', 'internal', 'public']),
})

type FormValues = z.infer<typeof schema>

interface WorkspaceCreateModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function WorkspaceCreateModal({ open, onOpenChange }: WorkspaceCreateModalProps) {
  const onClose = () => onOpenChange(false)
  const createMutation = useCreateWorkspaceMutation()
  const { setActiveWorkspace } = useAppStore()

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    reset,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { visibility: 'private' },
  })

  // Auto-generate slug from name
  const name = watch('name')

  function handleNameBlur() {
    const auto = name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 40)
    if (auto) setValue('slug', auto)
  }

  async function onSubmit(values: FormValues) {
    const ws = await createMutation.mutateAsync(values)
    setActiveWorkspace(ws)
    reset()
    onClose()
  }

  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Create workspace">
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <div>
          <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
            Name
          </label>
          <input
            {...register('name')}
            onBlur={handleNameBlur}
            placeholder="Engineering Team"
            className="w-full bg-bg border border-border rounded-lg px-3 py-2.5 text-sm text-primary placeholder-muted focus:border-accent-blue focus:outline-none"
          />
          {errors.name && <p className="text-xs text-[#EF4444] mt-1">{errors.name.message}</p>}
        </div>

        <div>
          <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
            Slug
          </label>
          <input
            {...register('slug')}
            placeholder="engineering-team"
            className="w-full bg-bg border border-border rounded-lg px-3 py-2.5 text-sm text-primary placeholder-muted font-mono focus:border-accent-blue focus:outline-none"
          />
          {errors.slug && <p className="text-xs text-[#EF4444] mt-1">{errors.slug.message}</p>}
        </div>

        <div>
          <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
            Visibility
          </label>
          <select
            {...register('visibility')}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2.5 text-sm text-primary focus:border-accent-blue focus:outline-none"
          >
            <option value="private">Private — only invited members</option>
            <option value="internal">Internal — all org members</option>
            <option value="public">Public — anyone with link</option>
          </select>
        </div>

        <div className="flex gap-3 pt-2">
          <Button type="submit" loading={createMutation.isPending}>
            Create workspace
          </Button>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </form>
    </Modal>
  )
}

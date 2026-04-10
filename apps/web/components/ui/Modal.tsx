'use client'

import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title?: string
  description?: string
  children: React.ReactNode
  className?: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
}

const sizeClasses = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
}

export function Modal({ open, onOpenChange, title, description, children, className, size = 'md' }: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50" />
        <Dialog.Content
          className={cn(
            'fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-full bg-elevated border border-border rounded-xl shadow-2xl max-h-[90vh] overflow-y-auto',
            sizeClasses[size],
            className
          )}
        >
          {(title || description) && (
            <div className="flex items-start justify-between p-6 border-b border-border">
              <div>
                {title && <Dialog.Title className="text-primary font-semibold text-base">{title}</Dialog.Title>}
                {description && <Dialog.Description className="text-secondary text-sm mt-1">{description}</Dialog.Description>}
              </div>
              <Dialog.Close asChild>
                <button className="text-muted hover:text-secondary transition-colors ml-4 mt-0.5">
                  <X className="w-4 h-4" />
                </button>
              </Dialog.Close>
            </div>
          )}
          <div className={cn(!title && !description && 'p-0')}>{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

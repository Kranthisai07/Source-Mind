import { SkeletonCard } from '@/components/ui/SkeletonCard'

export default function ConflictsLoading() {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="h-14 border-b border-border px-8 flex items-center">
        <div className="skeleton h-5 w-40 rounded" />
      </div>
      <div className="px-8 py-6 space-y-4">
        <div className="flex gap-1 border-b border-border pb-0">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="skeleton h-9 w-20 rounded-t" />
          ))}
        </div>
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      </div>
    </div>
  )
}

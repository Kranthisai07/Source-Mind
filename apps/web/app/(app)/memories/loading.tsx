import { SkeletonCard } from '@/components/ui/SkeletonCard'

export default function MemoriesLoading() {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="h-14 border-b border-border px-8 flex items-center">
        <div className="skeleton h-5 w-24 rounded" />
      </div>
      <div className="px-8 py-6 space-y-4">
        <div className="skeleton h-10 w-full rounded-lg" />
        <div className="grid grid-cols-2 gap-3">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      </div>
    </div>
  )
}

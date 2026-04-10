import { SkeletonCard, MetricCardSkeleton } from '@/components/ui/SkeletonCard'

export default function AppLoading() {
  return (
    <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
      {/* TopBar skeleton */}
      <div className="h-14 border-b border-border px-8 flex items-center gap-3">
        <div className="skeleton h-5 w-32 rounded" />
        <div className="skeleton h-3 w-24 rounded ml-2" />
      </div>
      {/* Content skeleton */}
      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => <MetricCardSkeleton key={i} />)}
        </div>
        <div className="grid grid-cols-2 gap-4">
          {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      </div>
    </div>
  )
}

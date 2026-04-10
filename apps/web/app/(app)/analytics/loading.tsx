import { SkeletonCard, MetricCardSkeleton } from '@/components/ui/SkeletonCard'

export default function AnalyticsLoading() {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="h-14 border-b border-border px-8 flex items-center">
        <div className="skeleton h-5 w-24 rounded" />
      </div>
      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => <MetricCardSkeleton key={i} />)}
        </div>
        <div className="grid grid-cols-3 gap-6">
          <div className="skeleton h-72 rounded-xl" />
          <div className="col-span-2 skeleton h-72 rounded-xl" />
        </div>
        <div className="skeleton h-64 rounded-xl" />
      </div>
    </div>
  )
}

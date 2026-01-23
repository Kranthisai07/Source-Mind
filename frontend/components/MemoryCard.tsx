import { FC } from 'react';

type MemoryCardProps = {
  title?: string;
  content: string;
  type: string;
  source?: 'human' | 'ai' | 'human_ai_mixed';
  importanceScore?: number;
  createdAt?: string;
  onClick?: () => void;
};

const MemoryCard: FC<MemoryCardProps> = ({
  title,
  content,
  type,
  source,
  importanceScore,
  createdAt,
  onClick,
}) => {
  // Get badge class based on memory type
  const getTypeBadgeClass = () => {
    switch (type.toLowerCase()) {
      case 'decision':
        return 'badge-primary';
      case 'note':
        return 'badge-neutral';
      case 'ai_output':
        return 'badge-secondary';
      case 'code':
        return 'badge-info';
      default:
        return 'badge-neutral';
    }
  };

  // Get source badge class
  const getSourceBadgeClass = () => {
    switch (source) {
      case 'human':
        return 'badge-human';
      case 'ai':
        return 'badge-ai';
      case 'human_ai_mixed':
        return 'badge-mixed';
      default:
        return 'badge-neutral';
    }
  };

  // Format type for display
  const formatType = (type: string) => {
    return type.replace(/_/g, ' ').toUpperCase();
  };

  return (
    <div className="card card-interactive" onClick={onClick}>
      {/* Header */}
      <div className="flex items-start justify-between mb-3 gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`badge ${getTypeBadgeClass()}`}>
            {formatType(type)}
          </span>
          {source && (
            <span className={`badge ${getSourceBadgeClass()}`}>
              {source === 'human_ai_mixed' ? 'Mixed' : source.toUpperCase()}
            </span>
          )}
        </div>
        {createdAt && (
          <span className="text-xs text-tertiary whitespace-nowrap">
            {createdAt}
          </span>
        )}
      </div>

      {/* Title */}
      <h3 className="text-lg font-semibold text-primary mb-2 truncate">
        {title || 'Untitled Memory'}
      </h3>

      {/* Content Preview */}
      <p className="text-sm text-secondary mb-3 line-clamp-3">
        {content.slice(0, 200)}
        {content.length > 200 ? '...' : ''}
      </p>

      {/* Footer */}
      {importanceScore !== undefined && (
        <div className="flex items-center gap-2 pt-3 border-t">
          <div className="flex items-center gap-1">
            <span className="text-xs text-tertiary">Importance:</span>
            <div className="flex items-center gap-1">
              {[...Array(5)].map((_, i) => (
                <div
                  key={i}
                  className={`w-2 h-2 rounded-full ${i < Math.round(importanceScore * 5)
                      ? 'bg-primary-500'
                      : 'bg-neutral-200'
                    }`}
                />
              ))}
            </div>
            <span className="text-xs font-medium text-primary ml-1">
              {(importanceScore * 100).toFixed(0)}%
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default MemoryCard;

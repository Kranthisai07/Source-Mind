import { FC } from 'react';

type MemoryCardProps = {
  title?: string;
  content: string;
  type: string;
  source?: 'human' | 'ai' | 'external_tool';
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
  // Get badge color based on memory type
  const getTypeBadgeColor = () => {
    switch (type.toLowerCase()) {
      case 'decision':
        return 'bg-blue-100 text-blue-700';
      case 'note':
        return 'bg-neutral-100 text-neutral-700';
      case 'ai_output':
        return 'bg-purple-100 text-purple-700';
      case 'code':
        return 'bg-green-100 text-green-700';
      case 'document':
        return 'bg-orange-100 text-orange-700';
      default:
        return 'bg-neutral-100 text-neutral-700';
    }
  };

  // Get source badge color
  const getSourceBadgeColor = () => {
    switch (source) {
      case 'human':
        return 'bg-blue-100 text-blue-700';
      case 'ai':
        return 'bg-purple-100 text-purple-700';
      case 'external_tool':
        return 'bg-cyan-100 text-cyan-700';
      default:
        return 'bg-neutral-100 text-neutral-700';
    }
  };

  // Format type for display
  const formatType = (type: string) => {
    return type.replace(/_/g, ' ').charAt(0).toUpperCase() + type.replace(/_/g, ' ').slice(1);
  };

  // Format source for display
  const formatSource = () => {
    if (source === 'external_tool') return 'Tool';
    if (source === 'ai') return 'AI';
    return 'User';
  };

  return (
    <div
      className="bg-white border border-neutral-200 rounded-xl p-6 hover:border-neutral-300 hover:shadow-sm transition cursor-pointer group"
      onClick={onClick}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium ${getTypeBadgeColor()}`}>
            {formatType(type)}
          </span>
          {source && (
            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium ${getSourceBadgeColor()}`}>
              {formatSource()}
            </span>
          )}
        </div>
        {createdAt && (
          <span className="text-xs text-neutral-500 whitespace-nowrap">
            {createdAt}
          </span>
        )}
      </div>

      {/* Title */}
      <h3 className="text-lg font-semibold text-neutral-900 mb-2 line-clamp-2 group-hover:text-primary-600 transition">
        {title || 'Untitled Memory'}
      </h3>

      {/* Content Preview */}
      <p className="text-sm text-neutral-600 mb-4 line-clamp-3 leading-relaxed">
        {content.slice(0, 200)}
        {content.length > 200 ? '...' : ''}
      </p>

      {/* Footer */}
      {importanceScore !== undefined && (
        <div className="flex items-center justify-between pt-4 border-t border-neutral-100">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-neutral-500">Importance</span>
            <div className="flex items-center gap-1">
              {[...Array(5)].map((_, i) => (
                <div
                  key={i}
                  className={`w-1.5 h-1.5 rounded-full ${i < Math.round(importanceScore * 5)
                    ? 'bg-primary-500'
                    : 'bg-neutral-200'
                    }`}
                />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default MemoryCard;

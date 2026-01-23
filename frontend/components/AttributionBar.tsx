import { FC } from 'react';

type Contributor = {
  id: string;
  name: string;
  type: 'human' | 'ai';
  percent: number;
};

type AttributionBarProps = {
  contributors: Contributor[];
  showDetails?: boolean;
  compact?: boolean;
};

const AttributionBar: FC<AttributionBarProps> = ({
  contributors,
  showDetails = true,
  compact = false,
}) => {
  // Get color based on contributor type
  const getColor = (type: 'human' | 'ai') => {
    return type === 'human' ? 'var(--color-human)' : 'var(--color-ai)';
  };

  // Sort contributors by percentage (descending)
  const sortedContributors = [...contributors].sort((a, b) => b.percent - a.percent);

  return (
    <div className="w-full">
      {/* Attribution Bar */}
      <div className="w-full border rounded-lg overflow-hidden flex h-6 mb-2 shadow-sm">
        {sortedContributors.map((contributor, idx) => (
          <div
            key={contributor.id || idx}
            title={`${contributor.name}: ${(contributor.percent * 100).toFixed(1)}%`}
            style={{
              width: `${contributor.percent * 100}%`,
              background: getColor(contributor.type),
            }}
            className="transition-all hover:opacity-80 cursor-help"
          />
        ))}
      </div>

      {/* Contributor Details */}
      {showDetails && sortedContributors.length > 0 && (
        <div className={`flex ${compact ? 'gap-2' : 'gap-3'} flex-wrap`}>
          {sortedContributors.map((contributor) => (
            <div
              key={contributor.id}
              className="flex items-center gap-2"
            >
              <div
                className="w-3 h-3 rounded-full"
                style={{ background: getColor(contributor.type) }}
              />
              <span className={`${compact ? 'text-xs' : 'text-sm'} text-secondary`}>
                <span className="font-medium text-primary">{contributor.name}</span>
                {' '}
                <span className="text-tertiary">
                  {(contributor.percent * 100).toFixed(1)}%
                </span>
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Summary Stats */}
      {!compact && sortedContributors.length > 0 && (
        <div className="flex gap-4 mt-3 pt-3 border-t text-xs text-tertiary">
          <div>
            <span className="font-medium">Human:</span>{' '}
            {(
              sortedContributors
                .filter((c) => c.type === 'human')
                .reduce((sum, c) => sum + c.percent, 0) * 100
            ).toFixed(1)}%
          </div>
          <div>
            <span className="font-medium">AI:</span>{' '}
            {(
              sortedContributors
                .filter((c) => c.type === 'ai')
                .reduce((sum, c) => sum + c.percent, 0) * 100
            ).toFixed(1)}%
          </div>
          <div>
            <span className="font-medium">Contributors:</span>{' '}
            {sortedContributors.length}
          </div>
        </div>
      )}
    </div>
  );
};

export default AttributionBar;

import { FC } from 'react';

type Contributor = {
  id: string;
  name: string;
  type: 'user' | 'tool';
  score?: number | null;
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
  // Filter for human users with positive scores
  const users = contributors
    .filter((c) => c.type === 'user' && typeof c.score === 'number' && c.score > 0)
    .sort((a, b) => (b.score || 0) - (a.score || 0));

  if (!users.length) return null;

  // Color palette for users
  const colors = [
    'bg-blue-500',
    'bg-indigo-500',
    'bg-sky-500',
    'bg-violet-500',
    'bg-cyan-500',
  ];

  return (
    <div className="w-full">
      {/* Visual Bar */}
      <div className="flex h-3 w-full rounded-full overflow-hidden bg-slate-100 dark:bg-slate-800">
        {users.map((u, i) => (
          <div
            key={u.id}
            className={`h-full ${colors[i % colors.length]}`}
            style={{ width: `${(u.score || 0) * 100}%` }}
            title={`${u.name}: ${Math.round((u.score || 0) * 100)}%`}
          />
        ))}
      </div>

      {/* Legend / Details */}
      {showDetails && (
        <div className={`mt-2 flex flex-wrap ${compact ? 'gap-2' : 'gap-4'}`}>
          {users.map((u, i) => (
            <div key={u.id} className="flex items-center gap-1.5">
              <span className={`w-2 h-2 rounded-full ${colors[i % colors.length]}`}></span>
              <span className={`${compact ? 'text-xs' : 'text-sm'} text-slate-600 dark:text-slate-400 font-medium`}>
                {u.name}
                <span className="ml-1 text-slate-400 dark:text-slate-500 font-normal">
                  {Math.round((u.score || 0) * 100)}%
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default AttributionBar;

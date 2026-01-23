import { FC } from 'react';

type MemoryCardProps = {
  title?: string;
  content: string;
  type: string;
  importanceScore?: number;
  onClick?: () => void;
};

const MemoryCard: FC<MemoryCardProps> = ({
  title,
  content,
  type,
  importanceScore,
  onClick,
}) => (
  <div className="card hover:border-slate-400 cursor-pointer" onClick={onClick}>
    <div className="text-xs uppercase text-slate-500">{type}</div>
    <div className="font-semibold">{title || 'Untitled'}</div>
    <div className="text-sm text-slate-700 whitespace-pre-wrap">
      {content.slice(0, 200)}
      {content.length > 200 ? '...' : ''}
    </div>
    {importanceScore !== undefined && (
      <div className="text-xs text-slate-500">Importance {importanceScore}</div>
    )}
  </div>
);

export default MemoryCard;

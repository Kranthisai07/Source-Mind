import { FC } from 'react';

type Segment = { label: string; percent: number; color?: string };

const AttributionBar: FC<{ segments: Segment[] }> = ({ segments }) => {
  return (
    <div className="w-full border rounded overflow-hidden flex h-4">
      {segments.map((seg, idx) => (
        <div
          key={idx}
          title={`${seg.label}: ${(seg.percent * 100).toFixed(0)}%`}
          style={{
            width: `${seg.percent * 100}%`,
            background: seg.color || '#0f172a',
          }}
        />
      ))}
    </div>
  );
};

export default AttributionBar;

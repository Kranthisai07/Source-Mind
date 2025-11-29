import { FC } from 'react';
import AttributionBar from './AttributionBar';

type SummaryEntry = {
  contributorId: string;
  contributorType: string;
  contributionPercent: number;
};

const ProjectDashboard: FC<{ summary: SummaryEntry[] }> = ({ summary }) => {
  return (
    <div className="card space-y-3">
      <div className="font-semibold">Attribution summary</div>
      <div className="space-y-2">
        {summary.map((s) => (
          <div key={s.contributorId} className="space-y-1">
            <div className="text-sm">
              {s.contributorType} · {s.contributorId}
            </div>
            <AttributionBar
              segments={[
                {
                  label: s.contributorId,
                  percent: Math.min(1, s.contributionPercent),
                  color: s.contributorType === 'ai' ? '#f59e0b' : '#0f172a',
                },
              ]}
            />
          </div>
        ))}
        {!summary.length && <div className="text-sm text-slate-500">No attribution data.</div>}
      </div>
    </div>
  );
};

export default ProjectDashboard;

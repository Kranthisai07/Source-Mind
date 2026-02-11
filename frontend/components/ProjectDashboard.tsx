import { FC } from 'react';
import AttributionBar from './AttributionBar';

type SummaryEntry = {
  contributorId: string;
  contributorType: 'user' | 'tool';
};

const ProjectDashboard: FC<{ summary: SummaryEntry[] }> = ({ summary }) => {
  return (
    <div className="card space-y-3">
      <div className="font-semibold">Collaborators</div>
      <div className="space-y-4">
        {summary.length > 0 ? (
          <AttributionBar
            contributors={summary.map(s => ({
              id: s.contributorId,
              name: s.contributorId, // Replace with name lookup if available
              type: s.contributorType
            }))}
          />
        ) : (
          <div className="text-sm text-slate-500">No attribution data.</div>
        )}
      </div>
    </div>
  );
};

export default ProjectDashboard;

import { FC } from 'react';

type Relation = { toMemoryId: string; relationType: string };

const RelationList: FC<{ relations: Relation[] }> = ({ relations }) => {
  if (!relations.length) return <div className="text-sm text-slate-500">No relations.</div>;
  return (
    <ul className="text-sm space-y-1">
      {relations.map((r, idx) => (
        <li key={idx} className="flex justify-between border-b pb-1">
          <span>{r.relationType}</span>
          <span className="text-slate-500">{r.toMemoryId}</span>
        </li>
      ))}
    </ul>
  );
};

export default RelationList;

import { FC } from 'react';

type Edit = {
  editorId: string;
  editorType: string;
  deltaSummary?: string;
  createdAt?: string;
};

const EditHistoryList: FC<{ edits: Edit[] }> = ({ edits }) => {
  if (!edits.length) return <div className="text-sm text-slate-500">No edits yet.</div>;
  return (
    <div className="space-y-2">
      {edits.map((e, idx) => (
        <div key={idx} className="border rounded p-2 text-sm">
          <div className="text-slate-500">
            {e.editorType} · {e.editorId} ·{' '}
            {e.createdAt ? new Date(e.createdAt).toLocaleString() : ''}
          </div>
          <div>{e.deltaSummary || 'Edited content'}</div>
        </div>
      ))}
    </div>
  );
};

export default EditHistoryList;

import { FC } from 'react';

type Edit = {
  id?: string;
  editorId: string;
  editorType: 'user' | 'tool';
  editorName?: string;
  deltaSummary?: string;
  createdAt?: string;
};

type EditHistoryListProps = {
  edits: Edit[];
  showEmpty?: boolean;
};

const EditHistoryList: FC<EditHistoryListProps> = ({ edits, showEmpty = true }) => {
  if (!edits.length && showEmpty) {
    return (
      <div className="text-center py-8">
        <div className="text-4xl mb-2">📝</div>
        <p className="text-sm text-tertiary">No edit history yet</p>
      </div>
    );
  }

  if (!edits.length) return null;

  // Format date
  const formatDate = (dateString?: string) => {
    if (!dateString) return 'Unknown time';
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  };

  return (
    <div className="space-y-3">
      {edits.map((edit, idx) => (
        <div
          key={edit.id || idx}
          className="card p-4 hover:shadow-md transition-shadow"
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              {/* Editor Type Badge */}
              <span
                className={`badge ${edit.editorType === 'user' ? 'bg-blue-100 text-blue-700' : 'bg-purple-100 text-purple-700'
                  }`}
              >
                {edit.editorType === 'user' ? '👤 User' : '🛠️ Tool'}
              </span>

              {/* Editor Name */}
              {edit.editorName && (
                <span className="text-sm font-medium text-primary">
                  {edit.editorName}
                </span>
              )}
            </div>

            {/* Timestamp */}
            <span className="text-xs text-tertiary">
              {formatDate(edit.createdAt)}
            </span>
          </div>

          {/* Delta Summary */}
          {edit.deltaSummary && (
            <p className="text-sm text-secondary">
              {edit.deltaSummary}
            </p>
          )}

          {!edit.deltaSummary && (
            <p className="text-sm text-tertiary italic">
              Content was edited
            </p>
          )}
        </div>
      ))}
    </div>
  );
};

export default EditHistoryList;

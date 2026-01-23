import { FC } from 'react';

type Message = { role: string; content: string };

const ChatPanel: FC<{ messages: Message[] }> = ({ messages }) => {
  return (
    <div className="space-y-2">
      {messages.map((m, idx) => (
        <div key={idx} className="p-2 border rounded">
          <div className="text-xs uppercase text-slate-500">{m.role}</div>
          <div>{m.content}</div>
        </div>
      ))}
    </div>
  );
};

export default ChatPanel;

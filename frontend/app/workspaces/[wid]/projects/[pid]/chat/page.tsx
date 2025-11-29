"use client";

import { useEffect, useState } from 'react';
import api from '../../../../../../lib/api';
import { useParams } from 'next/navigation';

interface Message {
  role: string;
  content: string;
}

export default function ChatPage() {
  const params = useParams();
  const workspaceId = params?.wid as string;
  const projectId = params?.pid as string;
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [suggested, setSuggested] = useState<any[]>([]);
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') ?? '' : '';

  useEffect(() => {
    const createConversation = async () => {
      const res = await api.post(
        `/workspaces/${workspaceId}/conversations`,
        { projectId },
        { headers: { Authorization: `Bearer ${token}` } },
      );
      setConversationId(res.data.data.id);
    };
    createConversation();
  }, [workspaceId, projectId, token]);

  const sendMessage = async () => {
    if (!input || !conversationId) return;
    const userMessage = { role: 'user', content: input };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    const res = await api.post(
      `/workspaces/${workspaceId}/conversations/${conversationId}/messages`,
      { role: 'user', content: input },
      { headers: { Authorization: `Bearer ${token}` } },
    );
    setSuggested(res.data.data.assistantSuggestedContext || []);
  };

  return (
    <main className="p-8 space-y-4">
      <h1 className="text-2xl font-semibold">Project chat</h1>
      <div className="card space-y-3">
        <div className="min-h-[300px] space-y-2">
          {messages.map((m, idx) => (
            <div key={idx} className="p-2 rounded border">
              <div className="text-xs uppercase text-slate-500">{m.role}</div>
              <div>{m.content}</div>
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            className="flex-1 border rounded px-3 py-2"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask or add context..."
          />
          <button className="bg-slate-900 text-white rounded px-4" onClick={sendMessage}>
            Send
          </button>
        </div>
      </div>
      <div className="card">
        <div className="font-medium mb-2">Suggested context</div>
        <div className="space-y-2">
          {suggested.map((item, idx) => (
            <div key={idx} className="border rounded p-2">
              <div className="text-xs text-slate-500">Score: {item.score.toFixed(2)}</div>
              <div className="font-semibold">{item.memory.title}</div>
              <div className="text-sm text-slate-700 whitespace-pre-wrap">
                {item.memory.content.slice(0, 200)}
              </div>
              <div className="text-xs text-slate-500">
                Related: {item.relatedMemoryIds.join(', ')}
              </div>
            </div>
          ))}
          {!suggested.length && <div className="text-sm text-slate-500">No context yet.</div>}
        </div>
      </div>
    </main>
  );
}

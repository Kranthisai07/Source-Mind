"use client";

import { useState } from 'react';
import api from '../../lib/api';
import { useRouter } from 'next/navigation';

export default function RegisterPage() {
  const router = useRouter();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const res = await api.post('/auth/register', { name, email, password });
      localStorage.setItem('token', res.data.data.token);
      router.push('/workspaces');
    } catch (err: any) {
      setError(err.response?.data?.message || 'Registration failed');
    }
  };

  return (
    <main className="flex items-center justify-center min-h-screen">
      <form onSubmit={submit} className="card w-full max-w-md space-y-3">
        <h1 className="text-2xl font-semibold">Create account</h1>
        <input
          className="w-full border rounded px-3 py-2"
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="w-full border rounded px-3 py-2"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          className="w-full border rounded px-3 py-2"
          placeholder="Password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <button className="w-full bg-slate-900 text-white rounded px-3 py-2" type="submit">
          Register
        </button>
      </form>
    </main>
  );
}

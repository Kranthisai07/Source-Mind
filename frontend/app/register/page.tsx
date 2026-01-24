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
      // Backend returns { success: true, data: { accessToken, refreshToken, user } }
      localStorage.setItem('token', res.data.data.accessToken);
      localStorage.setItem('refreshToken', res.data.data.refreshToken);
      router.push('/workspaces');
    } catch (err: any) {
      console.error('Registration error:', err.response?.data);
      setError(err.response?.data?.message || 'Registration failed');
    }
  };

  return (
    <main className="flex items-center justify-center min-h-screen bg-bg-secondary">
      <div className="card w-full max-w-md p-8 shadow-lg">
        <h1 className="text-3xl font-bold mb-6 text-center text-primary">Create Account</h1>

        <form onSubmit={submit} className="space-y-5">
          <div className="space-y-2">
            <label className="text-sm font-medium text-text-secondary" htmlFor="name">Full Name</label>
            <input
              id="name"
              className="input w-full"
              placeholder="Enter your name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium text-text-secondary" htmlFor="email">Email Address</label>
            <input
              id="email"
              className="input w-full"
              placeholder="name@example.com"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium text-text-secondary" htmlFor="password">Password</label>
            <input
              id="password"
              className="input w-full"
              placeholder="Create a password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          {error && (
            <div className="p-3 rounded bg-red-50 text-red-600 text-sm border border-red-100">
              {error}
            </div>
          )}

          <button
            className="btn btn-primary w-full py-3 font-semibold text-lg"
            type="submit"
          >
            Sign Up
          </button>

          <p className="text-center text-sm text-text-tertiary mt-4">
            Already have an account?{' '}
            <a href="/login" className="text-primary hover:underline font-medium">Log in</a>
          </p>
        </form>
      </div>
    </main>
  );
}

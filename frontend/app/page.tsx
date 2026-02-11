'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

export default function Home() {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  return (
    <main className="min-h-screen bg-white">
      {/* Navigation */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-white/80 backdrop-blur-xl border-b border-neutral-200">
        <div className="max-w-7xl mx-auto px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            {/* Logo */}
            <Link href="/" className="flex items-center gap-2 group">
              <div className="w-8 h-8 bg-gradient-to-br from-primary-600 to-secondary-600 rounded-lg flex items-center justify-center">
                <span className="text-white text-lg font-bold">S</span>
              </div>
              <span className="text-lg font-semibold text-neutral-900">SourceMind</span>
            </Link>

            {/* Nav Links */}
            <div className="hidden md:flex items-center gap-8">
              <Link href="#features" className="text-sm font-medium text-neutral-600 hover:text-neutral-900 transition">
                Features
              </Link>
              <Link href="#pricing" className="text-sm font-medium text-neutral-600 hover:text-neutral-900 transition">
                Pricing
              </Link>
              <Link href="#docs" className="text-sm font-medium text-neutral-600 hover:text-neutral-900 transition">
                Docs
              </Link>
            </div>

            {/* CTA */}
            <div className="flex items-center gap-3">
              <Link
                href="/login"
                className="text-sm font-medium text-neutral-700 hover:text-neutral-900 transition px-4 py-2"
              >
                Sign in
              </Link>
              <Link
                href="/register"
                className="text-sm font-medium bg-neutral-900 text-white px-4 py-2 rounded-lg hover:bg-neutral-800 transition"
              >
                Get started
              </Link>
            </div>
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="pt-32 pb-20 px-6 lg:px-8">
        <div className="max-w-7xl mx-auto">
          <div className="max-w-3xl mx-auto text-center">
            {/* Badge */}
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-neutral-100 border border-neutral-200 mb-8">
              <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
              <span className="text-sm font-medium text-neutral-700">Now in Beta</span>
            </div>

            {/* Headline */}
            <h1 className="text-5xl lg:text-6xl font-bold text-neutral-900 tracking-tight mb-6 leading-tight">
              Team memory that knows{' '}
              <span className="bg-gradient-to-r from-primary-600 to-secondary-600 bg-clip-text text-transparent">
                who contributed what
              </span>
            </h1>

            {/* Subheadline */}
            <p className="text-xl text-neutral-600 mb-10 leading-relaxed max-w-2xl mx-auto">
              The only knowledge platform that tracks contributions with AI-powered attribution.
              Know what your team knows, who built it, and how it evolved.
            </p>

            {/* CTA Buttons */}
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-12">
              <Link
                href="/register"
                className="w-full sm:w-auto px-8 py-3.5 bg-neutral-900 text-white rounded-lg font-medium hover:bg-neutral-800 transition shadow-sm"
              >
                Start free trial
              </Link>
              <Link
                href="/login"
                className="w-full sm:w-auto px-8 py-3.5 bg-white text-neutral-900 rounded-lg font-medium border border-neutral-300 hover:border-neutral-400 transition"
              >
                View demo
              </Link>
            </div>

            {/* Trust Indicators */}
            <div className="flex flex-wrap items-center justify-center gap-6 text-sm text-neutral-500">
              <div className="flex items-center gap-2">
                <svg className="w-4 h-4 text-green-600" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                <span>Free 14-day trial</span>
              </div>
              <div className="flex items-center gap-2">
                <svg className="w-4 h-4 text-green-600" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                <span>No credit card required</span>
              </div>
              <div className="flex items-center gap-2">
                <svg className="w-4 h-4 text-green-600" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                <span>Cancel anytime</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Stats Section */}
      <section className="py-16 px-6 lg:px-8 border-y border-neutral-200 bg-neutral-50">
        <div className="max-w-7xl mx-auto">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-8">
            <div className="text-center">
              <div className="text-4xl font-bold text-neutral-900 mb-2">10x</div>
              <div className="text-sm text-neutral-600">Faster knowledge retrieval</div>
            </div>
            <div className="text-center">
              <div className="text-4xl font-bold text-neutral-900 mb-2">100%</div>
              <div className="text-sm text-neutral-600">Attribution accuracy</div>
            </div>
            <div className="text-center">
              <div className="text-4xl font-bold text-neutral-900 mb-2">500+</div>
              <div className="text-sm text-neutral-600">Teams using daily</div>
            </div>
            <div className="text-center">
              <div className="text-4xl font-bold text-neutral-900 mb-2">99.9%</div>
              <div className="text-sm text-neutral-600">Uptime guarantee</div>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-24 px-6 lg:px-8" id="features">
        <div className="max-w-7xl mx-auto">
          {/* Section Header */}
          <div className="max-w-3xl mb-16">
            <h2 className="text-3xl lg:text-4xl font-bold text-neutral-900 mb-4">
              Everything you need to manage team knowledge
            </h2>
            <p className="text-lg text-neutral-600">
              Built for modern teams who value transparency, collaboration, and intelligent attribution.
            </p>
          </div>

          {/* Features Grid */}
          <div className="grid lg:grid-cols-3 gap-8">
            {/* Feature 1 */}
            <div className="group">
              <div className="w-12 h-12 bg-primary-100 rounded-lg flex items-center justify-center mb-4 group-hover:bg-primary-200 transition">
                <svg className="w-6 h-6 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <h3 className="text-xl font-semibold text-neutral-900 mb-2">Attribution Tracking</h3>
              <p className="text-neutral-600 leading-relaxed">
                Automatically track who contributed what. Distinguish between human and AI contributions with precision.
              </p>
            </div>

            {/* Feature 2 */}
            <div className="group">
              <div className="w-12 h-12 bg-secondary-100 rounded-lg flex items-center justify-center mb-4 group-hover:bg-secondary-200 transition">
                <svg className="w-6 h-6 text-secondary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
              </div>
              <h3 className="text-xl font-semibold text-neutral-900 mb-2">AI-Powered Search</h3>
              <p className="text-neutral-600 leading-relaxed">
                Semantic vector search finds relevant information even when you don't know the exact words.
              </p>
            </div>

            {/* Feature 3 */}
            <div className="group">
              <div className="w-12 h-12 bg-accent-100 rounded-lg flex items-center justify-center mb-4 group-hover:bg-accent-200 transition">
                <svg className="w-6 h-6 text-accent-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                </svg>
              </div>
              <h3 className="text-xl font-semibold text-neutral-900 mb-2">Team Workspaces</h3>
              <p className="text-neutral-600 leading-relaxed">
                Organize knowledge by workspace and project. Role-based access control keeps data secure.
              </p>
            </div>

            {/* Feature 4 */}
            <div className="group">
              <div className="w-12 h-12 bg-primary-100 rounded-lg flex items-center justify-center mb-4 group-hover:bg-primary-200 transition">
                <svg className="w-6 h-6 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
              </div>
              <h3 className="text-xl font-semibold text-neutral-900 mb-2">Knowledge Graph</h3>
              <p className="text-neutral-600 leading-relaxed">
                Visualize how ideas connect and decisions evolve over time with interactive relationship mapping.
              </p>
            </div>

            {/* Feature 5 */}
            <div className="group">
              <div className="w-12 h-12 bg-secondary-100 rounded-lg flex items-center justify-center mb-4 group-hover:bg-secondary-200 transition">
                <svg className="w-6 h-6 text-secondary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <h3 className="text-xl font-semibold text-neutral-900 mb-2">Version History</h3>
              <p className="text-neutral-600 leading-relaxed">
                Full version control with AI-generated summaries of what changed and why. Never lose context.
              </p>
            </div>

            {/* Feature 6 */}
            <div className="group">
              <div className="w-12 h-12 bg-accent-100 rounded-lg flex items-center justify-center mb-4 group-hover:bg-accent-200 transition">
                <svg className="w-6 h-6 text-accent-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
                </svg>
              </div>
              <h3 className="text-xl font-semibold text-neutral-900 mb-2">Knowledge Handoff</h3>
              <p className="text-neutral-600 leading-relaxed">
                Seamlessly transfer knowledge when team members transition. Zero knowledge loss guaranteed.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-20 px-6 lg:px-8 bg-neutral-900">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-3xl lg:text-4xl font-bold text-white mb-4">
            Ready to transform your team's knowledge management?
          </h2>
          <p className="text-lg text-neutral-400 mb-8">
            Join innovative teams already using SourceMind to track, attribute, and preserve their collective knowledge.
          </p>
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <Link
              href="/register"
              className="w-full sm:w-auto px-8 py-3.5 bg-white text-neutral-900 rounded-lg font-medium hover:bg-neutral-100 transition"
            >
              Start free trial
            </Link>
            <Link
              href="/login"
              className="w-full sm:w-auto px-8 py-3.5 bg-neutral-800 text-white rounded-lg font-medium border border-neutral-700 hover:bg-neutral-700 transition"
            >
              Sign in
            </Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 px-6 lg:px-8 border-t border-neutral-200">
        <div className="max-w-7xl mx-auto">
          <div className="flex flex-col md:flex-row justify-between items-center gap-6">
            {/* Logo */}
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-gradient-to-br from-primary-600 to-secondary-600 rounded-lg flex items-center justify-center">
                <span className="text-white text-lg font-bold">S</span>
              </div>
              <span className="text-lg font-semibold text-neutral-900">SourceMind</span>
            </div>

            {/* Links */}
            <div className="flex flex-wrap gap-6 text-sm text-neutral-600">
              <Link href="/docs" className="hover:text-neutral-900 transition">Documentation</Link>
              <Link href="/about" className="hover:text-neutral-900 transition">About</Link>
              <Link href="/pricing" className="hover:text-neutral-900 transition">Pricing</Link>
              <Link href="/contact" className="hover:text-neutral-900 transition">Contact</Link>
              <Link href="/privacy" className="hover:text-neutral-900 transition">Privacy</Link>
              <Link href="/terms" className="hover:text-neutral-900 transition">Terms</Link>
            </div>
          </div>

          <div className="mt-8 pt-8 border-t border-neutral-200 text-center text-sm text-neutral-500">
            © 2026 SourceMind. All rights reserved.
          </div>
        </div>
      </footer>
    </main>
  );
}

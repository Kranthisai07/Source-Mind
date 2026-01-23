import Link from 'next/link';

export default function Home() {
  return (
    <main className="min-h-screen">
      {/* Hero Section */}
      <div className="relative overflow-hidden">
        {/* Gradient Background */}
        <div className="absolute inset-0 bg-gradient-to-br from-primary-50 via-secondary-50 to-accent-50 opacity-50"></div>

        {/* Content */}
        <div className="container relative">
          <div className="flex flex-col items-center justify-center min-h-screen text-center py-20">
            {/* Badge */}
            <div className="badge badge-primary mb-6 animate-fade-in">
              🚀 Now in Beta
            </div>

            {/* Heading */}
            <h1 className="text-5xl font-bold mb-6 animate-slide-up" style={{ maxWidth: '800px' }}>
              Collaborative Memory with{' '}
              <span className="text-primary-600">Attribution Intelligence</span>
            </h1>

            {/* Subheading */}
            <p className="text-xl text-secondary mb-8 animate-slide-up" style={{ maxWidth: '600px', animationDelay: '100ms' }}>
              The only team memory platform that tracks not just{' '}
              <strong>WHAT</strong> your team knows, but{' '}
              <strong>WHO</strong> contributed it,{' '}
              <strong>HOW</strong> it evolved, and{' '}
              <strong>WHY</strong> decisions were made.
            </p>

            {/* CTA Buttons */}
            <div className="flex gap-4 mb-12 animate-slide-up" style={{ animationDelay: '200ms' }}>
              <Link href="/register" className="btn btn-primary btn-lg">
                Get Started Free
              </Link>
              <Link href="/login" className="btn btn-outline btn-lg">
                Sign In
              </Link>
            </div>

            {/* Feature Pills */}
            <div className="flex flex-wrap gap-3 justify-center animate-fade-in" style={{ animationDelay: '300ms' }}>
              <span className="badge badge-neutral">
                🧠 AI-Powered Memory
              </span>
              <span className="badge badge-neutral">
                👥 Team Collaboration
              </span>
              <span className="badge badge-neutral">
                📊 Attribution Tracking
              </span>
              <span className="badge badge-neutral">
                🔍 Semantic Search
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Features Section */}
      <div className="container py-20">
        <h2 className="text-3xl font-bold text-center mb-12">
          Why SourceMind?
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {/* Feature 1 */}
          <div className="card">
            <div className="text-4xl mb-4">🎯</div>
            <h3 className="card-title">Attribution Tracking</h3>
            <p className="card-description">
              Know exactly who contributed what. Track human vs AI contributions with precision.
            </p>
          </div>

          {/* Feature 2 */}
          <div className="card">
            <div className="text-4xl mb-4">🔗</div>
            <h3 className="card-title">Knowledge Graph</h3>
            <p className="card-description">
              Connect ideas with relationships. Visualize how decisions evolved over time.
            </p>
          </div>

          {/* Feature 3 */}
          <div className="card">
            <div className="text-4xl mb-4">🤖</div>
            <h3 className="card-title">AI-Powered Search</h3>
            <p className="card-description">
              Semantic vector search finds relevant memories even when you don't know the exact words.
            </p>
          </div>

          {/* Feature 4 */}
          <div className="card">
            <div className="text-4xl mb-4">👥</div>
            <h3 className="card-title">Team Workspaces</h3>
            <p className="card-description">
              Organize knowledge by workspace and project. Role-based access control included.
            </p>
          </div>

          {/* Feature 5 */}
          <div className="card">
            <div className="text-4xl mb-4">📝</div>
            <h3 className="card-title">Edit History</h3>
            <p className="card-description">
              Full version control with AI-generated summaries of what changed and why.
            </p>
          </div>

          {/* Feature 6 */}
          <div className="card">
            <div className="text-4xl mb-4">🔄</div>
            <h3 className="card-title">Knowledge Handoff</h3>
            <p className="card-description">
              Seamlessly transfer knowledge when team members transition or leave.
            </p>
          </div>
        </div>
      </div>

      {/* CTA Section */}
      <div className="bg-gradient-to-r from-primary-600 to-secondary-600 text-white py-20">
        <div className="container text-center">
          <h2 className="text-4xl font-bold mb-4">
            Ready to Transform Your Team's Memory?
          </h2>
          <p className="text-xl mb-8 opacity-90">
            Join teams already using SourceMind to track, attribute, and preserve their knowledge.
          </p>
          <Link href="/register" className="btn btn-lg" style={{ backgroundColor: 'white', color: 'var(--color-primary-600)' }}>
            Start Free Trial
          </Link>
        </div>
      </div>

      {/* Footer */}
      <footer className="border-t py-8">
        <div className="container">
          <div className="flex flex-col md:flex-row justify-between items-center gap-4">
            <div className="text-sm text-tertiary">
              © 2026 SourceMind. All rights reserved.
            </div>
            <div className="flex gap-6 text-sm">
              <Link href="/docs" className="text-secondary hover:text-primary">
                Documentation
              </Link>
              <Link href="/about" className="text-secondary hover:text-primary">
                About
              </Link>
              <Link href="/contact" className="text-secondary hover:text-primary">
                Contact
              </Link>
            </div>
          </div>
        </div>
      </footer>
    </main>
  );
}

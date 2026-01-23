import './globals.css';
import { ReactNode } from 'react';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'SourceMind - Collaborative Memory with Attribution Intelligence',
  description: 'The only team memory platform that tracks not just WHAT your team knows, but WHO contributed it, HOW it evolved, and WHY decisions were made.',
  keywords: ['team collaboration', 'knowledge management', 'attribution tracking', 'AI collaboration', 'memory platform'],
  authors: [{ name: 'SourceMind Team' }],
  viewport: 'width=device-width, initial-scale=1',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
    { media: '(prefers-color-scheme: dark)', color: '#020617' },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-theme="light">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <div className="min-h-screen">{children}</div>
      </body>
    </html>
  );
}

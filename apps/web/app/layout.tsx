import { ClerkProvider } from '@clerk/nextjs'
import { Fraunces } from 'next/font/google'
import type { Metadata } from 'next'
import { Providers } from '@/lib/providers'
import './globals.css'

const fraunces = Fraunces({
  subsets: ['latin'],
  weight: ['300', '400', '500', '600'],
  variable: '--font-display',
  display: 'swap',
})

export const metadata: Metadata = {
  title: { default: 'SourceMind', template: '%s · SourceMind' },
  description: 'Collaborative team memory — track what your team knows, who contributed it, and why.',
  icons: { icon: '/favicon.svg' },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${fraunces.variable}`}>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body className="bg-bg text-primary antialiased">
        <ClerkProvider
          appearance={{
            variables: {
              colorBackground: '#111118',
              colorText: '#E8E8F0',
              colorPrimary: '#4F8EF7',
              colorInputBackground: '#0A0A0F',
              colorInputText: '#E8E8F0',
            },
          }}
        >
          <Providers>{children}</Providers>
        </ClerkProvider>
      </body>
    </html>
  )
}

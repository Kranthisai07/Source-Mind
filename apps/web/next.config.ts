import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: '/v1/:path*',
        destination: `${process.env.API_URL ?? 'http://localhost:8000'}/v1/:path*`,
      },
      {
        source: '/health',
        destination: `${process.env.API_URL ?? 'http://localhost:8000'}/health`,
      },
    ]
  },
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'img.clerk.com' },
      { protocol: 'https', hostname: '**.clerk.accounts.dev' },
    ],
  },
}

export default nextConfig

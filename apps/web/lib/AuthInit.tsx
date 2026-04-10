'use client'

import { useAuth } from '@clerk/nextjs'
import { useEffect } from 'react'
import { setTokenGetter } from '@/lib/api/client'

/**
 * Injects the Clerk token getter into the axios client.
 * Must be rendered inside ClerkProvider.
 */
export function AuthInit() {
  const { getToken } = useAuth()

  useEffect(() => {
    setTokenGetter(() => getToken())
  }, [getToken])

  return null
}

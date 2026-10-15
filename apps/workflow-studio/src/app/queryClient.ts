/**
 * The one QueryClient, hoisted so route `beforeLoad` redirects can use it (UI-DESIGN §2.3, slice 0).
 */
import { QueryClient } from '@tanstack/react-query'
import { ApiError } from '@/shared/api/http'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000,
      // 4xx are answers, not glitches: never retry them (UI-INTEGRATION §3.6).
      retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 1,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
})

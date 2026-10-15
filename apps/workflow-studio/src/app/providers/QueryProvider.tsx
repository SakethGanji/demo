/**
 * React Query Provider Component
 *
 * Wraps the application with the hoisted QueryClient (`app/queryClient.ts`), which the
 * router also receives as context for `beforeLoad` lookups.
 */

import { type ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from '../queryClient';

interface QueryProviderProps {
  children: ReactNode;
}

export function QueryProvider({ children }: QueryProviderProps) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

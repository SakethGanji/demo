import { EmptyState } from '@/shared/components/ui/empty-state';
import { Database } from 'lucide-react';

/** Centered empty block for the NDV input/output data wells: icon chip, title, optional hint. */
export function DataEmpty({ title, hint }: { title: string; hint?: string }) {
  return (
    <EmptyState compact className="h-full" icon={<Database />} title={title}>
      {hint}
    </EmptyState>
  );
}

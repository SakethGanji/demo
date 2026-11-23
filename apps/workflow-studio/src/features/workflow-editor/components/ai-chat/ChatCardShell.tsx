import type { HTMLAttributes } from 'react';
import { cn } from '@/shared/lib/utils';

/** The recessed card every assistant chat card sits in (spec, proposal, template suggestion). */
export function ChatCardShell({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('rounded-ctl bg-recess p-2.5 text-[12.5px]', className)} {...props} />;
}

/**
 * Table primitive (shadcn API; 34px header, 42px rows, `dense` 32/38px), UI-DESIGN §7.1.
 */
import * as React from 'react'
import { cn } from '@/shared/lib/utils'

const DenseContext = React.createContext(false)

function Table({ className, dense = false, ...props }: React.ComponentProps<'table'> & { dense?: boolean }) {
  return (
    <DenseContext.Provider value={dense}>
      <table data-slot="table" className={cn('w-full caption-bottom border-collapse text-[13px]', className)} {...props} />
    </DenseContext.Provider>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<'thead'>) {
  return <thead data-slot="table-header" className={cn('[&_tr]:border-b [&_tr]:border-border', className)} {...props} />
}

function TableBody({ className, ...props }: React.ComponentProps<'tbody'>) {
  return <tbody data-slot="table-body" className={cn('[&_tr:last-child]:border-0', className)} {...props} />
}

function TableRow({ className, ...props }: React.ComponentProps<'tr'>) {
  return (
    <tr
      data-slot="table-row"
      className={cn('border-b border-border/65 transition-colors hover:bg-muted/50 data-[state=selected]:bg-muted', className)}
      {...props}
    />
  )
}

function TableHead({ className, ...props }: React.ComponentProps<'th'>) {
  const dense = React.useContext(DenseContext)
  return (
    <th
      data-slot="table-head"
      className={cn(
        'whitespace-nowrap px-4 text-left align-middle text-[11.5px] font-medium text-muted-foreground',
        dense ? 'h-8 px-3' : 'h-[34px]',
        className,
      )}
      {...props}
    />
  )
}

function TableCell({ className, ...props }: React.ComponentProps<'td'>) {
  const dense = React.useContext(DenseContext)
  return (
    <td
      data-slot="table-cell"
      className={cn('whitespace-nowrap px-4 align-middle', dense ? 'h-[38px] px-3' : 'h-[42px]', className)}
      {...props}
    />
  )
}

export { Table, TableHeader, TableBody, TableRow, TableHead, TableCell }

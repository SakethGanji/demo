/** Show/hide columns in the grid: client-only, remembered per table (`lib/hiddenColumns`). Not the schema editor. */
import { Columns3 } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuGroup, DropdownMenuLabel, DropdownMenuTrigger } from '@/shared/components/ui/dropdown-menu'

export interface ColumnOption {
  name: string
  sub?: string
}

export function ColumnsMenu({ options, hidden, onToggle }: { options: ColumnOption[]; hidden: string[]; onToggle: (name: string) => void }) {
  const visible = options.length - hidden.length
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" size="sm" className="text-muted-foreground hover:text-foreground" data-testid="data-columns-menu">
            <Columns3 /> Columns
            {hidden.length > 0 && <span className="font-normal text-faint">· {hidden.length} hidden</span>}
          </Button>
        }
      />
      <DropdownMenuContent align="end" className="min-w-56">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Show columns</DropdownMenuLabel>
          {options.map((o) => {
            const on = !hidden.includes(o.name)
            const last = on && visible === 1
            return (
              <DropdownMenuCheckboxItem
                key={o.name}
                checked={on}
                disabled={last}
                onCheckedChange={() => onToggle(o.name)}
                closeOnClick={false}
                data-testid={`columns-toggle-${o.name}`}
              >
                <span className="font-mono text-[12.5px]">{o.name}</span>
                {(last || o.sub) && <span className="ml-1.5 text-[11.5px] text-muted-foreground">{last ? 'last one shown' : o.sub}</span>}
              </DropdownMenuCheckboxItem>
            )
          })}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

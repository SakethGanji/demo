import { useState } from 'react';
import { Layers, Check, Settings } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu';
import { useEnvProfilesStore } from '../workflow-editor/stores/envProfilesStore';
import { useVariableEnvironments } from './useVariablesApi';
import { EnvVariablesModal } from './EnvVariablesModal';

// Fallback list when the API hasn't surfaced any envs yet (fresh DB / offline).
const FALLBACK_ENVIRONMENTS = ['default', 'dev', 'uat', 'prod'] as const;

export function EnvProfileSelector() {
  const activeEnvironment = useEnvProfilesStore((s) => s.activeEnvironment);
  const setActiveEnvironment = useEnvProfilesStore((s) => s.setActiveEnvironment);
  const [manageOpen, setManageOpen] = useState(false);

  const { data: envsFromApi } = useVariableEnvironments();

  // Merge API-known envs with fallbacks; always include the active env so it's selectable.
  const envSet = new Set<string>(FALLBACK_ENVIRONMENTS);
  for (const e of envsFromApi ?? []) envSet.add(e);
  envSet.add(activeEnvironment);
  const options = [...envSet].sort();

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          title={`Workflow runs use variables from environment: ${activeEnvironment}`}
          className="h-7 px-2.5 flex items-center gap-1.5 rounded-md text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
        >
          <Layers size={12} />
          <span>{activeEnvironment}</span>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-44">
          <DropdownMenuGroup>
            <DropdownMenuLabel>Environment</DropdownMenuLabel>
            {options.map((env) => (
              <DropdownMenuItem
                key={env}
                onClick={() => setActiveEnvironment(env)}
                className="flex items-center justify-between gap-2"
              >
                <span>{env}</span>
                {env === activeEnvironment && <Check size={12} />}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem onClick={() => setManageOpen(true)}>
              <Settings size={12} className="mr-2" />
              Manage variables…
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <EnvVariablesModal
        open={manageOpen}
        onOpenChange={setManageOpen}
        environment={activeEnvironment}
      />
    </>
  );
}

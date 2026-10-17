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
import { useEnvProfilesStore } from '../../stores/envProfilesStore';
import { useVariableEnvironments } from '../../hooks/useVariablesApi';
import { buttonVariants } from '@/shared/components/ui/button';
import { EnvVariablesModal } from './EnvVariablesModal';
import { useEditorProjectStore } from '../../stores/editorProjectStore';

// Fallback list when the API hasn't surfaced any envs yet (fresh DB / offline).
const FALLBACK_ENVIRONMENTS = ['default', 'dev', 'uat', 'prod'] as const;

export function EnvProfileSelector() {
  const activeEnvironment = useEnvProfilesStore((s) => s.activeEnvironment);
  const setActiveEnvironment = useEnvProfilesStore((s) => s.setActiveEnvironment);
  const [manageOpen, setManageOpen] = useState(false);

  const { data: envsFromApi } = useVariableEnvironments();
  const projectId = useEditorProjectStore((s) => s.projectId);
  const projectEnvs = useEditorProjectStore((s) => s.environments);

  // Projects other than Default list exactly their environments (UI-DESIGN §6.6). The Default
  // project keeps the legacy behaviour: API-known envs merged with fallbacks.
  let options: string[];
  if (projectId !== 'proj_default' && projectEnvs.length > 0) {
    options = projectEnvs;
  } else {
    const envSet = new Set<string>(FALLBACK_ENVIRONMENTS);
    for (const e of envsFromApi ?? []) envSet.add(e);
    envSet.add(activeEnvironment);
    options = [...envSet].sort();
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          title={`Workflow runs use variables from environment: ${activeEnvironment}`}
          data-testid="editor-env-select"
          className={buttonVariants({ variant: 'ghost', size: 'sm', className: 'text-muted-foreground hover:text-foreground' })}
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

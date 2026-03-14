import { createRoute, useNavigate } from '@tanstack/react-router';
import {
  Plus,
  Search,
  ArrowUpDown,
  LayoutGrid,
  List,
  Check,
  ChevronRight,
  ChevronLeft,
  Moon,
  Sun,
  FolderOpen,
  AppWindow,
  Workflow,
} from 'lucide-react';
import { useState, useMemo, useEffect } from 'react';

import { rootRoute } from './__root';
import { Input } from '@/shared/components/ui/input';
import { Skeleton } from '@/shared/components/ui/skeleton';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu';
import { ToolbarSeparator } from '@/shared/components/ui/toolbar';
import { WorkflowCard } from '@/features/projects/components/WorkflowCard';
import { WorkflowListRow } from '@/features/projects/components/WorkflowListRow';
import { AppCard } from '@/features/projects/components/AppCard';
import { AppListRow } from '@/features/projects/components/AppListRow';
import { useWorkflows } from '@/features/projects/hooks/useWorkflows';
import { useLatestExecutions } from '@/features/projects/hooks/useLatestExecutions';
import { useApps } from '@/features/projects/hooks/useApps';
import { useCreateApp } from '@/features/projects/hooks/useAppActions';
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore';
import { useTheme } from '@/shared/components/theme-provider';

export const projectsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'projects',
  component: ProjectsPage,
});

type SortBy = 'name-asc' | 'name-desc' | 'newest' | 'oldest';
type TypeFilter = 'all' | 'workflows' | 'apps';
type ViewMode = 'grid' | 'list';

interface ProjectItem {
  id: string;
  name: string;
  type: 'workflow' | 'app';
  updatedAt: string;
  createdAt: string;
  workflow?: import('@/features/projects/hooks/useWorkflows').WorkflowWithDefinition;
  app?: import('@/features/projects/hooks/useApps').AppSummary;
}

const SORT_OPTIONS: { value: SortBy; label: string }[] = [
  { value: 'name-asc', label: 'Name A-Z' },
  { value: 'name-desc', label: 'Name Z-A' },
  { value: 'newest', label: 'Newest' },
  { value: 'oldest', label: 'Oldest' },
];

const btnClass =
  'h-8 w-8 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors';

const floatingPanel = 'bg-card/80 backdrop-blur-xl rounded-xl shadow-lg border border-border/30';

function ProjectsPage() {
  const { data: workflows, isLoading: workflowsLoading, error: workflowsError } = useWorkflows();
  const { data: latestExecutions } = useLatestExecutions();
  const { data: apps, isLoading: appsLoading } = useApps();
  const { isCreating, handleCreate: handleCreateApp } = useCreateApp();

  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState<SortBy>('newest');
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all');
  const [viewMode, setViewMode] = useState<ViewMode>('grid');
  const [currentPage, setCurrentPage] = useState(1);
  const perPage = 12;
  const navigate = useNavigate();
  const resetWorkflow = useWorkflowStore((s) => s.resetWorkflow);
  const { theme, setTheme } = useTheme();

  const isLoading = workflowsLoading || appsLoading;

  const handleNewWorkflow = () => {
    resetWorkflow();
    navigate({ to: '/editor' });
  };

  const allItems = useMemo<ProjectItem[]>(() => {
    const items: ProjectItem[] = [];
    if (workflows) {
      for (const w of workflows) {
        items.push({ id: w.id, name: w.name, type: 'workflow', updatedAt: w.updatedAt, createdAt: w.createdAt, workflow: w });
      }
    }
    if (apps) {
      for (const a of apps) {
        items.push({ id: a.id, name: a.name, type: 'app', updatedAt: a.updatedAt, createdAt: a.createdAt, app: a });
      }
    }
    return items;
  }, [workflows, apps]);

  const processedItems = useMemo(() => {
    let result = [...allItems];
    if (typeFilter === 'workflows') result = result.filter((i) => i.type === 'workflow');
    else if (typeFilter === 'apps') result = result.filter((i) => i.type === 'app');
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      result = result.filter((i) => i.name.toLowerCase().includes(q));
    }
    result.sort((a, b) => {
      switch (sortBy) {
        case 'name-asc': return a.name.localeCompare(b.name);
        case 'name-desc': return b.name.localeCompare(a.name);
        case 'newest': return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
        case 'oldest': return new Date(a.updatedAt).getTime() - new Date(b.updatedAt).getTime();
        default: return 0;
      }
    });
    return result;
  }, [allItems, typeFilter, searchQuery, sortBy]);

  const totalPages = Math.max(1, Math.ceil(processedItems.length / perPage));
  const paginatedItems = useMemo(
    () => processedItems.slice((currentPage - 1) * perPage, currentPage * perPage),
    [processedItems, currentPage],
  );

  useEffect(() => { setCurrentPage(1); }, [searchQuery, typeFilter, sortBy]);

  const workflowCount = allItems.filter((i) => i.type === 'workflow').length;
  const appCount = allItems.filter((i) => i.type === 'app').length;
  const totalCount = allItems.length;
  const currentSortLabel = SORT_OPTIONS.find((o) => o.value === sortBy)?.label ?? 'Sort';

  // Workflows shown when filter is 'all' or 'workflows'
  const workflowItems = paginatedItems.filter((i) => i.type === 'workflow');
  const appItems = paginatedItems.filter((i) => i.type === 'app');
  const showWorkflowSection = typeFilter !== 'apps' && workflowItems.length > 0;
  const showAppSection = typeFilter !== 'workflows' && appItems.length > 0;

  return (
    <div className="h-full w-full relative bg-muted dark:bg-black/60">
      {/* Floating navbar */}
      <div className="absolute top-3 left-3 right-3 z-20">
        <div className={`flex items-center h-11 px-3 ${floatingPanel} gap-1`}>
          {/* Brand */}
          <span className="text-[13px] font-semibold text-primary-foreground bg-primary px-2 py-0.5 rounded-md mr-1">luna</span>

          <ToolbarSeparator />

          {/* Type filter pills */}
          <div className="flex items-center gap-0.5 bg-muted/50 rounded-md p-0.5">
            {([
              { key: 'all' as TypeFilter, label: 'All', count: totalCount },
              { key: 'workflows' as TypeFilter, label: 'Workflows', count: workflowCount },
              { key: 'apps' as TypeFilter, label: 'Apps', count: appCount },
            ]).map((f) => (
              <button
                key={f.key}
                className={`px-2.5 py-1 text-[11px] font-medium rounded-md transition-all duration-150 ${
                  typeFilter === f.key
                    ? 'bg-card text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
                onClick={() => setTypeFilter(f.key)}
              >
                {f.label}
                <span className="ml-1 opacity-50">{f.count}</span>
              </button>
            ))}
          </div>

          <ToolbarSeparator />

          {/* Search */}
          <div className="relative max-w-[220px] flex-1">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
            <Input
              placeholder="Search..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-7 h-7 text-xs bg-transparent border-none shadow-none focus-visible:ring-0 placeholder:text-muted-foreground/60"
            />
          </div>

          <div className="flex-1" />

          {/* Sort */}
          <DropdownMenu>
            <DropdownMenuTrigger className={btnClass + ' !w-auto px-2 gap-1 text-[11px]'}>
              <ArrowUpDown className="h-3 w-3" />
              <span className="hidden sm:inline">{currentSortLabel}</span>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {SORT_OPTIONS.map((option) => (
                <DropdownMenuItem key={option.value} onClick={() => setSortBy(option.value)}>
                  <span className="flex items-center gap-2 w-full">
                    {sortBy === option.value ? <Check className="h-3 w-3" /> : <span className="w-3" />}
                    {option.label}
                  </span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          {/* View toggle */}
          <div className="flex items-center gap-0.5 bg-muted/50 rounded-md p-0.5">
            <button
              className={`p-1 rounded transition-all duration-150 ${viewMode === 'grid' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              onClick={() => setViewMode('grid')}
            >
              <LayoutGrid className="h-3.5 w-3.5" />
            </button>
            <button
              className={`p-1 rounded transition-all duration-150 ${viewMode === 'list' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              onClick={() => setViewMode('list')}
            >
              <List className="h-3.5 w-3.5" />
            </button>
          </div>

          <ToolbarSeparator />

          {/* Theme */}
          <button
            className={btnClass}
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          >
            <Sun className="h-3.5 w-3.5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
            <Moon className="absolute h-3.5 w-3.5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
          </button>

          {/* New dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger className={btnClass + ' !text-primary'}>
              <Plus size={16} strokeWidth={2.5} />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={handleNewWorkflow}>
                <Workflow className="h-4 w-4 mr-2" />
                New Workflow
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleCreateApp} disabled={isCreating}>
                <AppWindow className="h-4 w-4 mr-2" />
                New App
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* Content area */}
      <div className="absolute top-[68px] left-3 right-3 bottom-3 overflow-auto">
        <div className="max-w-[1200px] mx-auto space-y-6 pb-6">
          {/* Loading */}
          {isLoading ? (
            viewMode === 'grid' ? (
              <div className="space-y-6">
                <SectionShell label="Workflows">
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    {Array.from({ length: 3 }).map((_, i) => (
                      <div key={i} className={`rounded-xl overflow-hidden ${floatingPanel}`}>
                        <Skeleton className="aspect-[16/10] w-full rounded-none" />
                        <div className="p-4 space-y-2.5">
                          <Skeleton className="h-4 w-3/5" />
                          <Skeleton className="h-3 w-2/5" />
                        </div>
                      </div>
                    ))}
                  </div>
                </SectionShell>
                <SectionShell label="Apps">
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    {Array.from({ length: 3 }).map((_, i) => (
                      <div key={i} className={`rounded-xl overflow-hidden ${floatingPanel}`}>
                        <Skeleton className="aspect-[16/10] w-full rounded-none" />
                        <div className="p-4 space-y-2.5">
                          <Skeleton className="h-4 w-3/5" />
                          <Skeleton className="h-3 w-2/5" />
                        </div>
                      </div>
                    ))}
                  </div>
                </SectionShell>
              </div>
            ) : (
              <div className={`rounded-xl divide-y divide-border/50 overflow-hidden ${floatingPanel}`}>
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-4 px-5 py-3.5">
                    <Skeleton className="h-4 flex-1 max-w-48" />
                    <Skeleton className="h-5 w-14 rounded-full" />
                    <Skeleton className="h-3 w-16" />
                    <Skeleton className="h-3 w-20" />
                  </div>
                ))}
              </div>
            )
          ) : workflowsError ? (
            <div className={`flex items-center justify-center py-32 rounded-xl ${floatingPanel}`}>
              <div className="text-center">
                <p className="text-sm font-medium text-destructive">Failed to load projects</p>
                <p className="text-[13px] text-muted-foreground mt-1">{String(workflowsError)}</p>
              </div>
            </div>
          ) : processedItems.length === 0 ? (
            <div className={`flex items-center justify-center py-24 rounded-xl ${floatingPanel}`}>
              <div className="text-center">
                <div className="w-12 h-12 rounded-2xl bg-muted/50 flex items-center justify-center mx-auto mb-4">
                  <FolderOpen className="h-5 w-5 text-muted-foreground/50" />
                </div>
                <p className="text-sm font-medium mb-1">
                  {searchQuery || typeFilter !== 'all' ? 'No matching projects' : 'No projects yet'}
                </p>
                <p className="text-[13px] text-muted-foreground mb-5">
                  {searchQuery || typeFilter !== 'all'
                    ? 'Try adjusting your search or filters'
                    : 'Create your first workflow or app to get started'}
                </p>
                {!searchQuery && typeFilter === 'all' && (
                  <div className="flex items-center justify-center gap-2">
                    <button
                      onClick={handleNewWorkflow}
                      className="h-8 px-3 text-xs font-medium rounded-md border border-border/50 text-foreground hover:bg-accent transition-colors inline-flex items-center gap-1.5"
                    >
                      <Workflow className="h-3.5 w-3.5" />
                      New Workflow
                    </button>
                    <button
                      onClick={handleCreateApp}
                      disabled={isCreating}
                      className="h-8 px-3 text-xs font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors inline-flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <AppWindow className="h-3.5 w-3.5" />
                      New App
                    </button>
                  </div>
                )}
              </div>
            </div>
          ) : viewMode === 'grid' ? (
            /* ── Grid view: separated sections ── */
            <div className="space-y-6">
              {showWorkflowSection && (
                <SectionShell label="Workflows" count={typeFilter === 'all' ? workflowItems.length : undefined}>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    {workflowItems.map((item) => (
                      <WorkflowCard
                        key={item.id}
                        workflow={item.workflow!}
                        lastRun={latestExecutions?.get(item.id)}
                      />
                    ))}
                  </div>
                </SectionShell>
              )}
              {showAppSection && (
                <SectionShell label="Apps" count={typeFilter === 'all' ? appItems.length : undefined}>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    {appItems.map((item) => (
                      <AppCard key={item.id} app={item.app!} />
                    ))}
                  </div>
                </SectionShell>
              )}
            </div>
          ) : (
            /* ── List view: separated sections ── */
            <div className="space-y-6">
              {showWorkflowSection && (
                <SectionShell label="Workflows" count={typeFilter === 'all' ? workflowItems.length : undefined}>
                  <div className={`rounded-xl divide-y divide-border/50 overflow-hidden ${floatingPanel}`}>
                    {workflowItems.map((item) => (
                      <WorkflowListRow
                        key={item.id}
                        workflow={item.workflow!}
                        lastRun={latestExecutions?.get(item.id)}
                      />
                    ))}
                  </div>
                </SectionShell>
              )}
              {showAppSection && (
                <SectionShell label="Apps" count={typeFilter === 'all' ? appItems.length : undefined}>
                  <div className={`rounded-xl divide-y divide-border/50 overflow-hidden ${floatingPanel}`}>
                    {appItems.map((item) => (
                      <AppListRow key={item.id} app={item.app!} />
                    ))}
                  </div>
                </SectionShell>
              )}
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between px-1">
              <span className="text-[11px] text-muted-foreground">
                {(currentPage - 1) * perPage + 1}–{Math.min(currentPage * perPage, processedItems.length)} of{' '}
                {processedItems.length}
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                  disabled={currentPage === 1}
                  className="h-7 w-7 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors disabled:opacity-30 disabled:pointer-events-none"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </button>
                {Array.from({ length: totalPages }).map((_, i) => (
                  <button
                    key={i}
                    onClick={() => setCurrentPage(i + 1)}
                    className={`h-7 min-w-[28px] px-1 rounded-lg text-[11px] font-medium transition-colors ${
                      currentPage === i + 1
                        ? 'bg-primary text-primary-foreground'
                        : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                    }`}
                  >
                    {i + 1}
                  </button>
                ))}
                <button
                  onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                  disabled={currentPage === totalPages}
                  className="h-7 w-7 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors disabled:opacity-30 disabled:pointer-events-none"
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Section header with label + optional count badge */
function SectionShell({ label, count, children }: { label: string; count?: number; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 px-1">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{label}</span>
        {count !== undefined && (
          <span className="text-[10px] text-muted-foreground/60 bg-muted/60 px-1.5 py-0.5 rounded-full">{count}</span>
        )}
        <div className="flex-1 h-px bg-border/30" />
      </div>
      {children}
    </div>
  );
}

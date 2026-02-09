import { createRoute, useNavigate } from '@tanstack/react-router';
import {
  Plus,
  Search,
  FolderOpen,
  ArrowUpDown,
  LayoutGrid,
  List,
  Check,
} from 'lucide-react';
import { useState, useMemo } from 'react';

import { rootRoute } from './__root';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Skeleton } from '@/shared/components/ui/skeleton';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu';
import { WorkflowCard } from '@/features/workflows/components/WorkflowCard';
import { WorkflowListRow } from '@/features/workflows/components/WorkflowListRow';
import { useWorkflows } from '@/features/workflows/hooks/useWorkflows';
import { useLatestExecutions } from '@/features/workflows/hooks/useLatestExecutions';
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore';

export const workflowsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'workflows',
  component: WorkflowsPage,
});

type SortBy = 'name-asc' | 'name-desc' | 'newest' | 'oldest' | 'most-nodes' | 'fewest-nodes';
type FilterBy = 'all' | 'active' | 'inactive';
type ViewMode = 'grid' | 'list';

const SORT_OPTIONS: { value: SortBy; label: string }[] = [
  { value: 'name-asc', label: 'Name A-Z' },
  { value: 'name-desc', label: 'Name Z-A' },
  { value: 'newest', label: 'Newest' },
  { value: 'oldest', label: 'Oldest' },
  { value: 'most-nodes', label: 'Most nodes' },
  { value: 'fewest-nodes', label: 'Fewest nodes' },
];

function WorkflowsPage() {
  const { data: workflows, isLoading, error } = useWorkflows();
  const { data: latestExecutions } = useLatestExecutions();
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState<SortBy>('newest');
  const [filterBy, setFilterBy] = useState<FilterBy>('all');
  const [viewMode, setViewMode] = useState<ViewMode>('grid');
  const navigate = useNavigate();
  const resetWorkflow = useWorkflowStore((s) => s.resetWorkflow);

  const handleNewWorkflow = () => {
    resetWorkflow();
    navigate({ to: '/editor' });
  };

  const processedWorkflows = useMemo(() => {
    if (!workflows) return [];

    let result = [...workflows];

    // Search filter
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      result = result.filter((w) => w.name.toLowerCase().includes(q));
    }

    // Active/inactive filter
    if (filterBy === 'active') {
      result = result.filter((w) => w.active);
    } else if (filterBy === 'inactive') {
      result = result.filter((w) => !w.active);
    }

    // Sort
    result.sort((a, b) => {
      switch (sortBy) {
        case 'name-asc':
          return a.name.localeCompare(b.name);
        case 'name-desc':
          return b.name.localeCompare(a.name);
        case 'newest':
          return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
        case 'oldest':
          return new Date(a.updatedAt).getTime() - new Date(b.updatedAt).getTime();
        case 'most-nodes':
          return b.nodeCount - a.nodeCount;
        case 'fewest-nodes':
          return a.nodeCount - b.nodeCount;
        default:
          return 0;
      }
    });

    return result;
  }, [workflows, searchQuery, filterBy, sortBy]);

  const activeCount = workflows?.filter((w) => w.active).length ?? 0;
  const totalCount = workflows?.length ?? 0;
  const currentSortLabel = SORT_OPTIONS.find((o) => o.value === sortBy)?.label ?? 'Sort';

  return (
    <div className="h-full w-full flex flex-col">
      {/* Header */}
      <header className="bg-card/80 backdrop-blur-sm px-5 py-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Workflows</h1>
            <p className="text-[13px] text-muted-foreground mt-0.5">
              {isLoading ? (
                <Skeleton className="h-4 w-40 inline-block" />
              ) : (
                <>{totalCount} workflows &middot; {activeCount} active</>
              )}
            </p>
          </div>
          <Button onClick={handleNewWorkflow} className="gap-1.5">
            <Plus className="h-3.5 w-3.5" />
            New Workflow
          </Button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-2">
          {/* Filter pills */}
          <div className="flex items-center gap-1">
            {(['all', 'active', 'inactive'] as FilterBy[]).map((f) => (
              <Button
                key={f}
                variant={filterBy === f ? 'default' : 'ghost'}
                size="sm"
                className="h-7 text-xs capitalize"
                onClick={() => setFilterBy(f)}
              >
                {f}
              </Button>
            ))}
          </div>

          <div className="w-px h-5 bg-border mx-1" />

          {/* Search */}
          <div className="relative max-w-xs flex-1">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              placeholder="Search workflows..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-8 h-7 text-xs"
            />
          </div>

          <div className="flex-1" />

          {/* Sort dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs">
                <ArrowUpDown className="h-3 w-3" />
                {currentSortLabel}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {SORT_OPTIONS.map((option) => (
                <DropdownMenuItem
                  key={option.value}
                  onClick={() => setSortBy(option.value)}
                >
                  <span className="flex items-center gap-2 w-full">
                    {sortBy === option.value ? (
                      <Check className="h-3 w-3" />
                    ) : (
                      <span className="w-3" />
                    )}
                    {option.label}
                  </span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          {/* View toggle */}
          <div className="flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="icon-sm"
              className={viewMode === 'grid' ? 'bg-accent' : ''}
              onClick={() => setViewMode('grid')}
            >
              <LayoutGrid className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              className={viewMode === 'list' ? 'bg-accent' : ''}
              onClick={() => setViewMode('list')}
            >
              <List className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>

        {/* Gradient accent line */}
        <div className="h-px bg-gradient-to-r from-transparent via-primary/30 to-transparent mt-4 -mb-4" />
      </header>

      {/* Content */}
      <main className="flex-1 overflow-auto p-5">
        {isLoading ? (
          viewMode === 'grid' ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="rounded-lg border border-border bg-card overflow-hidden">
                  <Skeleton className="h-40 w-full rounded-none" />
                  <div className="px-3.5 pt-3 pb-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <Skeleton className="h-4 w-32" />
                      <Skeleton className="h-5 w-14 rounded" />
                    </div>
                    <div className="flex items-center gap-3">
                      <Skeleton className="h-3 w-16" />
                      <Skeleton className="h-3 w-20" />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="bg-card border border-border rounded-lg divide-y divide-border">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="flex items-center gap-4 px-4 py-3">
                  <Skeleton className="h-4 flex-1 max-w-48" />
                  <Skeleton className="h-5 w-14 rounded-full" />
                  <Skeleton className="h-3 w-16" />
                  <Skeleton className="h-3 w-20" />
                  <Skeleton className="h-3.5 w-3.5 rounded-full" />
                </div>
              ))}
            </div>
          )
        ) : error ? (
          <div className="flex items-center justify-center h-64">
            <div className="bg-card border border-border rounded-lg p-6 text-center">
              <p className="text-[13px] font-medium text-destructive">Failed to load workflows</p>
              <p className="text-[12px] text-muted-foreground mt-1">{String(error)}</p>
            </div>
          </div>
        ) : processedWorkflows.length === 0 ? (
          <div className="flex items-center justify-center h-64">
            <div className="bg-card border border-border rounded-lg p-8 text-center">
              <div className="w-10 h-10 rounded-lg bg-muted/50 flex items-center justify-center mx-auto mb-3">
                <FolderOpen className="h-5 w-5 text-muted-foreground/50" />
              </div>
              <p className="text-[13px] font-medium text-foreground mb-0.5">
                {searchQuery || filterBy !== 'all'
                  ? 'No workflows match your filters'
                  : 'No workflows yet'}
              </p>
              <p className="text-[12px] text-muted-foreground mb-3">
                {searchQuery || filterBy !== 'all'
                  ? 'Try adjusting your search or filters'
                  : 'Get started by creating your first workflow'}
              </p>
              {!searchQuery && filterBy === 'all' && (
                <Button onClick={handleNewWorkflow} className="gap-1.5">
                  <Plus className="h-3.5 w-3.5" />
                  Create your first workflow
                </Button>
              )}
            </div>
          </div>
        ) : viewMode === 'grid' ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {processedWorkflows.map((workflow) => (
              <WorkflowCard
                key={workflow.id}
                workflow={workflow}
                lastRun={latestExecutions?.get(workflow.id)}
              />
            ))}
          </div>
        ) : (
          <div className="bg-card border border-border rounded-lg divide-y divide-border">
            {processedWorkflows.map((workflow) => (
              <WorkflowListRow
                key={workflow.id}
                workflow={workflow}
                lastRun={latestExecutions?.get(workflow.id)}
              />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}

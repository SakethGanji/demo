import { createRoute, useNavigate } from '@tanstack/react-router';
import {
  Plus,
  Search,
  FolderOpen,
  ArrowUpDown,
  LayoutGrid,
  List,
  Check,
  Workflow,
  TrendingUp,
  Upload,
  Sparkles,
  ArrowRight,
  ChevronRight,
  ChevronLeft,
  GitBranch,
  Mail,
  RefreshCw,
  Radar,
  MessageSquare,
  Bell,
  Activity,
  Clock,
  Globe,
  Shield,
  BarChart3,
  ArrowUpRight,
  Circle,
  Timer,
  Cpu,
  FileText,
  Zap,
  Moon,
  Sun,
  Play,
  Pin,
  Calendar,
  Database,
  Cloud,
  Plug,
} from 'lucide-react';
import { useState, useMemo, useEffect, useRef } from 'react';

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
import { formatDate } from '@/features/workflows/hooks/useWorkflowActions';
import { useTheme } from '@/shared/components/theme-provider';

export const workflowsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'workflows',
  component: WorkflowsPage,
});

type SortBy =
  | 'name-asc'
  | 'name-desc'
  | 'newest'
  | 'oldest'
  | 'most-nodes'
  | 'fewest-nodes';
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

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  return 'Good evening';
}

function formatTodayDate(): string {
  return new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });
}

function Sparkline({
  values,
  width = 120,
  height = 32,
  color = 'var(--primary)',
}: {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
}) {
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const id = color.replace(/[^a-z0-9]/gi, '');
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width;
      const y = height - ((v - min) / range) * (height - 4) - 2;
      return `${x},${y}`;
    })
    .join(' ');
  const areaPoints = `0,${height} ${points} ${width},${height}`;
  return (
    <svg width={width} height={height} className="shrink-0">
      <defs>
        <linearGradient id={`sg-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.2" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={areaPoints} fill={`url(#sg-${id})`} />
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DonutChart({ value, size = 56 }: { value: number; size?: number }) {
  const stroke = 5;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const filled = (value / 100) * circumference;
  return (
    <svg width={size} height={size} className="shrink-0 -rotate-90">
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="var(--muted)"
        strokeWidth={stroke}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="var(--success)"
        strokeWidth={stroke}
        strokeDasharray={`${filled} ${circumference - filled}`}
        strokeLinecap="round"
      />
    </svg>
  );
}

function AnimatedNumber({
  value,
  duration = 800,
}: {
  value: number;
  duration?: number;
}) {
  const [display, setDisplay] = useState(0);
  const raf = useRef<number>();
  useEffect(() => {
    const start = display;
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min((now - t0) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      setDisplay(Math.round(start + (value - start) * eased));
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return <>{display.toLocaleString()}</>;
}

const HOURLY_THROUGHPUT = [
  12, 18, 24, 31, 28, 35, 41, 38, 45, 52, 48, 55, 42, 38, 44, 51, 47, 53,
  49, 45, 39, 32, 25, 18,
];

const TEMPLATES = [
  { name: 'Email Automation', desc: 'Trigger emails based on events', icon: Mail, iconBg: 'bg-amber-500/10', iconFg: 'text-amber-600 dark:text-amber-400' },
  { name: 'Data Sync', desc: 'Keep databases in sync', icon: RefreshCw, iconBg: 'bg-blue-500/10', iconFg: 'text-blue-600 dark:text-blue-400' },
  { name: 'API Monitor', desc: 'Watch endpoint health & latency', icon: Radar, iconBg: 'bg-teal-500/10', iconFg: 'text-teal-600 dark:text-teal-400' },
  { name: 'AI Chat Bot', desc: 'LLM-powered conversational flows', icon: MessageSquare, iconBg: 'bg-purple-500/10', iconFg: 'text-purple-600 dark:text-purple-400' },
  { name: 'Alert Pipeline', desc: 'Route alerts to the right team', icon: Bell, iconBg: 'bg-rose-500/10', iconFg: 'text-rose-600 dark:text-rose-400' },
  { name: 'Git Workflow', desc: 'Automate CI/CD & PR actions', icon: GitBranch, iconBg: 'bg-emerald-500/10', iconFg: 'text-emerald-600 dark:text-emerald-400' },
];

const TEAM_MEMBERS = [
  { initials: 'AK', color: 'var(--node-transform)' },
  { initials: 'MR', color: 'var(--node-flow)' },
  { initials: 'JL', color: 'var(--node-action)' },
  { initials: 'ST', color: 'var(--node-trigger)' },
  { initials: 'DP', color: 'var(--node-output)' },
];

const ACTIVITY_FEED = [
  { user: 'Alex K.', initials: 'AK', action: 'deployed', target: 'Customer Onboarding', time: '3m ago', color: 'var(--node-transform)', icon: ArrowUpRight },
  { user: 'Maya R.', initials: 'MR', action: 'edited', target: 'Weekly Report Gen', time: '12m ago', color: 'var(--node-flow)', icon: FileText },
  { user: 'You', initials: 'SG', action: 'ran', target: 'Slack Digest Bot', time: '28m ago', color: 'var(--node-action)', icon: Zap },
  { user: 'James L.', initials: 'JL', action: 'created', target: 'API Health Monitor', time: '1h ago', color: 'var(--node-trigger)', icon: Plus },
  { user: 'System', initials: 'SY', action: 'alert on', target: 'Data Sync Pipeline', time: '2h ago', color: 'var(--node-ai)', icon: Bell },
];

const ENVIRONMENTS = [
  { name: 'Production', status: 'healthy' as const, workflows: 12, uptime: '99.97%', region: 'US-East', icon: Globe },
  { name: 'Staging', status: 'healthy' as const, workflows: 8, uptime: '99.91%', region: 'US-West', icon: Shield },
  { name: 'Development', status: 'warning' as const, workflows: 5, uptime: '98.2%', region: 'EU-West', icon: Cpu },
];

function WorkflowsPage() {
  const { data: workflows, isLoading, error } = useWorkflows();
  const { data: latestExecutions } = useLatestExecutions();
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState<SortBy>('newest');
  const [filterBy, setFilterBy] = useState<FilterBy>('all');
  const [viewMode, setViewMode] = useState<ViewMode>('grid');
  const [currentPage, setCurrentPage] = useState(1);
  const perPage = 9;
  const navigate = useNavigate();
  const resetWorkflow = useWorkflowStore((s) => s.resetWorkflow);
  const { theme, setTheme } = useTheme();
  const toggleTheme = () => setTheme(theme === 'dark' ? 'light' : 'dark');

  const handleNewWorkflow = () => {
    resetWorkflow();
    navigate({ to: '/editor' });
  };

  const processedWorkflows = useMemo(() => {
    if (!workflows) return [];
    let result = [...workflows];
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      result = result.filter((w) => w.name.toLowerCase().includes(q));
    }
    if (filterBy === 'active') result = result.filter((w) => w.active);
    else if (filterBy === 'inactive') result = result.filter((w) => !w.active);
    result.sort((a, b) => {
      switch (sortBy) {
        case 'name-asc':
          return a.name.localeCompare(b.name);
        case 'name-desc':
          return b.name.localeCompare(a.name);
        case 'newest':
          return (
            new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
          );
        case 'oldest':
          return (
            new Date(a.updatedAt).getTime() - new Date(b.updatedAt).getTime()
          );
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

  const totalPages = Math.max(1, Math.ceil(processedWorkflows.length / perPage));
  const paginatedWorkflows = useMemo(
    () => processedWorkflows.slice((currentPage - 1) * perPage, currentPage * perPage),
    [processedWorkflows, currentPage],
  );

  // Reset to page 1 when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [searchQuery, filterBy, sortBy]);

  const activeCount = workflows?.filter((w) => w.active).length ?? 0;
  const totalCount = workflows?.length ?? 0;
  const currentSortLabel =
    SORT_OPTIONS.find((o) => o.value === sortBy)?.label ?? 'Sort';
  const recentWorkflows = useMemo(
    () =>
      workflows
        ? [...workflows]
          .sort(
            (a, b) =>
              new Date(b.updatedAt).getTime() -
              new Date(a.updatedAt).getTime(),
          )
          .slice(0, 5)
        : [],
    [workflows],
  );

  return (
    <div className="h-full w-full overflow-auto relative">
      <div
        className="max-w-[1720px] mx-auto px-4 sm:px-6 lg:px-8 py-5 lg:py-7 grid gap-5 relative grid-cols-1 lg:grid-cols-[1fr_320px] 2xl:grid-cols-[280px_1fr_340px]"
      >
        {/* ── Left Sidebar ── */}
        <div className="hidden 2xl:block bg-muted/40 dark:bg-muted/20 rounded-2xl border border-border/30 p-4 space-y-3 fade-in-up">
          {/* Quick Actions */}
          <div className="bg-card rounded-xl p-4">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-6 h-6 rounded-md bg-primary/10 flex items-center justify-center">
                <Zap className="h-3.5 w-3.5 text-primary" />
              </div>
              <h2 className="text-sm font-semibold">Quick Actions</h2>
            </div>
            <div className="space-y-1">
              {[
                { label: 'New Workflow', icon: Plus, iconBg: 'bg-primary/10', iconFg: 'text-primary', action: handleNewWorkflow },
                { label: 'Import', icon: Upload, iconBg: 'bg-amber-500/10', iconFg: 'text-amber-600 dark:text-amber-400', action: () => {} },
                { label: 'AI Generate', icon: Sparkles, iconBg: 'bg-violet-500/10', iconFg: 'text-violet-600 dark:text-violet-400', action: () => {} },
                { label: 'Run All Active', icon: Play, iconBg: 'bg-emerald-500/10', iconFg: 'text-emerald-600 dark:text-emerald-400', action: () => {} },
              ].map((item) => (
                <button
                  key={item.label}
                  onClick={item.action}
                  className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] font-medium text-foreground hover:bg-muted/50 transition-colors text-left group/qa"
                >
                  <div className={`w-6 h-6 rounded-md ${item.iconBg} flex items-center justify-center shrink-0`}>
                    <item.icon className={`h-3 w-3 ${item.iconFg}`} />
                  </div>
                  <span className="flex-1">{item.label}</span>
                  <ArrowRight className="h-3 w-3 text-muted-foreground/30 opacity-0 group-hover/qa:opacity-100 transition-opacity" />
                </button>
              ))}
            </div>
          </div>

          {/* Pinned Workflows */}
          <div className="bg-card rounded-xl p-4">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-6 h-6 rounded-md bg-rose-500/10 flex items-center justify-center">
                <Pin className="h-3.5 w-3.5 text-rose-600 dark:text-rose-400" />
              </div>
              <h2 className="text-sm font-semibold">Pinned</h2>
            </div>
            {isLoading ? (
              <div className="space-y-2.5">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-2.5 px-2.5 py-1.5">
                    <Skeleton className="w-6 h-6 rounded-md" />
                    <Skeleton className="h-3 w-24" />
                  </div>
                ))}
              </div>
            ) : workflows && workflows.length > 0 ? (
              <div className="space-y-0.5">
                {workflows.slice(0, 4).map((w) => (
                  <button
                    key={w.id}
                    onClick={() => navigate({ to: '/editor', search: { workflowId: w.id } })}
                    className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg hover:bg-muted/50 transition-colors text-left group/pin"
                  >
                    <div className="w-6 h-6 rounded-md bg-primary/8 flex items-center justify-center shrink-0">
                      <Workflow className="h-3 w-3 text-primary" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-[12px] font-medium truncate">{w.name}</div>
                    </div>
                    <Play className="h-3 w-3 text-muted-foreground/30 opacity-0 group-hover/pin:opacity-100 transition-opacity shrink-0" />
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-[11px] text-muted-foreground px-2.5">No workflows yet</p>
            )}
          </div>

          {/* Scheduled Runs */}
          <div className="bg-card rounded-xl p-4">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-6 h-6 rounded-md bg-blue-500/10 flex items-center justify-center">
                <Calendar className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" />
              </div>
              <h2 className="text-sm font-semibold">Scheduled</h2>
            </div>
            <div className="space-y-0.5">
              {[
                { name: 'Daily Data Sync', time: 'Today, 6:00 PM', color: 'bg-blue-500' },
                { name: 'Weekly Report', time: 'Mon, 9:00 AM', color: 'bg-purple-500' },
                { name: 'API Health Check', time: 'Every 15 min', color: 'bg-emerald-500' },
              ].map((run) => (
                <div key={run.name} className="flex items-center gap-2.5 p-2 rounded-lg hover:bg-muted/40 transition-colors">
                  <div className={`w-1.5 h-6 rounded-full ${run.color}/60 shrink-0`} />
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] font-medium truncate">{run.name}</div>
                    <div className="text-[10px] text-muted-foreground">{run.time}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Integrations */}
          <div className="bg-card rounded-xl p-4">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-6 h-6 rounded-md bg-teal-500/10 flex items-center justify-center">
                <Plug className="h-3.5 w-3.5 text-teal-600 dark:text-teal-400" />
              </div>
              <h2 className="text-sm font-semibold">Integrations</h2>
            </div>
            <div className="space-y-0.5">
              {[
                { name: 'PostgreSQL', icon: Database, iconBg: 'bg-blue-500/10', iconFg: 'text-blue-600 dark:text-blue-400' },
                { name: 'AWS S3', icon: Cloud, iconBg: 'bg-amber-500/10', iconFg: 'text-amber-600 dark:text-amber-400' },
                { name: 'Slack', icon: MessageSquare, iconBg: 'bg-purple-500/10', iconFg: 'text-purple-600 dark:text-purple-400' },
              ].map((svc) => (
                <div key={svc.name} className="flex items-center gap-2.5 p-2 rounded-lg hover:bg-muted/40 transition-colors">
                  <div className={`w-6 h-6 rounded-md ${svc.iconBg} flex items-center justify-center shrink-0`}>
                    <svc.icon className={`h-3 w-3 ${svc.iconFg}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] font-medium">{svc.name}</div>
                  </div>
                  <Circle className="h-2 w-2 fill-emerald-500 text-emerald-500 shrink-0" />
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ── Main Content ── */}
        <div className="bg-muted/40 dark:bg-muted/20 rounded-2xl border border-border/30 p-4 space-y-4 min-w-0">
          {/* ── Hero with integrated metrics ── */}
          <div className="fade-in-up">
            <div className="bg-card rounded-xl p-8 relative overflow-hidden">
              <div className="relative z-10 flex flex-col xl:flex-row xl:items-start justify-between gap-6 xl:gap-8">
                <div className="flex-1">
                  <p className="text-[11px] text-muted-foreground tracking-wide uppercase font-medium">
                    {formatTodayDate()}
                  </p>
                  <h1 className="text-2xl font-semibold tracking-tight mt-0.5 mb-0.5">
                    {getGreeting()}
                  </h1>
                  <p className="text-[13px] text-muted-foreground mb-5">
                    Your automation command center
                  </p>
                  <div className="flex items-center gap-3">
                    <Button onClick={handleNewWorkflow} className="gap-1.5">
                      <Plus className="h-3.5 w-3.5" />
                      New Workflow
                    </Button>
                    <Button
                      variant="outline"
                      className="gap-1.5"
                      onClick={() => navigate({ to: '/editor' })}
                    >
                      Open Editor
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 rounded-full"
                      onClick={toggleTheme}
                    >
                      <Sun className="h-4 w-4 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
                      <Moon className="absolute h-4 w-4 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
                      <span className="sr-only">Toggle theme</span>
                    </Button>
                  </div>
                </div>
                <div className="flex items-center">
                  {[
                    { label: 'Workflows', value: totalCount, trend: '+3', sparkData: [3, 5, 4, 7, 6, 8, 7] },
                    { label: 'Active', value: activeCount, trend: '+2', sparkData: [2, 3, 3, 4, 5, 5, 6] },
                    { label: 'Executions', value: 1248, trend: '+12%', sparkData: [42, 67, 53, 89, 72, 95, 61] },
                  ].map((stat, idx) => (
                    <div
                      key={stat.label}
                      className={`min-w-[130px] px-5 py-3 ${idx > 0 ? 'border-l border-border/50' : ''}`}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                          {stat.label}
                        </span>
                        <span className="text-[10px] font-semibold text-emerald-600 dark:text-emerald-400">
                          {stat.trend}
                        </span>
                      </div>
                      <div className="flex items-end justify-between gap-3">
                        <span className="text-2xl font-bold tracking-tight leading-none">
                          {isLoading ? (
                            <Skeleton className="h-6 w-10" />
                          ) : stat.label === 'Executions' ? (
                            <AnimatedNumber value={stat.value} />
                          ) : (
                            stat.value
                          )}
                        </span>
                        <Sparkline
                          values={stat.sparkData}
                          width={56}
                          height={22}
                          color="var(--primary)"
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Integrated metrics strip */}
              <div className="relative z-10 grid grid-cols-2 xl:grid-cols-4 gap-4 xl:gap-0 mt-6 pt-5 border-t border-border/50">
                <div className="flex items-center gap-3 pr-5">
                  <DonutChart value={94.2} size={40} />
                  <div>
                    <div className="text-lg font-bold tracking-tight leading-none">94.2%</div>
                    <div className="text-[10px] text-muted-foreground mt-0.5">Success rate</div>
                  </div>
                  <span className="text-[10px] font-semibold text-emerald-600 dark:text-emerald-400 flex items-center gap-0.5 ml-auto">
                    <TrendingUp className="h-2.5 w-2.5" /> +1.8%
                  </span>
                </div>
                <div className="flex items-center gap-3 xl:px-5 xl:border-l xl:border-border/50">
                  <div className="w-7 h-7 rounded-lg bg-amber-500/10 flex items-center justify-center shrink-0">
                    <Timer className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />
                  </div>
                  <div>
                    <div className="text-lg font-bold tracking-tight leading-none">1.8s</div>
                    <div className="text-[10px] text-muted-foreground mt-0.5">Avg Duration</div>
                  </div>
                  <div className="ml-auto flex items-center gap-2">
                    <span className="text-[10px] font-semibold text-emerald-600 dark:text-emerald-400">-0.3s</span>
                    <Sparkline values={[2.4, 2.1, 1.9, 2.2, 1.8, 1.6, 1.8]} width={48} height={18} color="var(--warning)" />
                  </div>
                </div>
                <div className="flex items-center gap-3 xl:px-5 xl:border-l xl:border-border/50">
                  <div className="w-7 h-7 rounded-lg bg-purple-500/10 flex items-center justify-center shrink-0">
                    <BarChart3 className="h-3.5 w-3.5 text-purple-600 dark:text-purple-400" />
                  </div>
                  <div>
                    <div className="text-lg font-bold tracking-tight leading-none">52</div>
                    <div className="text-[10px] text-muted-foreground mt-0.5">Throughput/hr</div>
                  </div>
                  <div className="ml-auto flex items-center gap-2">
                    <span className="text-[10px] font-semibold text-emerald-600 dark:text-emerald-400">+18%</span>
                    <Sparkline values={HOURLY_THROUGHPUT.slice(-8)} width={48} height={18} color="#7b61ff" />
                  </div>
                </div>
                <div className="flex items-center gap-3 xl:pl-5 xl:border-l xl:border-border/50">
                  <div className="w-7 h-7 rounded-lg bg-emerald-500/10 flex items-center justify-center shrink-0">
                    <Activity className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                  </div>
                  <div>
                    <div className="text-lg font-bold tracking-tight leading-none">99.97%</div>
                    <div className="text-[10px] text-muted-foreground mt-0.5">Uptime</div>
                  </div>
                  <div className="flex items-center gap-0.5 ml-auto">
                    {Array.from({ length: 14 }).map((_, i) => (
                      <div
                        key={i}
                        className={`w-0.5 h-3 rounded-full ${i === 9 ? 'bg-amber-400' : 'bg-emerald-500/60'}`}
                      />
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Templates */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold">Start from Template</h2>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs text-muted-foreground gap-1 h-6"
              >
                View all <ChevronRight className="h-3 w-3" />
              </Button>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
              {TEMPLATES.map((t) => (
                <div
                  key={t.name}
                  className="bg-card rounded-xl p-4 cursor-pointer hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 group/t"
                >
                  <div className={`w-10 h-10 rounded-xl ${t.iconBg} flex items-center justify-center mb-2.5 group-hover/t:scale-110 transition-transform`}>
                    <t.icon className={`h-[18px] w-[18px] ${t.iconFg}`} />
                  </div>
                  <div className="text-[12px] font-semibold mb-0.5">{t.name}</div>
                  <div className="text-[11px] text-muted-foreground leading-snug">
                    {t.desc}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Divider */}
          <div className="h-px bg-gradient-to-r from-transparent via-border to-transparent" />

          {/* Filter + Workflow Grid */}
          <div>
            <div className="flex flex-wrap items-center gap-2 mb-4">
              <div className="flex items-center gap-0.5 bg-muted/60 rounded-full p-0.5">
                {(['all', 'active', 'inactive'] as FilterBy[]).map((f) => (
                  <button
                    key={f}
                    className={`px-3 py-1.5 text-xs font-medium rounded-full capitalize transition-all duration-150 ${filterBy === f
                        ? 'bg-card text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                      }`}
                    onClick={() => setFilterBy(f)}
                  >
                    {f === 'all'
                      ? `All (${totalCount})`
                      : f === 'active'
                        ? `Active (${activeCount})`
                        : `Inactive (${totalCount - activeCount})`}
                  </button>
                ))}
              </div>
              <div className="w-px h-5 bg-border mx-1" />
              <div className="relative max-w-[240px] flex-1">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                <Input
                  placeholder="Search workflows..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-8 h-8 text-xs rounded-full"
                />
              </div>
              <div className="flex-1" />
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 gap-1.5 text-xs text-muted-foreground"
                  >
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
              <div className="flex items-center gap-0.5 rounded-full border border-border/50 p-0.5">
                <button
                  className={`p-1.5 rounded-full transition-all duration-150 ${viewMode === 'grid' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
                  onClick={() => setViewMode('grid')}
                >
                  <LayoutGrid className="h-3.5 w-3.5" />
                </button>
                <button
                  className={`p-1.5 rounded-full transition-all duration-150 ${viewMode === 'list' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
                  onClick={() => setViewMode('list')}
                >
                  <List className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>

            {isLoading ? (
              viewMode === 'grid' ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
                  {Array.from({ length: 6 }).map((_, i) => (
                    <div
                      key={i}
                      className="rounded-xl bg-card overflow-hidden wf-stagger"
                      style={{ animationDelay: `${i * 40}ms` }}
                    >
                      <Skeleton className="aspect-[16/10] w-full rounded-none" />
                      <div className="p-4 space-y-2.5">
                        <Skeleton className="h-4 w-3/5" />
                        <Skeleton className="h-3 w-2/5" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl bg-card divide-y divide-border/50 overflow-hidden">
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
            ) : error ? (
              <div className="flex items-center justify-center py-32">
                <div className="text-center">
                  <p className="text-sm font-medium text-destructive">
                    Failed to load workflows
                  </p>
                  <p className="text-[13px] text-muted-foreground mt-1">
                    {String(error)}
                  </p>
                </div>
              </div>
            ) : processedWorkflows.length === 0 ? (
              <div className="flex items-center justify-center py-24">
                <div className="text-center max-w-sm">
                  <div className="w-12 h-12 rounded-2xl bg-muted/50 flex items-center justify-center mx-auto mb-4">
                    <FolderOpen className="h-5 w-5 text-muted-foreground/50" />
                  </div>
                  <p className="text-sm font-medium mb-1">
                    {searchQuery || filterBy !== 'all'
                      ? 'No matching workflows'
                      : 'No workflows yet'}
                  </p>
                  <p className="text-[13px] text-muted-foreground mb-5">
                    {searchQuery || filterBy !== 'all'
                      ? 'Try adjusting your search or filters'
                      : 'Create your first workflow to get started'}
                  </p>
                  {!searchQuery && filterBy === 'all' && (
                    <Button
                      onClick={handleNewWorkflow}
                      size="sm"
                      className="gap-1.5"
                    >
                      <Plus className="h-3.5 w-3.5" />
                      New Workflow
                    </Button>
                  )}
                </div>
              </div>
            ) : viewMode === 'grid' ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
                {paginatedWorkflows.map((workflow, i) => (
                  <div
                    key={workflow.id}
                    className="wf-stagger"
                    style={{ animationDelay: `${i * 30}ms` }}
                  >
                    <WorkflowCard
                      workflow={workflow}
                      lastRun={latestExecutions?.get(workflow.id)}
                    />
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-xl bg-card divide-y divide-border/50 overflow-hidden fade-in-up">
                {paginatedWorkflows.map((workflow) => (
                  <WorkflowListRow
                    key={workflow.id}
                    workflow={workflow}
                    lastRun={latestExecutions?.get(workflow.id)}
                  />
                ))}
              </div>
            )}

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between pt-2">
                <span className="text-[11px] text-muted-foreground">
                  {(currentPage - 1) * perPage + 1}–{Math.min(currentPage * perPage, processedWorkflows.length)} of {processedWorkflows.length}
                </span>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                    disabled={currentPage === 1}
                    className="h-7 w-7 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors disabled:opacity-30 disabled:pointer-events-none"
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
                          : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                      }`}
                    >
                      {i + 1}
                    </button>
                  ))}
                  <button
                    onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                    disabled={currentPage === totalPages}
                    className="h-7 w-7 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors disabled:opacity-30 disabled:pointer-events-none"
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── Sidebar ── */}
        <div className="hidden lg:block bg-muted/40 dark:bg-muted/20 rounded-2xl border border-border/30 p-4 space-y-3 fade-in-up h-fit" style={{ animationDelay: '40ms' }}>
          {/* Recent Workflows */}
          <div className="bg-card rounded-xl p-5 flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Clock className="h-4 w-4 text-muted-foreground" />
                <h2 className="text-sm font-semibold">Recent Workflows</h2>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs text-muted-foreground gap-1 h-6"
              >
                View all <ChevronRight className="h-3 w-3" />
              </Button>
            </div>
            {isLoading ? (
              <div className="space-y-3 flex-1">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-3">
                    <Skeleton className="w-8 h-8 rounded-lg" />
                    <div className="flex-1 space-y-1.5">
                      <Skeleton className="h-3 w-32" />
                      <Skeleton className="h-2.5 w-20" />
                    </div>
                    <Skeleton className="h-5 w-14 rounded-full" />
                  </div>
                ))}
              </div>
            ) : recentWorkflows.length === 0 ? (
              <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
                No workflows yet
              </div>
            ) : (
              <div className="space-y-0.5 flex-1">
                {recentWorkflows.map((w) => (
                  <button
                    key={w.id}
                    onClick={() =>
                      navigate({
                        to: '/editor',
                        search: { workflowId: w.id },
                      })
                    }
                    className="w-full flex items-center gap-3 p-2 rounded-lg hover:bg-muted/50 transition-colors text-left group/item"
                  >
                    <div className="w-8 h-8 rounded-lg bg-primary/8 flex items-center justify-center shrink-0">
                      <Workflow className="h-3.5 w-3.5 text-primary" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <span className="text-[13px] font-medium truncate block">
                        {w.name}
                      </span>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[11px] text-muted-foreground">
                          {w.nodeCount} nodes
                        </span>
                        <span className="text-[11px] text-muted-foreground">
                          {formatDate(w.updatedAt)}
                        </span>
                      </div>
                    </div>
                    <span
                      className={`shrink-0 text-[10px] font-semibold px-2 py-0.5 rounded-full ${w.active
                          ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                          : 'bg-muted text-muted-foreground'
                        }`}
                    >
                      {w.active ? 'Active' : 'Draft'}
                    </span>
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/30 opacity-0 group-hover/item:opacity-100 transition-opacity shrink-0" />
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Activity Feed */}
          <div className="bg-card rounded-xl p-5 flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Activity className="h-4 w-4 text-muted-foreground" />
                <h2 className="text-sm font-semibold">Activity</h2>
              </div>
              <div className="flex items-center -space-x-1.5">
                {TEAM_MEMBERS.slice(0, 4).map((m) => (
                  <div
                    key={m.initials}
                    className="w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-bold text-white ring-2 ring-card"
                    style={{ backgroundColor: m.color }}
                  >
                    {m.initials}
                  </div>
                ))}
                <div className="w-6 h-6 rounded-full bg-muted flex items-center justify-center text-[9px] font-bold text-muted-foreground ring-2 ring-card">
                  +{TEAM_MEMBERS.length - 4}
                </div>
              </div>
            </div>
            <div className="space-y-0.5 flex-1">
              {ACTIVITY_FEED.map((item, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2.5 p-2 rounded-lg hover:bg-muted/40 transition-colors"
                >
                  <div
                    className="w-7 h-7 rounded-full flex items-center justify-center text-[9px] font-bold text-white shrink-0 mt-0.5"
                    style={{ backgroundColor: item.color }}
                  >
                    {item.initials}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-[12px] leading-relaxed">
                      <span className="font-semibold">{item.user}</span>{' '}
                      <span className="text-muted-foreground">
                        {item.action}
                      </span>{' '}
                      <span className="font-medium">{item.target}</span>
                    </p>
                    <span className="text-[10px] text-muted-foreground">
                      {item.time}
                    </span>
                  </div>
                  <item.icon className="h-3 w-3 text-muted-foreground/50 shrink-0 mt-1" />
                </div>
              ))}
            </div>
          </div>

          {/* Environments */}
          <div className="bg-card rounded-xl p-5 flex flex-col">
            <div className="flex items-center gap-2 mb-4">
              <Globe className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold">Environments</h2>
            </div>
            <div className="space-y-3 flex-1">
              {ENVIRONMENTS.map((env) => (
                <div
                  key={env.name}
                  className="p-3 rounded-lg bg-muted/30 border border-border/50 hover:border-border transition-colors"
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <env.icon className="h-3.5 w-3.5 text-muted-foreground" />
                      <span className="text-[12px] font-semibold">
                        {env.name}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Circle
                        className={`h-2 w-2 fill-current ${env.status === 'healthy' ? 'text-emerald-500' : 'text-amber-500'}`}
                      />
                      <span
                        className={`text-[10px] font-medium ${env.status === 'healthy' ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}`}
                      >
                        {env.status}
                      </span>
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <div>
                      <div className="text-[10px] text-muted-foreground">
                        Workflows
                      </div>
                      <div className="text-[13px] font-bold">
                        {env.workflows}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-muted-foreground">
                        Uptime
                      </div>
                      <div className="text-[13px] font-bold">{env.uptime}</div>
                    </div>
                    <div>
                      <div className="text-[10px] text-muted-foreground">
                        Region
                      </div>
                      <div className="text-[13px] font-bold">{env.region}</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-3 pt-3 border-t border-border/50 space-y-1">
              {[
                { label: 'Import Workflow', icon: Upload },
                { label: 'AI Generate', icon: Sparkles },
              ].map((item) => (
                <button
                  key={item.label}
                  className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] font-medium text-foreground hover:bg-muted/50 transition-colors text-left group/qa"
                >
                  <div className="w-6 h-6 rounded-md bg-primary/8 flex items-center justify-center shrink-0">
                    <item.icon className="h-3 w-3 text-primary" />
                  </div>
                  <span className="flex-1">{item.label}</span>
                  <ArrowRight className="h-3 w-3 text-muted-foreground/30 opacity-0 group-hover/qa:opacity-100 transition-opacity" />
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

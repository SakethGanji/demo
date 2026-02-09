/**
 * Shared icon mapping for workflow nodes
 * Maps FontAwesome names (fa:xxx) and Lucide names to Lucide components
 */

import {
  MousePointer,
  Play,
  Clock,
  Webhook,
  Code,
  Filter,
  GitBranch,
  Route,
  GitMerge,
  Layers,
  Globe,
  Pen,
  Calendar,
  AlertTriangle,
  MessageSquare,
  Bot,
  File,
  BarChart3,
  Zap,
  Timer,
  CircleAlert,
  MessageCircle,
  Shuffle,
  Split,
  Pause,
  ArrowRightLeft,
  FileCode,
  FileText,
  Send,
  Database,
  Brain,
  Sparkles,
  Calculator,
  Dice1,
  Type,
  PanelBottom,
  Monitor,
  Hash,
  Settings,
  Cpu,
  Network,
  Terminal,
  Box,
  Boxes,
  LayoutGrid,
  Wrench,
  Download,
  Upload,
  // New icons for missing nodes
  RefreshCw,
  CircleStop,
  GitFork,
  ListOrdered,
  Leaf,
  CircuitBoard,
  Mail,
  FileOutput,
  FileType,
  Reply,
  LogIn,
  Repeat,
  SquareFunction,
  Workflow,
  SlidersHorizontal,
  BookOpen,
  HardDrive,
  MessageSquareDashed,
  ScrollText,
  Share2,
  Unplug,
  Table,
  Search,
  KeyRound,
  FolderOpen,
  Image,
  Link,
  Regex,
  CloudDownload,
  CloudUpload,
  Variable,
  Cog,
  BrainCircuit,
} from 'lucide-react';

// Lucide icon component type
type LucideIconComponent = React.ComponentType<{ size?: string | number; className?: string }>;

/**
 * Icon mapping - maps icon names to Lucide components
 * Supports FontAwesome names (fa:xxx), Lucide names, and node type names
 */
const iconMap: Record<string, LucideIconComponent> = {
  // ── Trigger nodes ──────────────────────────────────────────────────
  'mouse-pointer': MousePointer,
  play: Play,
  'fa:play': Play,
  start: Play,
  zap: Zap,
  bolt: Zap,
  'fa:bolt': Zap,
  webhook: Webhook,
  'fa:webhook': Webhook,
  clock: Clock,
  'fa:clock': Clock,
  timer: Timer,
  cron: Timer,
  calendar: Calendar,
  'calendar-alt': Calendar,
  'fa:calendar': Calendar,
  'alert-triangle': AlertTriangle,
  'exclamation-triangle': CircleAlert,
  'fa:exclamation-triangle': CircleAlert,
  errortrigger: CircleAlert,
  message: MessageCircle,
  'fa:message': MessageCircle,
  chatinput: MessageCircle,
  'sign-in-alt': LogIn,
  'fa:sign-in-alt': LogIn,
  executeworkflowtrigger: LogIn,

  // ── Flow control nodes ─────────────────────────────────────────────
  'git-branch': GitBranch,
  'code-branch': GitBranch,
  'fa:code-branch': GitBranch,
  if: GitBranch,
  shuffle: Shuffle,
  random: Shuffle,
  'fa:random': Shuffle,
  switch: Shuffle,
  route: Route,
  split: Split,
  'layer-group': Boxes,
  'fa:layer-group': Boxes,
  splitinbatches: Boxes,
  'git-merge': GitMerge,
  merge: GitMerge,
  'compress-arrows-alt': ArrowRightLeft,
  'fa:compress-arrows-alt': ArrowRightLeft,
  pause: Pause,
  'hourglass-half': Pause,
  'fa:hourglass-half': Pause,
  wait: Pause,
  sync: RefreshCw,
  'fa:sync': RefreshCw,
  loop: RefreshCw,
  repeat: Repeat,
  'stop-circle': CircleStop,
  'fa:stop-circle': CircleStop,
  stopanderror: CircleStop,
  sitemap: GitFork,
  'fa:sitemap': GitFork,
  executeworkflow: GitFork,

  // ── Transform/Action nodes ─────────────────────────────────────────
  code: Code,
  'fa:code': Code,
  terminal: Terminal,
  filecode: FileCode,
  filter: Filter,
  'fa:filter': Filter,
  layers: Layers,
  'th-large': LayoutGrid,
  'fa:th-large': LayoutGrid,
  globe: Globe,
  'fa:globe': Globe,
  httprequest: Globe,
  pen: Pen,
  edit: Pen,
  'fa:edit': Pen,
  set: Settings,
  file: File,
  'fa:file': File,
  readfile: FileText,
  'chart-bar': BarChart3,
  'fa:chart-bar': BarChart3,
  pandasexplore: BarChart3,
  'list-ol': ListOrdered,
  'fa:list-ol': ListOrdered,
  itemlists: ListOrdered,
  sample: Filter,

  // ── Integration/Database nodes ─────────────────────────────────────
  leaf: Leaf,
  'fa:leaf': Leaf,
  mongodb: Leaf,
  'fa:database': Database,
  postgres: Database,
  'project-diagram': CircuitBoard,
  'fa:project-diagram': CircuitBoard,
  neo4j: CircuitBoard,
  envelope: Mail,
  'fa:envelope': Mail,
  sendemail: Mail,

  // ── AI nodes ───────────────────────────────────────────────────────
  bot: Bot,
  robot: Bot,
  'fa:robot': Bot,
  llmchat: Bot,
  brain: Brain,
  'fa:brain': Brain,
  aiagent: Brain,
  'brain-circuit': BrainCircuit,
  sparkles: Sparkles,
  cpu: Cpu,

  // ── File I/O nodes ─────────────────────────────────────────────────
  'file-export': FileOutput,
  'fa:file-export': FileOutput,
  writefile: FileOutput,
  'file-text': FileType,
  'fa:file-text': FileType,

  // ── Output/Display nodes ───────────────────────────────────────────
  'message-square': MessageSquare,
  'comment-dots': Send,
  'fa:comment-dots': Send,
  chatoutput: Send,
  monitor: Monitor,
  htmldisplay: Monitor,
  markdowndisplay: FileText,
  panelbottom: PanelBottom,
  reply: Reply,
  'fa:reply': Reply,
  respondtowebhook: Reply,

  // ── Subnode tools ──────────────────────────────────────────────────
  calculator: Calculator,
  'fa:calculator': Calculator,
  calculatortool: Calculator,
  dice: Dice1,
  'fa:dice': Dice1,
  randomnumbertool: Dice1,
  font: Type,
  'fa:font': Type,
  texttool: Type,
  currenttimetool: Clock,
  httprequesttool: Globe,
  codetool: SquareFunction,
  workflowtool: Workflow,

  // ── Subnode models ─────────────────────────────────────────────────
  google: Sparkles,
  'fa:google': Sparkles,
  geminimodel: Sparkles,
  llmmodelnode: SlidersHorizontal,

  // ── Subnode memory ─────────────────────────────────────────────────
  database: Database,
  simplememory: Database,
  memory: Database,
  sqlitememory: HardDrive,
  buffermemory: BookOpen,
  tokenbuffermemory: BookOpen,
  conversationwindowmemory: MessageSquareDashed,
  summarymemory: ScrollText,
  summarybuffermemory: ScrollText,
  progressivesummarymemory: ScrollText,
  vectormemory: Search,
  entitymemory: Share2,
  knowledgegraphmemory: CircuitBoard,

  // ── Storage nodes ──────────────────────────────────────────────────
  download: Download,
  'fa:download': Download,
  objectread: Download,
  upload: Upload,
  'fa:upload': Upload,
  objectwrite: Upload,

  // ── Generic / future-proofing ──────────────────────────────────────
  network: Network,
  box: Box,
  hash: Hash,
  wrench: Wrench,
  tasks: Wrench,
  cog: Cog,
  'fa:cog': Cog,
  'fa:cogs': Cog,
  sliders: SlidersHorizontal,
  'fa:sliders-h': SlidersHorizontal,
  table: Table,
  'fa:table': Table,
  key: KeyRound,
  'fa:key': KeyRound,
  folder: FolderOpen,
  'fa:folder': FolderOpen,
  'fa:folder-open': FolderOpen,
  image: Image,
  'fa:image': Image,
  link: Link,
  'fa:link': Link,
  regex: Regex,
  'cloud-download': CloudDownload,
  'fa:cloud-download-alt': CloudDownload,
  'cloud-upload': CloudUpload,
  'fa:cloud-upload-alt': CloudUpload,
  variable: Variable,
  'fa:variable': Variable,
  unplug: Unplug,
  'fa:plug': Unplug,
};

/**
 * Get the appropriate icon for a node
 * @param icon - The icon name from node data
 * @param nodeType - The node type as fallback
 * @returns LucideIconComponent
 */
export function getIconForNode(icon?: string, nodeType?: string): LucideIconComponent {
  // First try the explicit icon
  if (icon) {
    // Try direct match
    if (iconMap[icon]) return iconMap[icon];
    // Try without fa: prefix
    const withoutPrefix = icon.replace('fa:', '');
    if (iconMap[withoutPrefix]) return iconMap[withoutPrefix];
  }

  // Try matching by node type (lowercase)
  if (nodeType) {
    const typeLower = nodeType.toLowerCase();
    if (iconMap[typeLower]) return iconMap[typeLower];
  }

  // Fallback to Code icon
  return Code;
}

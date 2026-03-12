/**
 * Shared icon system for app builder components.
 *
 * Components use icon names (strings) in their props.
 * This module maps those names to lucide-react icons.
 */

import {
  Home, Settings, User, Users, Search, Bell, Mail, Inbox, Star,
  Heart, Plus, Minus, Check, X, ChevronRight, ChevronLeft, ChevronDown, ChevronUp,
  ArrowRight, ArrowLeft, ArrowUp, ArrowDown,
  ExternalLink, Link, Download, Upload, Share2,
  Edit, Trash2, Copy, Save, File, Folder, Image,
  Eye, EyeOff, Lock, Unlock, Shield,
  Sun, Moon, Monitor, Smartphone,
  BarChart3, PieChart, TrendingUp, Activity,
  Calendar, Clock, MapPin, Phone,
  MessageSquare, MessageCircle, Send,
  ShoppingCart, CreditCard, DollarSign, Package,
  Zap, Bookmark, Filter, Menu, MoreHorizontal, MoreVertical,
  LogOut, LogIn, UserPlus, RefreshCw,
  AlertCircle, AlertTriangle, Info, HelpCircle, CheckCircle, XCircle,
  Play, Pause, SkipForward, SkipBack,
  Wifi, Database, Globe, Code, Terminal,
  Camera, Mic, Volume2, VolumeX,
  Printer, Paperclip, Scissors, Layers,
  Grid, Layout, Sidebar, PanelLeft,
  Tag, Hash, AtSign, Percent,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

const iconRegistry: Record<string, LucideIcon> = {
  // Navigation
  home: Home,
  menu: Menu,
  'chevron-right': ChevronRight,
  'chevron-left': ChevronLeft,
  'chevron-down': ChevronDown,
  'chevron-up': ChevronUp,
  'arrow-right': ArrowRight,
  'arrow-left': ArrowLeft,
  'arrow-up': ArrowUp,
  'arrow-down': ArrowDown,
  'external-link': ExternalLink,
  link: Link,

  // User
  user: User,
  users: Users,
  'user-plus': UserPlus,
  'log-out': LogOut,
  'log-in': LogIn,
  lock: Lock,
  unlock: Unlock,
  shield: Shield,

  // Actions
  search: Search,
  plus: Plus,
  minus: Minus,
  check: Check,
  x: X,
  edit: Edit,
  trash: Trash2,
  copy: Copy,
  save: Save,
  download: Download,
  upload: Upload,
  share: Share2,
  send: Send,
  refresh: RefreshCw,
  filter: Filter,
  'more-h': MoreHorizontal,
  'more-v': MoreVertical,

  // Communication
  mail: Mail,
  inbox: Inbox,
  bell: Bell,
  message: MessageSquare,
  chat: MessageCircle,
  phone: Phone,

  // Media
  image: Image,
  camera: Camera,
  mic: Mic,
  'volume-on': Volume2,
  'volume-off': VolumeX,
  play: Play,
  pause: Pause,
  'skip-fwd': SkipForward,
  'skip-back': SkipBack,

  // Files
  file: File,
  folder: Folder,
  paperclip: Paperclip,
  printer: Printer,
  scissors: Scissors,

  // Status
  star: Star,
  heart: Heart,
  bookmark: Bookmark,
  eye: Eye,
  'eye-off': EyeOff,
  zap: Zap,

  // Feedback
  'alert-circle': AlertCircle,
  'alert-triangle': AlertTriangle,
  info: Info,
  help: HelpCircle,
  'check-circle': CheckCircle,
  'x-circle': XCircle,

  // Data
  'bar-chart': BarChart3,
  'pie-chart': PieChart,
  'trending-up': TrendingUp,
  activity: Activity,
  database: Database,

  // Commerce
  cart: ShoppingCart,
  'credit-card': CreditCard,
  dollar: DollarSign,
  package: Package,

  // Misc
  calendar: Calendar,
  clock: Clock,
  'map-pin': MapPin,
  sun: Sun,
  moon: Moon,
  monitor: Monitor,
  smartphone: Smartphone,
  globe: Globe,
  code: Code,
  terminal: Terminal,
  layers: Layers,
  grid: Grid,
  layout: Layout,
  sidebar: Sidebar,
  'panel-left': PanelLeft,
  tag: Tag,
  hash: Hash,
  'at-sign': AtSign,
  percent: Percent,
  settings: Settings,
}

// Grouped for UI dropdowns
export const iconGroups: Array<{ label: string; icons: string[] }> = [
  { label: 'Navigation', icons: ['home', 'menu', 'arrow-right', 'arrow-left', 'chevron-right', 'chevron-left', 'external-link', 'link'] },
  { label: 'User', icons: ['user', 'users', 'user-plus', 'log-out', 'log-in', 'lock', 'shield'] },
  { label: 'Actions', icons: ['search', 'plus', 'minus', 'check', 'x', 'edit', 'trash', 'copy', 'save', 'download', 'upload', 'share', 'send', 'refresh', 'filter'] },
  { label: 'Communication', icons: ['mail', 'inbox', 'bell', 'message', 'chat', 'phone'] },
  { label: 'Status', icons: ['star', 'heart', 'bookmark', 'eye', 'zap', 'check-circle', 'x-circle', 'alert-circle', 'info', 'help'] },
  { label: 'Data', icons: ['bar-chart', 'pie-chart', 'trending-up', 'activity', 'database'] },
  { label: 'Commerce', icons: ['cart', 'credit-card', 'dollar', 'package'] },
  { label: 'Misc', icons: ['calendar', 'clock', 'map-pin', 'sun', 'moon', 'globe', 'code', 'settings', 'tag'] },
]

export const allIconNames = Object.keys(iconRegistry)

/**
 * Render an icon by name. Returns null if icon not found or empty.
 */
export function IconRenderer({
  name,
  size = 16,
  className,
  style,
}: {
  name: string | undefined
  size?: number
  className?: string
  style?: React.CSSProperties
}) {
  if (!name) return null
  const Icon = iconRegistry[name] ?? iconRegistry[name.toLowerCase()]
  if (!Icon) return null
  return <Icon size={size} className={className} style={style} />
}

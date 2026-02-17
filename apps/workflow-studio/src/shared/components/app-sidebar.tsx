import * as React from "react"
import { Link, useMatchRoute } from "@tanstack/react-router"
import { Moon, Sun, Workflow, FolderOpen, Network } from "lucide-react"

import { Avatar, AvatarFallback } from "@/shared/components/ui/avatar"
import { useTheme } from "@/shared/components/theme-provider"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarGroup,
  SidebarGroupContent,
} from "@/shared/components/ui/sidebar"

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const matchRoute = useMatchRoute()
  const isWorkflowsActive = matchRoute({ to: '/workflows' })
  const isEditorActive = matchRoute({ to: '/editor' })
  const { theme, setTheme } = useTheme()

  const toggleTheme = () => {
    setTheme(theme === 'dark' ? 'light' : 'dark')
  }

  return (
    <Sidebar {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" tooltip="Command Studio">
              <div className="flex aspect-square size-7 items-center justify-center rounded-md bg-sidebar-primary">
                <Network className="size-4 text-sidebar-primary-foreground" strokeWidth={2.5} />
              </div>
              <div className="grid flex-1 text-left leading-tight">
                <div className="flex items-center gap-1">
                  <span className="text-[13px] font-semibold text-sidebar-foreground">Command</span>
                  <span className="text-[10px] font-semibold px-1 py-0.5 rounded bg-sidebar-primary text-sidebar-primary-foreground">Studio</span>
                </div>
                <span className="truncate text-[10px] text-sidebar-foreground/40">by Luna</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={!!isWorkflowsActive} tooltip="Workflows">
                  <Link to="/workflows">
                    <FolderOpen />
                    <span>Workflows</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={!!isEditorActive} tooltip="Editor">
                  <Link to="/editor">
                    <Workflow />
                    <span>Editor</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton onClick={toggleTheme} tooltip={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
              <Sun className="h-4 w-4 scale-100 rotate-0 transition-all dark:scale-0 dark:-rotate-90" />
              <Moon className="absolute h-4 w-4 scale-0 rotate-90 transition-all dark:scale-100 dark:rotate-0" />
              <span>{theme === 'dark' ? 'Dark' : 'Light'}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" tooltip="Profile">
              <Avatar className="h-7 w-7 rounded-md">
                <AvatarFallback className="rounded-md text-[11px] bg-sidebar-accent text-sidebar-foreground">SG</AvatarFallback>
              </Avatar>
              <div className="grid flex-1 text-left leading-tight">
                <span className="truncate text-[13px] font-medium text-sidebar-foreground">Saketh G</span>
                <span className="truncate text-[11px] text-sidebar-foreground/50">saketh@example.com</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  )
}

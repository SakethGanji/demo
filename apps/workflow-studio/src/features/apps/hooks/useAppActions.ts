import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { appsApi } from '@/shared/lib/api'
import type { AppSummary } from './useApps'

/** Default blank definition for a new app. */
const BLANK_DEFINITION = {
  sourceCode: null,
}

export function useCreateApp() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [isCreating, setIsCreating] = useState(false)

  const handleCreate = async () => {
    if (isCreating) return
    setIsCreating(true)
    try {
      const result = await appsApi.create({
        name: 'Untitled App',
        definition: BLANK_DEFINITION,
      })
      queryClient.invalidateQueries({ queryKey: ['apps'] })
      navigate({ to: '/builder', search: { appId: result.id } })
    } catch (error) {
      toast.error('Failed to create app', {
        description: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setIsCreating(false)
    }
  }

  return { isCreating, handleCreate }
}

export function useAppActions(app: AppSummary) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const handleOpen = () => {
    navigate({ to: '/builder', search: { appId: app.id } })
  }

  const handleDelete = async () => {
    if (isDeleting) return
    setIsDeleting(true)
    try {
      await appsApi.delete(app.id)
      queryClient.invalidateQueries({ queryKey: ['apps'] })
      setDeleteDialogOpen(false)
      toast.success('App deleted', {
        description: `"${app.name}" has been deleted.`,
      })
    } catch (error) {
      toast.error('Failed to delete app', {
        description: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setIsDeleting(false)
    }
  }

  return {
    isDeleting,
    deleteDialogOpen,
    setDeleteDialogOpen,
    handleOpen,
    handleDelete,
  }
}

export function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

export function formatRelative(dateString: string): string {
  const diff = Date.now() - new Date(dateString).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 7) return `${days}d ago`
  return formatDate(dateString)
}

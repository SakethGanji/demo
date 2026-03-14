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

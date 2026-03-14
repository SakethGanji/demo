/**
 * Zustand store for AI Chat message state.
 * Panel open/size is managed by editorLayoutStore (shared tabbed panel).
 */

import { createChatStore } from '@/shared/stores/createChatStore';
import type { AIChatMessage } from '../types/workflow';

export const useAIChatStore = createChatStore<AIChatMessage>({ prefix: 'ai' });

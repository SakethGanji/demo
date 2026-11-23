/** Words and pure helpers for choosing steps in a plan. */
import type { TriggerKind } from '../types';

export const TRIGGER_WORDS: Record<TriggerKind, string> = {
  form: 'A person fills in a form',
  webhook: 'Another system calls it',
  cron: 'On a schedule',
  manual: 'Someone starts it by hand',
  chat: 'A person chats with it',
  execute: 'Another workflow starts it',
};

export interface ApiRefOption { ref: string; label: string }

/** One entry of `GET /ai/builder/api-refs` (the engine builds the ref names; the studio never re-derives them). */
export interface ApiRefItem { api_ref: string; api: string | null; method: string | null; path: string | null; summary: string }

/** The picker options for the plan's API steps, from the engine's own list of the project's `api_ref` strings. */
export function apiRefOptions(items: ApiRefItem[]): ApiRefOption[] {
  return items.map((i) => {
    const what = i.summary || [i.method?.toUpperCase(), i.path].filter(Boolean).join(' ');
    return { ref: i.api_ref, label: i.api ? `${i.api}: ${what || i.api_ref}` : what || i.api_ref };
  });
}

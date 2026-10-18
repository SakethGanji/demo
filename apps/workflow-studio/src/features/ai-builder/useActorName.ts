import { useSyncExternalStore } from 'react';
import { getActorName, subscribeActorName } from './api';

/** The name remembered for this browser (studio.actorName); '' until it has been asked once. */
export function useActorName(): string {
  return useSyncExternalStore(subscribeActorName, getActorName, () => '');
}

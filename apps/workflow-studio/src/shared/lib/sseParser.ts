/**
 * Robust SSE stream parser using eventsource-parser.
 *
 * Replaces manual line-splitting with a battle-tested parser that
 * correctly handles chunked transfers, partial lines, and edge cases.
 */

import { createParser, type EventSourceMessage } from 'eventsource-parser';

/**
 * Consume a ReadableStream of SSE data and invoke `onEvent` for each
 * parsed `data:` line.  Returns when the stream closes.
 */
export async function consumeSSEStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onEvent: (data: string) => void,
): Promise<void> {
  const decoder = new TextDecoder();

  const parser = createParser({
    onEvent(event: EventSourceMessage) {
      onEvent(event.data);
    },
  });

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.feed(decoder.decode(value, { stream: true }));
  }

  // Flush any remaining bytes
  parser.feed(decoder.decode());
}

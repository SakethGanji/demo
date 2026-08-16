export interface ParsedCurlResult {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD';
  url: string;
  headers: Array<{ name: string; value: string }>;
  body: string;
}

type HttpMethod = ParsedCurlResult['method'];

const VALID_METHODS = new Set<string>(['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD']);

/**
 * Tokenize a shell-like command string into arguments,
 * handling single quotes, double quotes, and backslash escapes.
 */
function tokenize(input: string): string[] {
  const tokens: string[] = [];
  let current = '';
  let i = 0;

  while (i < input.length) {
    const ch = input[i];

    if (ch === "'") {
      // Single-quoted string: no escaping inside
      i++;
      while (i < input.length && input[i] !== "'") {
        current += input[i];
        i++;
      }
      i++; // skip closing quote
    } else if (ch === '"') {
      // Double-quoted string: backslash escapes
      i++;
      while (i < input.length && input[i] !== '"') {
        if (input[i] === '\\' && i + 1 < input.length) {
          const next = input[i + 1];
          if (next === '"' || next === '\\' || next === '$' || next === '`') {
            current += next;
            i += 2;
          } else {
            current += input[i];
            i++;
          }
        } else {
          current += input[i];
          i++;
        }
      }
      i++; // skip closing quote
    } else if (ch === '\\' && i + 1 < input.length) {
      // Backslash escape outside quotes
      current += input[i + 1];
      i += 2;
    } else if (ch === ' ' || ch === '\t') {
      if (current.length > 0) {
        tokens.push(current);
        current = '';
      }
      i++;
    } else {
      current += ch;
      i++;
    }
  }

  if (current.length > 0) {
    tokens.push(current);
  }

  return tokens;
}

/**
 * Parse a cURL command string into method, URL, headers, and body.
 */
export function parseCurl(input: string): ParsedCurlResult {
  // Normalize: strip backslash + newline continuations
  const normalized = input.replace(/\\\n\s*/g, ' ').replace(/\\\r\n\s*/g, ' ').trim();

  const tokens = tokenize(normalized);

  // Strip leading "curl" or "curl.exe"
  if (tokens.length > 0 && /^curl(\.exe)?$/i.test(tokens[0])) {
    tokens.shift();
  }

  let method: HttpMethod | null = null;
  let url = '';
  const headers: Array<{ name: string; value: string }> = [];
  let body = '';
  let hasBody = false;

  // Flags that take a value argument (next token)
  const flagsWithValue = new Set([
    '-X', '--request',
    '-H', '--header',
    '-d', '--data', '--data-raw', '--data-binary', '--data-urlencode',
    '--json',
    '-u', '--user',
    '-o', '--output',
    '-A', '--user-agent',
    '-e', '--referer',
    '-b', '--cookie',
    '-c', '--cookie-jar',
    '--connect-timeout',
    '--max-time',
    '-m',
    '--retry',
    '-w', '--write-out',
    '--url',
    '--proxy', '-x',
    '--cacert', '--cert', '--key',
    '-T', '--upload-file',
  ]);

  let i = 0;
  while (i < tokens.length) {
    const token = tokens[i];

    // Handle -X / --request
    if (token === '-X' || token === '--request') {
      i++;
      if (i < tokens.length) {
        const m = tokens[i].toUpperCase();
        if (VALID_METHODS.has(m)) {
          method = m as HttpMethod;
        }
      }
      i++;
      continue;
    }

    // Handle -H / --header
    if (token === '-H' || token === '--header') {
      i++;
      if (i < tokens.length) {
        const headerStr = tokens[i];
        const colonIdx = headerStr.indexOf(':');
        if (colonIdx > 0) {
          headers.push({
            name: headerStr.slice(0, colonIdx).trim(),
            value: headerStr.slice(colonIdx + 1).trim(),
          });
        }
      }
      i++;
      continue;
    }

    // Handle -d / --data / --data-raw / --data-binary
    if (token === '-d' || token === '--data' || token === '--data-raw' || token === '--data-binary') {
      i++;
      if (i < tokens.length) {
        body = tokens[i];
        hasBody = true;
      }
      i++;
      continue;
    }

    // Handle --data-urlencode
    if (token === '--data-urlencode') {
      i++;
      if (i < tokens.length) {
        body = tokens[i];
        hasBody = true;
      }
      i++;
      continue;
    }

    // Handle --json (body + auto Content-Type)
    if (token === '--json') {
      i++;
      if (i < tokens.length) {
        body = tokens[i];
        hasBody = true;
        // Add Content-Type: application/json if not already present
        if (!headers.some((h) => h.name.toLowerCase() === 'content-type')) {
          headers.push({ name: 'Content-Type', value: 'application/json' });
        }
        // Add Accept: application/json if not already present
        if (!headers.some((h) => h.name.toLowerCase() === 'accept')) {
          headers.push({ name: 'Accept', value: 'application/json' });
        }
      }
      i++;
      continue;
    }

    // Handle -u / --user (basic auth)
    if (token === '-u' || token === '--user') {
      i++;
      if (i < tokens.length) {
        const encoded = btoa(tokens[i]);
        headers.push({ name: 'Authorization', value: `Basic ${encoded}` });
      }
      i++;
      continue;
    }

    // Handle --url
    if (token === '--url') {
      i++;
      if (i < tokens.length) {
        url = tokens[i];
      }
      i++;
      continue;
    }

    // Skip known flags that take a value
    if (flagsWithValue.has(token)) {
      i += 2;
      continue;
    }

    // Skip boolean flags (start with -)
    if (token.startsWith('-')) {
      // Handle combined short flags like -sSL
      i++;
      continue;
    }

    // Bare positional arg = URL
    if (!url) {
      url = token;
    }
    i++;
  }

  if (!url) {
    throw new Error('No URL found in cURL command');
  }

  // Default method
  if (!method) {
    method = hasBody ? 'POST' : 'GET';
  }

  return { method, url, headers, body };
}

/**
 * Environment Configuration
 *
 * Determines backend URLs based on the current hostname.
 * Each environment can have multiple backends.
 */

interface Backends {
  workflow: string;    // Workflow engine API
  analytics: string;   // Analytics service API (datasets)
  // Add more backends as needed:
  // auth: string;
}

// Add your environments here
const ENVIRONMENTS: Record<string, Backends> = {
  'localhost': {
    workflow: 'http://localhost:8000',
    analytics: 'http://localhost:8001',
  },
  '127.0.0.1': {
    workflow: 'http://localhost:8000',
    analytics: 'http://localhost:8001',
  },

  // Development
  // 'dev.yourapp.com': {
  //   workflow: 'https://workflow-dev.yourapp.com',
  //   auth: 'https://auth-dev.yourapp.com',
  // },

  // UAT / Staging
  // 'uat.yourapp.com': {
  //   workflow: 'https://workflow-uat.yourapp.com',
  //   auth: 'https://auth-uat.yourapp.com',
  // },

  // Production
  // 'app.yourapp.com': {
  //   workflow: 'https://workflow.yourapp.com',
  //   auth: 'https://auth.yourapp.com',
  // },
};

// Default: same origin for all backends
const DEFAULT_BACKENDS: Backends = {
  workflow: '',
  analytics: '',
};

function getBackends(): Backends {
  const hostname = window.location.hostname;
  return ENVIRONMENTS[hostname] ?? DEFAULT_BACKENDS;
}

export const backends = getBackends();

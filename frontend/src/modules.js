// Distributor is deliberately chat-only; other modules keep their existing roles.
export const modules = [
  { id: 'overview', label: 'Overview', icon: 'grid', roles: ['retail-manager', 'supplier'] },
  {
    id: 'assistant',
    label: 'Agent Chat',
    icon: 'spark',
    roles: ['retail-manager', 'supplier', 'distributor'],
  },
  {
    id: 'evaluation',
    label: 'Evaluation & metrics',
    icon: 'chart',
    roles: ['retail-manager'],
  },
  {
    id: 'activity',
    label: 'Request Activity',
    icon: 'file',
    roles: ['retail-manager', 'supplier'],
  },
  { id: 'agent-registry', label: 'Agent registry', icon: 'flow', roles: ['retail-manager', 'supplier'] },
];

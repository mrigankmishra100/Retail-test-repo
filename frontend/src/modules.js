// Minimal navigation shared by the retail and supplier workspaces.
export const modules = [
  { id: 'overview', label: 'Overview', icon: 'grid', roles: ['retail-manager', 'supplier'] },
  {
    id: 'assistant',
    label: 'Agent Chat',
    icon: 'spark',
    roles: ['retail-manager', 'supplier'],
  },
  {
    id: 'activity',
    label: 'Request Activity',
    icon: 'file',
    roles: ['retail-manager', 'supplier'],
  },
];

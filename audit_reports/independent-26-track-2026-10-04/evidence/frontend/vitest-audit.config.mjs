import react from '../../../../frontend/node_modules/@vitejs/plugin-react/dist/index.js';
import path from 'node:path';
const root = path.resolve('frontend');
export default {
  root,
  plugins: [react()],
  resolve: { alias: {
    '@': path.join(root, 'src'),
    'next': path.join(root, 'node_modules/next'),
    '@tanstack/react-query': path.join(root, 'node_modules/@tanstack/react-query'),
    'react-dom': path.join(root, 'node_modules/react-dom'),
    'react': path.join(root, 'node_modules/react'),
    '@testing-library/react': path.join(root, 'node_modules/@testing-library/react'),
    'vitest': path.join(root, 'node_modules/vitest'),
  } },
  test: { environment: 'jsdom', globals: true, maxWorkers: 1,
    include: ['../audit_reports/independent-26-track-2026-10-04/evidence/frontend/*.probe.test.tsx'],
  },
};

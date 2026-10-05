import React from 'react';
import { act, render, screen, cleanup } from '@testing-library/react';
import { expect, it, vi, afterEach } from 'vitest';
vi.mock('@/lib/i18n/te', () => { throw new Error('offline chunk unavailable'); });
import { LanguageProvider } from '@/lib/i18n';
afterEach(cleanup);
it('CONFIRMED: rejected initial Telugu import permanently hides the page and all recovery controls', async () => {
  window.localStorage.setItem('herdly.language', 'te');
  const mounted = render(<LanguageProvider initialLanguage="te"><button>Recover shift</button></LanguageProvider>);
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });
  expect(screen.queryByText('Recover shift')).toBeNull();
  expect(mounted.container.textContent).toBe('');
  expect(mounted.container.querySelector('[aria-busy="true"]')).not.toBeNull();
  await act(async () => { window.dispatchEvent(new Event('online')); await new Promise(resolve => setTimeout(resolve, 50)); });
  expect(screen.queryByRole('button')).toBeNull();
  expect(mounted.container.textContent).toBe('');
  console.log('Initial lazy locale import failed; provider output is empty, no retry/toggle, online event did not recover.');
});

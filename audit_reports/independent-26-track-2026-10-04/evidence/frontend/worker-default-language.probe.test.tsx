import React from 'react';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { expect, it, vi, afterEach } from 'vitest';
vi.mock('next/navigation', () => ({usePathname: () => '/worker/login', useRouter: () => ({replace: vi.fn()})}));
vi.mock('@/lib/auth-context', () => ({useAuth: () => ({user:null, farmId:null, farms:[], signOut:vi.fn(), loading:false})}));
vi.mock('@/lib/use-permissions', () => ({usePermissions: () => ({can:()=>false})}));
vi.mock('@/components/account-dialog', () => ({AccountDialog:()=>null}));
import { LanguageProvider, useLanguage } from '@/lib/i18n';
import { WorkerShell } from '@/app/worker/layout';
function ReadLanguage() { const { language } = useLanguage(); return <span data-testid="active-language">{language}</span>; }
afterEach(cleanup);
it('CONFIRMED: real no-cookie bootstrap writes English before worker default can select Telugu', async () => {
  window.localStorage.clear();
  render(<LanguageProvider initialLanguage={null}><WorkerShell><ReadLanguage /></WorkerShell></LanguageProvider>);
  await waitFor(() => expect(screen.getByTestId('active-language').textContent).toBe('en'));
  expect(window.localStorage.getItem('herdly.language')).toBe('en');
  expect(document.cookie).toContain('herdly.language=en');
});

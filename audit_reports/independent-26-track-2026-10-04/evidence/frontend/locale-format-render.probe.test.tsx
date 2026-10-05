import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { expect, it, afterEach } from 'vitest';
import { LanguageProvider, useLanguage, loadLanguageCatalog } from '@/lib/i18n';
import { formatDate } from '@/lib/format';
import { getActiveLanguage, setActiveLanguage } from '@/lib/active-language';
function Consumer(){const {language,setLanguage}=useLanguage();return <><span data-testid="lang">{language}</span><span data-testid="date">{formatDate('2026-08-05')}</span><button onClick={()=>setLanguage('te')}>Telugu</button></>;}
afterEach(cleanup);
it('CONFIRMED: formatDate keeps previous-language output after a language switch until another render', async () => {
 window.localStorage.clear();setActiveLanguage('en');await loadLanguageCatalog('te');
 render(<LanguageProvider initialLanguage="en"><Consumer/></LanguageProvider>);
 await waitFor(()=>expect(screen.getByTestId('lang').textContent).toBe('en'));
 fireEvent.click(screen.getByRole('button',{name:'Telugu'}));
 await waitFor(()=>expect(screen.getByTestId('lang').textContent).toBe('te'));
 expect(getActiveLanguage()).toBe('te');
 expect(screen.getByTestId('date').textContent).toBe('5 Aug 2026');
 expect(formatDate('2026-08-05')).not.toBe('5 Aug 2026');
});

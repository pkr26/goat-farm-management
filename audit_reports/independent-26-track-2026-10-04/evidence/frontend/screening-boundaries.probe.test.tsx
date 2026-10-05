import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi, afterEach } from 'vitest';
const state=vi.hoisted(()=>({search:'',detailIds:[] as number[]}));
vi.mock('next/navigation',()=>({useSearchParams:()=>new URLSearchParams(state.search),usePathname:()=>'/screening',useRouter:()=>({replace:vi.fn()})}));
vi.mock('@/lib/use-permissions',()=>({usePermissions:()=>({can:(key:string)=>key==='health.view',loading:false,isError:false})}));
vi.mock('@/components/screening-check-dialog',()=>({DiseaseCheckDialog:()=>null}));
vi.mock('@/components/screening-review-history',()=>({ScreeningReviewHistory:()=>null}));
vi.mock('@/api/generated/endpoints',()=>({
 exportDatasetApiScreeningExportGet:vi.fn(),
 useReviewFindingApiScreeningFindingsFindingIdReviewPost:()=>({mutateAsync:vi.fn()}),
 useListImagesApiScreeningImagesGet:()=>({data:{status:200,data:{images:[],total:0,limit:25,offset:0}},refetch:vi.fn()}),
 useProviderStatsApiScreeningStatsGet:()=>({isPending:false,isError:true,error:new Error('503 Service unavailable'),refetch:vi.fn()}),
 useGetImageApiScreeningImagesImageIdGet:(id:number)=>{state.detailIds.push(id);return {isError:true,isPending:false,error:new Error('404 Wrong farm'),refetch:vi.fn()};},
}));
import ScreeningPage from '@/app/(app)/screening/page';
import { permittedAppPathFromList } from '@/lib/permission-navigation';
import en from '@/lib/i18n/en';
const mount=()=>render(<QueryClientProvider client={new QueryClient()}><ScreeningPage/></QueryClientProvider>);
afterEach(()=>{cleanup();state.search='';state.detailIds=[];});
it('CONFIRMED: a health.view-only user is offered the manage-only disease-check workflow',()=>{
 mount();expect(screen.queryByRole('button',{name:'Disease check'})).not.toBeNull();
 expect(screen.queryByRole('button',{name:/Export/i})).toBeNull();
});
it('CONFIRMED: a failed stats read is labeled as an empty scoreboard with no alert',()=>{
 mount();expect(screen.queryByText(en['screening.stats.empty'])).not.toBeNull();expect(screen.queryByRole('alert')).toBeNull();
});
it('CONFIRMED: retained cross-farm image query opens a stale detail error panel instead of the new farm list',()=>{
 const next=permittedAppPathFromList('/screening?image_id=71',['health.view'])!;
 state.search=next.split('?')[1];mount();
 expect(state.detailIds).toContain(71);
 expect(screen.queryByRole('button',{name:'Back to list'})).not.toBeNull();
 expect(screen.queryByRole('alert')).not.toBeNull();
});

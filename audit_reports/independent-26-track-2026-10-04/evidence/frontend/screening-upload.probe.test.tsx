import React, {useState} from 'react';
import { act, render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { expect, it, vi, afterEach } from 'vitest';
const mocks = vi.hoisted(() => ({
 submit:vi.fn(async()=>({status:200,data:{id:99}})),
 create:vi.fn(async()=>({status:201,data:{id:99}})),
 upload:vi.fn(async()=>({status:201,data:{upload_url:'https://object-store.invalid/upload',upload_fields:{key:'photo'},upload_method:'POST'}})),
}));
vi.mock('@/api/generated/endpoints', () => ({
 useCreateBatchApiScreeningBatchesPost:()=>({mutateAsync:mocks.create,isPending:false}),
 useSubmitBatchApiScreeningBatchesBatchIdSubmitPost:()=>({mutateAsync:mocks.submit,isPending:false}),
 requestUploadApiScreeningUploadsPost:mocks.upload,
 useBucketsBoardApiBucketsGet:()=>({data:{status:200,data:[{bucket:'BREEDING'}]}}),
}));
vi.mock('@/lib/farm-scope-guard',()=>({captureFarmScope:()=>()=>true}));
vi.mock('@/lib/api-client',()=>({ApiError:class extends Error{},composeRequestSignal:(signal:AbortSignal)=>signal}));
vi.mock('@/components/ui/dialog',()=>Object.fromEntries(['Dialog','DialogContent','DialogDescription','DialogFooter','DialogHeader','DialogTitle'].map(name=>[name,({children}:{children:React.ReactNode})=><div>{children}</div>])));
import { DiseaseCheckDialog } from '@/components/screening-check-dialog';
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('CONFIRMED: Finish accepts and aborts an in-flight second photo upload', async () => {
  const finished = vi.fn();
  let secondSignal:AbortSignal|null=null;
  const fetchMock=vi.fn().mockResolvedValueOnce({ok:true}).mockImplementationOnce((_url,init)=>new Promise((_resolve,reject)=>{
    secondSignal=init.signal;
    init.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')));
  }));
  vi.stubGlobal('fetch',fetchMock);
  URL.createObjectURL=vi.fn(()=> 'blob:audit'); URL.revokeObjectURL=vi.fn();
  function Harness(){const [open,setOpen]=useState(true);return <DiseaseCheckDialog open={open} onOpenChange={setOpen} onFinished={finished}/>;}
  const mounted=render(<Harness/>);
  fireEvent.click(screen.getByRole('button',{name:/Breeding/}));
  const choose=(name:string)=>fireEvent.change(mounted.container.querySelector('input[type=file]')!,{target:{files:[new File(['photo'],name,{type:'image/jpeg'})]}});
  choose('first.jpg'); fireEvent.click(screen.getByRole('button',{name:/Upload photo/i}));
  await waitFor(()=>expect(fetchMock).toHaveBeenCalledTimes(1));
  await waitFor(()=>expect(screen.getByRole('button',{name:/Finish/i}).disabled).toBe(false));
  choose('second.jpg'); fireEvent.click(screen.getByRole('button',{name:/Upload photo/i}));
  await waitFor(()=>expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(secondSignal!.aborted).toBe(false);
  const finish=screen.getByRole('button',{name:/Finish/i}) as HTMLButtonElement;
  expect(finish.disabled).toBe(false);
  await act(async()=>fireEvent.click(finish));
  expect(mocks.submit).toHaveBeenCalledWith({batchId:99});
  expect(finished).toHaveBeenCalledOnce();
  expect(secondSignal!.aborted).toBe(true);
});

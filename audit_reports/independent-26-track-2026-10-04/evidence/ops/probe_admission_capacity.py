"""Bounded synthetic thread work; no database or provider access."""
import asyncio,json,os,threading,time
for key in tuple(os.environ):
 if key.startswith('GOATFARM_'):del os.environ[key]
os.environ['GOATFARM_ENVIRONMENT']='development'
from fastapi import HTTPException
from app.api import _run_limits as limits
async def main():
 releases=[threading.Event(),threading.Event()]; starts=[threading.Event(),threading.Event()]
 def work(index):
  starts[index].set(); releases[index].wait(5);return index
 async def run(index):return await limits._with_run_limits(index+1,index+1,lambda:limits._offload(lambda:work(index)))
 jobs=[asyncio.create_task(run(i)) for i in range(2)]
 result={}
 try:
  result['both_native_workers_started']=all([await asyncio.to_thread(s.wait,2) for s in starts])
  t=time.perf_counter()
  try:await limits._with_run_limits(3,3,lambda:limits._offload(lambda:3))
  except HTTPException as e:result['third_request_status']=e.status_code
  result['third_request_seconds']=time.perf_counter()-t
  jobs[0].cancel()
  try:await jobs[0]
  except asyncio.CancelledError:pass
  try:await limits._with_run_limits(3,3,lambda:limits._offload(lambda:3))
  except HTTPException as e:result['after_cancel_before_native_completion_status']=e.status_code
 finally:
  for r in releases:r.set()
  await asyncio.gather(*jobs,return_exceptions=True)
  await asyncio.sleep(.05)
 result['after_native_completion']=await limits._with_run_limits(3,3,lambda:limits._offload(lambda:'admitted'))
 result['farm_lock_count_after_completion']=len(limits._farm_run_locks)
 result['user_lock_count_after_completion']=len(limits._user_run_locks)
 print(json.dumps(result,indent=2))
asyncio.run(main())

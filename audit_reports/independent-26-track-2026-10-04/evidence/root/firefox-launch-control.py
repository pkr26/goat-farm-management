"""Bounded Firefox-only diagnostic; kill only descendants owned by this probe."""
import datetime,json,os,pathlib,signal,subprocess,time
base=pathlib.Path(__file__).resolve().parent
node=pathlib.Path.home()/'.local/opt/node24/bin/node'
started=time.monotonic()
owners={}
snapshots=[]
def processes():
    data=subprocess.check_output(['ps','-axo','pid,ppid,etime,%cpu,command'],text=True)
    out={}
    for line in data.splitlines()[1:]:
        cols=line.strip().split(None,4)
        if len(cols)==5:
            out[int(cols[0])]={'pid':int(cols[0]),'ppid':int(cols[1]),'etime':cols[2],'cpu':cols[3],'command':cols[4]}
    return out
with (base/'firefox-launch-control.stdout.log').open('w') as stdout, (base/'firefox-launch-control.stderr.log').open('w') as stderr:
    proc=subprocess.Popen([str(node),str(base/'firefox-launch-control.mjs')],stdout=stdout,stderr=stderr,start_new_session=True)
    root_pid=proc.pid
    timed_out=False
    while True:
        rows=processes()
        descendants={root_pid}
        previous=-1
        while len(descendants)!=previous:
            previous=len(descendants)
            descendants.update(pid for pid,row in rows.items() if row['ppid'] in descendants)
        for pid in descendants:
            if pid in rows: owners[pid]=rows[pid]['command']
        snapshots.append({'elapsed_seconds':round(time.monotonic()-started,3),'owned_processes':[rows[pid] for pid in sorted(descendants) if pid in rows]})
        if proc.poll() is not None: break
        if time.monotonic()-started>=20:
            timed_out=True
            break
        time.sleep(.5)
    cleanup=[]
    # Playwright's own 15-second launch deadline should clean up. If its
    # shutdown is stuck, terminate only observed probe descendants with
    # command identity unchanged; never use killall or broad process names.
    for sig in (signal.SIGTERM,signal.SIGKILL):
        rows=processes()
        targets=[pid for pid,command in owners.items() if pid in rows and rows[pid]['command']==command]
        for pid in sorted(targets,reverse=True):
            try:
                os.kill(pid,sig)
                cleanup.append({'pid':pid,'signal':sig.name,'command':owners[pid]})
            except ProcessLookupError: pass
        if targets: time.sleep(.5)
    try: exit_code=proc.wait(timeout=1)
    except subprocess.TimeoutExpired: exit_code=None
rows=processes()
remaining=[rows[pid] for pid,command in owners.items() if pid in rows and rows[pid]['command']==command]
result={'completed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'node':str(node),'probe_root_pid':root_pid,'outer_deadline_seconds':20,'outer_deadline_reached':timed_out,'exit_code':exit_code,'elapsed_seconds':round(time.monotonic()-started,3),'cleanup_signals':cleanup,'remaining_owned_processes':remaining,'snapshots':snapshots,'scope':'Firefox launch plus about:blank only; no application ports, API, database, or external page navigation.'}
(base/'firefox-launch-control.result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='snapshots'},indent=2))
print((base/'firefox-launch-control.stdout.log').read_text())
print((base/'firefox-launch-control.stderr.log').read_text())

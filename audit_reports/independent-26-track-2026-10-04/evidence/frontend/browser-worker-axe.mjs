import { chromium } from '../../../../frontend/node_modules/@playwright/test/index.mjs';
import AxeBuilder from '../../../../frontend/node_modules/@axe-core/playwright/dist/index.mjs';
import fs from 'node:fs/promises';
const browser=await chromium.launch({headless:true});
try{
 const context=await browser.newContext({viewport:{width:390,height:844}});const page=await context.newPage();
 await page.goto('http://localhost:3000/worker/offline',{waitUntil:'domcontentloaded'});
 await page.getByRole('heading',{level:1}).waitFor();
 const result=await new AxeBuilder({page}).analyze();
 const output={violations:result.violations.map(({id,impact,description,nodes})=>({id,impact,description,nodes:nodes.map(({html,target,failureSummary})=>({html,target,failureSummary}))})),passes:result.passes.length,incomplete:result.incomplete.map(({id})=>id)};
 console.log(JSON.stringify(output,null,2));
 await fs.writeFile('audit_reports/independent-26-track-2026-10-04/evidence/frontend/browser-worker-axe.json',JSON.stringify(output,null,2));
}finally{await browser.close();}

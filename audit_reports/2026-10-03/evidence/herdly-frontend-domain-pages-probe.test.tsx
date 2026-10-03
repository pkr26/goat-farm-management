import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, expect, it, vi } from "vitest";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { farmToday, addDays } from "@/lib/format";
import InsurancePage from "@/app/(app)/finance/insurance/page";
import AnimalProfilePage from "@/app/(app)/animals/[id]/page";
import FinancePage from "@/app/(app)/finance/page";
const nav = vi.hoisted(() => ({ qs: "", pathname: "/finance/insurance", replace: vi.fn() }));
vi.mock("next/navigation", () => ({useRouter: () => ({push:vi.fn(), replace:nav.replace,prefetch:vi.fn()}), usePathname:()=>nav.pathname,useSearchParams:()=>new URLSearchParams(nav.qs),useParams:()=>({id:"1"})}));
vi.mock("sonner",()=>({toast:{success:vi.fn(),error:vi.fn()}}));
beforeAll(()=>{Object.assign(Element.prototype,{hasPointerCapture:()=>false,setPointerCapture:()=>{},releasePointerCapture:()=>{},scrollIntoView:()=>{}});});
const policy = {id:1,policy_number:"POL-001",insurer:"Insurer",animal_id:null,animal_tag:null,sum_insured:10000,premium:100,start_date:"2026-01-01",renewal_date:addDays(farmToday(),20),status:"active",notes:null,created_at:"2026-01-01T00:00:00Z",claim_date:null,claimed_at:null,claimed_by_id:null};
it("proves a dismissed insurance write closes the newly reopened policy draft",async()=>{
 nav.qs="";nav.pathname="/finance/insurance";
 let release!:()=>void; const pending=new Promise<void>(r=>release=r); let calls=0;
 server.use(http.get("/api/finance/insurance",()=>HttpResponse.json({policies:[policy],total:1,limit:50,offset:0})), http.post("/api/finance/insurance",async()=>{calls++;await pending;return HttpResponse.json({...policy,id:2},{status:201});}));
 const user=userEvent.setup();renderWithProviders(<InsurancePage/>);
 await screen.findAllByText("POL-001");await user.click(screen.getByRole("button",{name:/register policy/i}));
 const first=await screen.findByRole("dialog");
 fireEvent.change(within(first).getByLabelText(/policy number/i),{target:{value:"OLD-DRAFT"}});
 fireEvent.change(within(first).getByLabelText(/insurer/i),{target:{value:"Insurer"}});
 fireEvent.change(within(first).getByLabelText(/sum insured/i),{target:{value:"10000"}});
 fireEvent.change(within(first).getByLabelText(/^premium/i),{target:{value:"100"}});
 fireEvent.change(within(first).getByLabelText(/renewal date/i),{target:{value:addDays(farmToday(),30)}});
 await user.click(within(first).getByRole("button",{name:/register policy/i}));
 await waitFor(()=>expect(calls).toBe(1));await user.keyboard("{Escape}");await waitFor(()=>expect(screen.queryByRole("dialog")).toBeNull());
 await user.click(screen.getByRole("button",{name:/register policy/i}));const second=await screen.findByRole("dialog");
 fireEvent.change(within(second).getByLabelText(/policy number/i),{target:{value:"NEW-DRAFT"}});
 expect(within(second).getByLabelText(/policy number/i)).toHaveValue("NEW-DRAFT");
 release();await waitFor(()=>expect(screen.queryByRole("dialog")).toBeNull());
});
it("proves same-route finance filter navigation carries the prior page offset",async()=>{
 nav.qs="";nav.pathname="/finance";const requests:string[]=[];
 server.use(http.get("/api/finance",({request})=>{const url=new URL(request.url);requests.push(url.search);const offset=Number(url.searchParams.get("offset"));const filtered=url.searchParams.has("month");return HttpResponse.json({transactions:[],transactions_total:filtered?2:100,limit:50,offset,total_income:0,total_expense:0,feed_stock_value:0,pnl:[]});}));
 const user=userEvent.setup();const view=renderWithProviders(<FinancePage/>);await screen.findByRole("navigation",{name:/transactions pagination/i});
 await user.click(screen.getByRole("button",{name:"Next"}));await waitFor(()=>expect(requests.some(q=>q.includes("offset=50"))).toBe(true));
 nav.qs="month=2026-01";view.rerender(<FinancePage/>);
 await waitFor(()=>expect(requests.some(q=>q.includes("month=2026-01")&&q.includes("offset=50"))).toBe(true));
});

const ANIMAL = {
  id: 1,
  tag_number: "G-001",
  name: "Lakshmi",
  breed: "Osmanabadi",
  sex: "F",
  date_of_birth: "2025-05-10",
  estimated_dob: null,
  birth_type: "TWIN",
  source: "BORN",
  dam_id: null,
  sire_id: null,
  birth_weight: 2.4,
  current_bucket: "FOUNDATION",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
  sale_weight_kg: null,
  sale_price_per_kg: null,
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  movement_restricted: false,
  restriction_reason: null,
  suspected_scheduled_disease: false,
  suspected_disease: null,
  authority_notified_at: null,
  restriction_cleared_at: null,
  restriction_cleared_by_id: null,
  restriction_clearance_reference: null,
  restriction_version: 0,
  mortality_cause: null,
  mortality_cause_code: null,
  disposal_method: null,
  necropsy_done: false,
  necropsy_findings: null,
  mortality_reported_at: null,
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
  days_in_current_bucket: 12,
};

const PROFILE = {
  animal: ANIMAL,
  kids: [],
  kids_total: 0,
  kids_offset: 0,
  weights: [],
  weights_total: 0,
  weights_offset: 0,
  moves: [],
  moves_total: 0,
  moves_offset: 0,
  health_events: [],
  health_events_total: 0,
  health_events_offset: 0,
  breedings: [],
  breedings_total: 0,
  breedings_offset: 0,
  history_limit: 25,
};


it("proves phenotype selections remain editable while their old values are saving",async()=>{
 nav.qs="";nav.pathname="/animals/1";let release!:()=>void;const pending=new Promise<void>(r=>release=r);let body:Record<string,unknown>|null=null;
 server.use(http.get("/api/animals/1",()=>HttpResponse.json({...PROFILE,animal:{...ANIMAL,coat_color:"black",horned:false}})),http.get("/api/health/restrictions/1",()=>HttpResponse.json({animal_id:1,restriction_version:0,active:false,actions:[],total:0,limit:25,offset:0})),http.patch("/api/animals/1",async({request})=>{body=await request.json() as Record<string,unknown>;await pending;return HttpResponse.json(ANIMAL);}));
 const user=userEvent.setup();renderWithProviders(<AnimalProfilePage/>);await user.click(await screen.findByRole("button",{name:"Edit phenotype"}));const dialog=await screen.findByRole("dialog");
 await user.click(within(dialog).getByRole("button",{name:"Save"}));await waitFor(()=>expect(body).not.toBeNull());
 await user.click(within(dialog).getByLabelText("Coat colour"));await user.click(await screen.findByRole("option",{name:"Spotted"}));
 expect(within(dialog).getByLabelText("Coat colour")).toHaveTextContent("Spotted");expect(body).toEqual({coat_color:"black",horned:false});release();await waitFor(()=>expect(screen.queryByRole("dialog")).toBeNull());
});

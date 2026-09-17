// Stryker disable all: this file is a translation data table; its keys are
// verified by the catalog parity tests and per-key usage assertions, not by
// per-string mutation (mutating every sentence would only measure whether
// each literal is asserted verbatim somewhere).
/**
 * English message catalog — the source of truth for every key the wired
 * surfaces use. `te.ts` is typed against this map (Partial), so a Telugu
 * translation can lag without ever breaking rendering: missing keys fall
 * back to English at lookup time. Keys are dot-namespaced by surface
 * ("tasks.title", "nav.dashboard", "common.cancel").
 */

const en = {
  // ---------- shared, cross-page words ----------
  "common.cancel": "Cancel",
  "common.save": "Save",
  "common.saveChanges": "Save changes",
  "common.retry": "Retry",
  "common.loading": "Loading…",
  "common.close": "Close",
  "common.yes": "Yes",
  "common.no": "No",
  "common.areYouSure": "Are you sure?",
  "common.login": "Login",
  "common.logout": "Logout",
  "common.search": "Search",
  "common.filter": "Filter",
  "common.clear": "Clear",
  "common.note": "Note",
  "common.none": "— none —",
  "common.language": "Language",
  "common.somethingWentWrong": "Something went wrong",

  // ---------- app shell sidebar ----------
  "nav.group.overview": "Overview",
  "nav.group.herd": "Herd",
  "nav.group.healthFeed": "Health & Feed",
  "nav.group.operations": "Operations",
  "nav.group.business": "Business",
  "nav.dashboard": "Dashboard",
  "nav.animals": "Animals",
  "nav.buckets": "Buckets",
  "nav.breeding": "Breeding",
  "nav.kidding": "Kidding",
  "nav.health": "Health",
  "nav.feeding": "Feeding",
  "nav.purchases": "Purchases",
  "nav.tasks": "Tasks",
  "nav.finance": "Finance",
  "nav.planner": "Planner",
  "nav.simulation": "Simulation",
  "nav.opsSimulation": "Ops Simulation",
  "nav.reports": "Reports",
  "nav.team": "Team",

  // ---------- tasks board ----------
  "tasks.title": "Tasks",
  "tasks.description":
    "Duties and auto-generated protocol tasks, grouped by when they're due.",
  "tasks.tab.today": "Today",
  "tasks.tab.overdue": "Overdue",
  "tasks.tab.upcoming": "Upcoming",
  "tasks.tab.awaiting": "Awaiting verification",
  "tasks.tab.completed": "Completed",
  "tasks.pagination.today": "today tasks",
  "tasks.pagination.overdue": "overdue tasks",
  "tasks.pagination.upcoming": "upcoming tasks",
  "tasks.pagination.awaiting": "awaiting verification tasks",
  "tasks.pagination.completed": "completed tasks",

  "tasks.empty.today": "No tasks for today.",
  "tasks.empty.overdue": "No overdue tasks.",
  "tasks.empty.upcoming": "No upcoming tasks.",
  "tasks.empty.awaiting": "No tasks awaiting verification.",
  "tasks.empty.completed": "No completed tasks.",
  "tasks.empty.fallbackTitle": "No tasks yet.",
  "tasks.empty.fallbackGuidance":
    "New duties and auto-generated protocol tasks will show up here.",
  "tasks.guidance.today": "Nothing is due today. Late work shows on the Overdue tab.",
  "tasks.guidance.overdue": "Nothing is overdue. Today's duties show on the Today tab.",
  "tasks.guidance.upcoming":
    "No duties scheduled ahead. Overdue and today's work appear on their own tabs as duties are spawned.",
  "tasks.guidance.awaiting":
    "Nothing awaits verification. Duties land here for review once they are completed.",
  "tasks.guidance.completed":
    "No completed or skipped duties yet. Work you finish will land here.",

  "tasks.updatingBoard": "Updating the duty board…",
  "tasks.loadingTasks": "Loading tasks…",
  "tasks.loadFailed": "Could not load tasks.",
  "tasks.retryTasks": "Retry tasks",
  "tasks.noAccess": "You don't have access to this page.",

  "tasks.col.due": "Due",
  "tasks.col.task": "Task",
  "tasks.col.category": "Category",
  "tasks.col.assignedTo": "Assigned to",
  "tasks.col.animal": "Animal",
  "tasks.col.status": "Status",
  "tasks.col.finished": "Finished",
  "tasks.everyDays": "every {days}d",
  "tasks.daysLate": "({days}d late)",
  "tasks.sentBack": "Sent back: {note}",
  "tasks.skipReasonLabel": "Reason: {reason}",
  "tasks.viaRole": "via {role}",
  "tasks.awaitingMarker": "awaiting",
  "tasks.by": "by {name}",

  "tasks.openForm": "Open form",
  "tasks.notDueForm": "Not due yet — the linked form opens on the due date.",
  "tasks.formUnavailable": "Linked form unavailable with your permissions.",
  "tasks.notDueActions": "Not due yet — actions open on the due date.",
  "tasks.complete": "Complete",
  "tasks.retryComplete": "Retry complete",
  "tasks.skip": "Skip",
  "tasks.verify": "Verify",
  "tasks.retryVerify": "Retry verify",
  "tasks.reject": "Reject…",
  "tasks.retryReject": "Retry reject",
  "tasks.actionErrorSuffix": "Review the duty, then try again.",

  "tasks.skip.title": "Skip this task?",
  "tasks.skip.body":
    "Skipping moves this duty to its audit history. Add a reason so the team can understand why it was not completed.",
  "tasks.skip.reason": "Reason *",
  "tasks.skip.confirm": "Skip task",
  "tasks.skip.inFlight": "Skipping…",
  "tasks.skip.retry": "Retry skip",
  "tasks.skip.errorSuffix": "Check the reason, then try again.",

  "tasks.reject.title": "Reject duty",
  "tasks.reject.body":
    "Send this completed duty back to the worker. A reason is required so they know what to fix.",
  "tasks.reject.reason": "Reason *",
  "tasks.reject.reasonMissing": "Reason is required.",
  "tasks.reject.confirm": "Reject duty",
  "tasks.reject.inFlight": "Rejecting…",

  "tasks.recurConfirm.title": "Complete recurring duty?",
  "tasks.recurConfirm.body":
    "This duty repeats every {days} days. Completing it now schedules the next occurrence for {date}.",
  "tasks.recurConfirm.confirm": "Complete duty",

  "tasks.toast.completed": "Task completed.",
  "tasks.toast.skipped": "Task skipped.",
  "tasks.toast.verified": "Task verified.",
  "tasks.toast.sentBack": "Task sent back.",
  "tasks.toast.created": "Duty created.",
  "tasks.newDuty": "New duty",

  // New-duty dialog (labels, help text and inline validation).
  "tasks.form.intro":
    "Assign to a role (everyone with that role sees it) or to one specific worker. Set \"repeats every\" for recurring duties like daily cleaning — completing one schedules the next.",
  "tasks.form.titleLabel": "Title *",
  "tasks.form.titlePlaceholder": "e.g. Clean water troughs in BREEDING pen",
  "tasks.form.titleRequired": "Title is required",
  "tasks.form.dueDateLabel": "Due date *",
  "tasks.form.dueRequired": "Due date is required",
  "tasks.form.dueInvalid": "Pick a valid due date",
  "tasks.form.yearBand": "Year must be between 2000 and 2100",
  "tasks.form.categoryLabel": "Category",
  "tasks.form.recurLabel": "Repeats every (days)",
  "tasks.form.recurPlaceholder": "blank = one-off",
  "tasks.form.recurInvalid": "Must be a whole number of days (1–{max})",
  "tasks.form.recurTooLate":
    "Recurring due date is too late to schedule its next occurrence",
  "tasks.form.animalLabel": "Animal (optional)",
  "tasks.form.animalPlaceholder": "No animal",
  "tasks.form.animalDialogTitle": "Choose an animal for this duty",
  "tasks.form.animalHelp":
    "Link vaccine or deworming duties to an animal to open the matching health form. Unlinked duties remain ordinary checklists.",
  "tasks.form.noAnimalAccess":
    "You don't have animal access, so this duty will be created without an animal link.",
  "tasks.form.assignment": "Assignment",
  "tasks.form.loadingAssignments": "Loading assignment options…",
  "tasks.form.assignmentsFailed": "Could not load assignment options.",
  "tasks.form.retryAssignments": "Retry assignments",
  "tasks.form.assignToRole": "Assign to role",
  "tasks.form.assignToWorker": "or assign to worker",
  "tasks.form.workerFallback": "worker",
  "tasks.form.noTeamAccess": "You don't have team access — the duty will be created unassigned.",
  "tasks.form.createErrorSuffix": "Check the duty details, then try again.",
  "tasks.form.creating": "Creating…",
  "tasks.form.retryCreate": "Retry create",
  "tasks.form.create": "Create duty",

  // ---------- feeding plan (common buttons/toasts) ----------
  "feeding.recordDispensing": "Record dispensing",
  "feeding.record": "Record",
  "feeding.recording": "Recording…",
  "feeding.dispensedToast": "Dispensing recorded.",
  "feeding.edit": "Edit",
  "feeding.save": "Save",
  "feeding.saving": "Saving…",
  "feeding.dailyRation": "Daily ration — {bucket}",
  "feeding.kgPerHead": "kg per head per day *",
  "feeding.savedToast": "Saved {kg} kg/head for {bucket}.",

  // ---------- login ----------
  "login.totpPrompt":
    "This account uses two-factor authentication. Enter the current 6-digit code from your authenticator app.",
  "login.totpCodeLabel": "Authenticator code",
  "login.totpCodeInvalid": "That code is not valid right now.",
  "login.totpBack": "Back",
  "login.invalidCredentials": "Invalid email or password.",
  "login.networkError":
    "Network is weak — please check your connection and try again.",
  "login.forgotPassword": "Forgot password?",
  "login.forgotTitle": "Forgot password?",
  "login.forgotBody":
    "Worker passwords are reset by the farm owner from the farm's Team page. If you are the farm owner, contact your farm operator or support — there is no self-service email recovery yet.",

  // ---------- register ----------
  "register.networkError":
    "Network is weak — please check your connection and try again.",

  // ---------- generated duty titles (taskGen) ----------
  // The backend attaches `title_key`/`title_args` to auto-generated tasks so
  // the worker's language, not the server, renders the title. The 40 keys
  // below are the final contract (audit_reports/2026-09-14/
  // task_title_keys.md — breeding/kidding/cadence/quarantine/finance
  // services); a missing or unknown key falls back to the payload's own
  // English `title`. `{month}` args arrive as English month names and resolve
  // through the taskGen.month.* keys; `*_date` args are ISO and render
  // through the locale-aware formatDate.
  "taskGen.pregnancy_check": "Pregnancy check: {tag} (bred {breeding_date})",
  "taskGen.return_to_heat_watch":
    "Return-to-heat watch: {tag} — days 18–21 post-service; a standing heat means the service failed; record the observation early",
  "taskGen.pre_kidding_vaccine": "Pre-kidding ET+TT vaccine: {tag}",
  "taskGen.pre_kidding_vaccine_booster": "Pre-kidding ET+TT vaccine booster: {tag}",
  "taskGen.move_to_delivery": "Move {tag} to DELIVERY (kidding in ~2 weeks)",
  "taskGen.move_to_pregnancy_late":
    "Move {tag} to PREGNANCY_LATE (gestation day 100 — ration step-up)",
  "taskGen.birthing_kit_check":
    "Birthing kit check: {tag} due {kidding_date} — 7% iodine+cup, towels, disinfected scissors, lubricant, gloves, lamp, thermometer, tube+syringe, colostrum+electrolytes, weigh sling, ear tags+applicator",
  "taskGen.kidding_watch":
    "Kidding watch: {tag} (due {kidding_date}) — check udder fill, tail-head ligaments, vulva discharge",
  "taskGen.kidding_watch_due":
    "Kidding watch: {tag} (due {kidding_date}) — labor watch through the night; assist after 30 min straining w/o progress; call vet if 15–20 min unresolved",
  "taskGen.kidding_due": "Kidding due: {tag}",
  "taskGen.wean_kids": "Wean kids of {tag}; doe → RESTING",
  "taskGen.move_to_resting": "Move {tag} to RESTING after postpartum recovery",
  "taskGen.post_kidding_dam_check":
    "Post-kidding dam check: {tag} — placenta passed? udder/mastitis check, warm water, light feed, clean hindquarters",
  "taskGen.kidding_stall_cleanout":
    "Clean & disinfect kidding stall: {tag} — remove soiled bedding, disinfect, re-bed dry",
  "taskGen.kid_support": "Kid support: bottle-feed / colostrum replacer for {tag}'s litter",
  "taskGen.rebreed": "Re-breed {tag} (resting complete — flush window done)",
  "taskGen.fmd_vaccination_round":
    "FMD vaccination round ({month} {year}) — all animals; close via a bucket/batch vaccine health event",
  "taskGen.et_hs_premonsoon_round": "ET + HS pre-monsoon round ({year}) — all animals",
  "taskGen.goat_pox_round": "Goat Pox round ({year})",
  "taskGen.ccpp_round": "CCPP round ({year})",
  "taskGen.deworming_round":
    "Deworming round ({month} {year}) — adults; kids 1–6 months every 3 months",
  "taskGen.hoof_trimming_round": "Hoof trimming round (6-monthly) — trim all ages, heel to toe",
  "taskGen.ectoparasite_spray_round":
    "Ectoparasite spray/dip round (Butox/deltamethrin) — never heavily pregnant does",
  "taskGen.shed_disinfection_round":
    "Shed disinfection round — disinfect + lime; extra attention to kidding pens",
  "taskGen.monthly_weighing_round": "Monthly weighing round — record weights; grow-out buckets first",
  "taskGen.morning_feed_routine":
    "Morning routine: sweep bunks before the 6:30 AM feeding",
  "taskGen.daily_water_check": "Water check: check and refill all water troughs",
  "taskGen.feed_reorder": "Reorder {ingredient}: {qty_on_hand} kg on hand (reorder level {reorder_level} kg)",
  "taskGen.buck_rotation":
    "Rotate/replace buck {tag} — {age_months} months old (inbreeding management)",
  "taskGen.insurance_renewal": "Insurance renewal due: policy {policy_number}",
  "taskGen.quarantine_arrival_inspection":
    "Day 0–1: arrival inspection — dehydration (skin tent/gums), injuries, lameness, temperature; isolate sick immediately; handle quarantine animals LAST (dedicated boots/tools)",
  "taskGen.quarantine_rest":
    "Days 1–3: rest, electrolyte/jaggery water, dry roughage only, zero grain",
  "taskGen.quarantine_deworm": "Day 4: deworm — Albendazole/Closantel oral + Ivermectin SC",
  "taskGen.quarantine_liver_tonic": "Days 5–9: liver tonic in water + Vitamin AD3E injection",
  "taskGen.quarantine_ppr_vaccine": "Day 10: vaccinate PPR (live viral, SC)",
  "taskGen.quarantine_fecal_exam":
    "Day 13: fecal/dung sample exam — confirm day-4 deworm efficacy (record result as a FECAL_EXAM health event)",
  "taskGen.quarantine_et_tetanus_vaccine": "Day 20: vaccinate ET + Tetanus (toxoid, SC)",
  "taskGen.quarantine_goat_pox_vaccine": "Day 30: vaccinate Goat Pox (live viral, SC)",
  "taskGen.quarantine_prerelease_review": "Day 30: fecal recheck + clinical review before release",
  "taskGen.quarantine_fmd_vaccine": "Day 40: vaccinate FMD (killed, SC)",
  "taskGen.quarantine_release": "Day 45: 10% zinc sulfate footbath → release to FOUNDATION",
  // Localized month names for calendar-round titles ({month} args 1–12).
  "taskGen.month.1": "January",
  "taskGen.month.2": "February",
  "taskGen.month.3": "March",
  "taskGen.month.4": "April",
  "taskGen.month.5": "May",
  "taskGen.month.6": "June",
  "taskGen.month.7": "July",
  "taskGen.month.8": "August",
  "taskGen.month.9": "September",
  "taskGen.month.10": "October",
  "taskGen.month.11": "November",
  "taskGen.month.12": "December",

  // ---------- auth (login / register / brand panel) ----------
  "auth.welcomeBack": "Welcome back",
  "auth.signInSubtitle": "Sign in to your account",
  "auth.email": "Email",
  "auth.password": "Password",
  "auth.signIn": "Sign in",
  "auth.signingIn": "Signing in…",
  "auth.noAccount": "No account?",
  "auth.registerLink": "Register",
  "auth.emailInvalid": "Enter a valid email address",
  "auth.emailTooLong": "Email must be at most 254 characters",
  "auth.passwordRequired": "Password is required",
  "auth.passwordTooLong": "Password must be at most 128 characters",
  "auth.passwordTooShort": "Password must be at least 12 characters",
  "auth.passwordHint": "At least 12 characters.",
  "auth.createTitle": "Create your account",
  "auth.createSubtitle": "Start managing your herd in minutes",
  "auth.nameOptional": "Name (optional)",
  "auth.creatingAccount": "Creating account…",
  "auth.createAccount": "Create account",
  "auth.haveAccount": "Already have an account?",
  "auth.brandTitleLine1": "Herd management,",
  "auth.brandTitleLine2": "simplified.",
  "auth.brandTagline":
    "Run a healthier, more profitable farm — from the first tag to the final sale.",
  "auth.featureRecordsTitle": "Complete herd records",
  "auth.featureRecordsDesc": "Track every animal, tag and lineage in one place.",
  "auth.featureHealthTitle": "Proactive health care",
  "auth.featureHealthDesc": "Stay ahead of vaccinations, treatments and checkups.",
  "auth.featureInsightsTitle": "Insights that pay off",
  "auth.featureInsightsDesc": "Breeding, kidding and finance reports at a glance.",
  "auth.brandFoot": "For Osmanabadi goat herds across Telangana.",

  // ---------- app shell ----------
  "shell.skipToContent": "Skip to content",
  "shell.tagline": "Goat farm management",
  "shell.passwordChangeNotice":
    "This password was set by the farm owner — change it (Account → Change password) before continuing. Farm pages and actions stay blocked until you do.",

  // ---------- browser tab titles (app shell) ----------
  "doc.title.dashboard": "Dashboard",
  "doc.title.addAnimal": "Add animal",
  "doc.title.animal": "Animal",
  "doc.title.animals": "Animals",
  "doc.title.buckets": "Buckets",
  "doc.title.ultrasound": "Ultrasound",
  "doc.title.breeding": "Breeding",
  "doc.title.recordBirth": "Record birth",
  "doc.title.births": "Births",
  "doc.title.addHealthEvent": "Add health event",
  "doc.title.vaccinationSchedule": "Vaccination schedule",
  "doc.title.health": "Health",
  "doc.title.feedInventory": "Feed inventory",
  "doc.title.feedRecipes": "Feed recipes",
  "doc.title.feeding": "Feeding",
  "doc.title.purchases": "Purchases",
  "doc.title.tasks": "Tasks",
  "doc.title.finance": "Finance",
  "doc.title.planner": "Planner",
  "doc.title.simulation": "Simulation",
  "doc.title.opsSimulation": "Ops Simulation",
  "doc.title.reports": "Reports",
  "doc.title.team": "Team",
  "doc.title.noAccess": "No access",

  // ---------- health event log ----------
  // Meat-goat food safety: a drug withdrawal blocks slaughter/sale, not milk.
  "health.notForSaleUntil": "Not for sale until {date}",

  // ---------- health event record dialog ----------
  // The most worker-facing form in the app: every label, placeholder, helper
  // line, button state and client-side validation message lives here so the
  // dialog renders fully in Telugu.
  "health.form.title": "Add health event",
  "health.form.applyTo": "Apply to",
  "health.form.scopeAnimal": "Single animal",
  "health.form.scopeBucket": "Whole bucket",
  "health.form.scopeBatch": "Purchase batch",
  "health.form.animalLabel": "Animal *",
  "health.form.animalPlaceholder": "Pick an animal",
  "health.form.animalPickerTitle": "Choose an animal for this health event",
  "health.form.bucketLabel": "Bucket *",
  "health.form.bucketPlaceholder": "Pick a bucket",
  "health.form.batchLabel": "Purchase batch *",
  "health.form.batchPlaceholder": "Pick a batch",
  "health.form.batchPickerTitle": "Choose a purchase batch for this health event",
  "health.form.reviewedSnapshotOne": "Reviewed target snapshot: {count} active animal",
  "health.form.reviewedSnapshotMany": "Reviewed target snapshot: {count} active animals",
  "health.form.reviewedExplainer":
    "Confirming records only the reviewed IDs. An animal that joins an unlinked scope afterward is not silently added; if a reviewed animal leaves the scope (or a linked batch no longer matches exactly), the server rejects the write and requires a fresh review.",
  "health.form.reviewedListLabel": "Reviewed target animals",
  "health.form.reviewedEmptyTitle": "No active animals are in this reviewed target.",
  "health.form.reviewedEmptyDescription":
    "Pick a different target above — recording this one would save nothing.",
  "health.form.reviewIds": "Review exact animal IDs",
  "health.form.reviewIdsEmptyTitle": "No active animal IDs were returned.",
  "health.form.reviewIdsEmptyDescription":
    "The reviewed snapshot is empty — choose a different target above.",
  "health.form.dateLabel": "Date (defaults to today)",
  "health.form.typeLabel": "Type",
  "health.form.productNameLabel": "Product name",
  "health.form.productNamePlaceholder": "e.g. PPR vaccine",
  "health.form.diseaseTargetLabel": "Disease target",
  "health.form.doseLabel": "Dose",
  "health.form.dosePlaceholder": "e.g. 1 ml",
  "health.form.routeLabel": "Route",
  "health.form.vetLabel": "Vet",
  "health.form.costLabel": "Total cost (₹, split evenly)",
  "health.form.nextDueLabel": "Next due date",
  "health.form.loadingDuties": "Loading linked duties…",
  "health.form.dutiesLoadFailed": "Could not load linked duties.",
  "health.form.retryDuties": "Retry linked duties",
  "health.form.unresolvedDuty":
    "Could not link duty #{id} — it is unavailable or no longer a pending health duty. Record the event without it.",
  "health.form.dutyNotDue":
    "Duty #{id} is not due until {date} — the server rejects an event dated before then.",
  "health.form.linkedDutyLabel": "Linked duty (completes it)",
  "health.form.advancedTitle": "Advanced traceability & compliance",
  "health.form.advancedIntro":
    "Record the product trail, authorised schedule, statutory notification and movement/withdrawal holds when they apply.",
  "health.form.templateLabel": "Schedule/template name",
  "health.form.templatePlaceholder": "Select a seeded programme",
  "health.form.loadingTemplates": "Loading programmes…",
  "health.form.nextDueAuthorityLabel": "Next-due authority",
  "health.form.nextDueAuthorityPlaceholder":
    "e.g. veterinarian prescription / official schedule",
  "health.form.lotLabel": "Product lot/batch",
  "health.form.administeredByLabel": "Administered by",
  "health.form.manufacturedLabel": "Product manufactured",
  "health.form.expiresLabel": "Product expires",
  "health.form.vaccineValidLabel": "Vaccine valid until",
  "health.form.withdrawalLabel": "Withdrawal until",
  "health.form.certificateLabel": "Certificate number",
  "health.form.officialTagLabel": "Official tag number",
  "health.form.suspectedDiseaseLabel":
    "Suspected scheduled/notifiable disease — apply movement restriction",
  "health.form.authorityNotifiedLabel": "Authority notified date",
  "health.form.isolationStartedLabel": "Isolation started date",
  "health.form.notesLabel": "Notes",
  "health.form.errorSuffix": "Check the event details, then try again.",
  "health.form.reviewing": "Reviewing targets…",
  "health.form.saving": "Saving…",
  "health.form.reviewTargets": "Review target animals",
  "health.form.confirmForOne": "Confirm for {count} animal",
  "health.form.confirmForMany": "Confirm for {count} animals",
  "health.form.retrySave": "Retry save",
  "health.form.save": "Save event",
  "health.toast.reviewFailed": "Could not review the bulk target set.",
  "health.toast.recorded": "Health event recorded.",
  "health.toast.recordedForOne": "Health event recorded for {count} animal.",
  "health.toast.recordedForMany": "Health event recorded for {count} animals.",
  "health.toast.saveFailed": "Could not save the health event.",
  // Client-side (zod) validation messages — byte-identical to the strings the
  // schema-level tests pin; the money floor mirrors MIN_PERSISTED_MONEY_MESSAGE.
  "health.validation.costNonnegative": "Cost must be a number ≥ 0",
  "health.validation.moneyMin": "Amount must be ₹0 or at least ₹0.005",
  "health.validation.costTooLarge": "Cost cannot exceed ₹1,000,000,000",
  "health.validation.notesTooLong": "Notes cannot exceed {max} characters",
  "health.validation.pickAnimal": "Pick an animal",
  "health.validation.pickBucket": "Pick a bucket",
  "health.validation.pickBatch": "Pick a batch",
  "health.validation.pickDuty": "Pick a valid duty",
  "health.validation.dateFuture": "Date cannot be in the future",
  "health.validation.nextDueAfterEvent": "Next due date must be after the event date",
  "health.validation.nameSchedule": "Name the schedule used for a next-due date",
  "health.validation.recordAuthority": "Record the authority for this next-due date",
  "health.validation.manufactureFuture": "Manufacture date cannot be in the future",
  "health.validation.manufactureAfterEvent": "Manufacture date cannot be after the event date",
  "health.validation.expiryBeforeManufacture": "Expiry cannot be before manufacture date",
  "health.validation.productExpired": "Product was expired on the event date",
  "health.validation.validityBeforeEvent": "Vaccine validity cannot be before the event date",
  "health.validation.validityBeyondExpiry":
    "Vaccine validity cannot extend beyond product expiry",
  "health.validation.withdrawalBeforeEvent":
    "Withdrawal date cannot be before the event date",
  "health.validation.withdrawalTooLong":
    "Withdrawal date cannot be more than {days} days after the event",
  "health.validation.nameDisease": "Name the suspected scheduled disease",

  // ---------- health page chrome (headers, schedule card, event log) ----------
  "health.page.title": "Health",
  "health.page.description": "Vaccinations, deworming and treatments across the herd.",
  "health.page.loading": "Loading health events…",
  "health.addEvent": "Add event",
  "health.schedule.cardTitle": "Vaccination schedule per animal",
  "health.schedule.cardDescription":
    "Pick an animal to see due dates and boosters from the vaccination templates.",
  "health.schedule.viewFor": "View schedule for",
  "health.schedule.pickerTitle": "Choose an animal schedule",
  "health.schedule.view": "View",
  "health.log.title": "Event log",
  "health.log.description": "Every recorded health event, newest scope first.",
  "health.log.emptyTitle": "No health events recorded yet.",
  "health.log.emptyDescription":
    "Recorded vaccinations, deworming and treatments will appear here.",
  "health.log.updating": "Updating health events…",
  "health.pagination.label": "health events",
  "health.log.lot": "Lot: {lot}",
  "health.log.manufactured": "Manufactured: {date}",
  "health.log.expires": "Expires: {date}",
  "health.log.vaccineValidUntil": "Vaccine valid until: {date}",
  "health.log.certificate": "Certificate: {number}",
  "health.log.officialTag": "Official tag: {tag}",
  "health.log.administeredBy": "Administered by: {name}",
  "health.log.authorityNotified": "Authority notified: {date}",
  "health.log.isolationStarted": "Isolation started: {date}",
  "health.log.scheduledDiseaseSuspected": "Scheduled disease suspected",
  "health.log.noNextDue": "No next due date",
  "health.table.date": "Date",
  "health.table.type": "Type",
  "health.table.animal": "Animal",
  "health.table.product": "Product",
  "health.table.target": "Target",
  "health.table.dose": "Dose",
  "health.table.route": "Route",
  "health.table.cost": "Cost",
  "health.table.nextDue": "Next due",
  "health.table.notes": "Notes",
  "health.table.traceability": "Traceability & holds",

  // ---------- planner ----------
  "planner.useMyHerd": "Use my herd",
  // NLM (National Livestock Mission) capital-subsidy toggle and the DPR
  // (Detailed Project Report) download on saved plans.
  "planner.nlmSubsidy": "Apply NLM subsidy",
  "planner.nlmSubsidyHelp":
    "National Livestock Mission: the plan is priced with the scheme's 50% back-ended capital subsidy (capped per unit size) instead of the plain subsidy fraction.",
  "planner.downloadDpr": "Download DPR",
  "planner.downloadingDpr": "Downloading…",
  "planner.downloadDprFor": "Download DPR for {name}",
  "planner.dprDownloaded": "DPR downloaded.",
  "planner.dprFailed": "Could not download the DPR.",

  // ---------- feeding page static guidance ----------
  "feeding.shiftsLine":
    "Shifts: {morning} 6:30 AM (sweep bunks first) · {afternoon} 1:30 PM · {night} 7:30 PM.",
  "feeding.phase.maintenance": "Maintenance",
  "feeding.phase.flush": "Flush",
  "feeding.frameBuilder": "frame-builder",
  "feeding.fattening": "fattening",
  "feeding.rotationNote":
    "{resting} switches {maintenance} → {flush} at day 10; {maleKids} {frameBuilder} → {fattening} at day 91.",

  // ---------- animal phenotype ----------
  "animals.coatColor": "Coat colour",
  "animals.coatColor.black": "Black",
  "animals.coatColor.black_patched": "Black with patches",
  "animals.coatColor.brown": "Brown",
  "animals.coatColor.white": "White",
  "animals.coatColor.spotted": "Spotted",
  "animals.horned": "Horned",
  "animals.editPhenotype": "Edit phenotype",
  "animals.phenotypeSaved": "Phenotype saved.",
  "animals.notRecorded": "Not recorded",
  "common.unknown": "Unknown",

  // ---------- dashboard advisories ----------
  "dashboard.advisory.bakridHold":
    "{count} males finish within 2 months of Bakrid ({date}) — hold for the festival price.",
} as const;

export type MessageKey = keyof typeof en;
export default en;

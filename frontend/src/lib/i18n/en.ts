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
  "tasks.skip.reason": "Reason",
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
} as const;

export type MessageKey = keyof typeof en;
export default en;

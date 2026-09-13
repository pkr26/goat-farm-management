// Stryker disable all: this file is a translation data table; its keys are
// verified by the catalog parity tests and per-key usage assertions, not by
// per-string mutation (mutating every sentence would only measure whether
// each literal is asserted verbatim somewhere).
/**
 * Telugu message catalog (తెలుగు). Typed as Partial over the English key
 * set: an untranslated key simply renders its English text, so this file can
 * grow key-by-key without coordination. Glossary decisions worth noting:
 * status words follow the audit glossary (Completed = పూర్తైనవి), while
 * button labels use the respectful imperative register the glossary itself
 * uses for Save (సేవ్ చేయండి) — "పూర్తయింది" on a button would read as a
 * statement, not an action.
 */

import type { MessageKey } from "./en";

const te: Partial<Record<MessageKey, string>> = {
  // ---------- shared, cross-page words ----------
  "common.cancel": "రద్దు చేయి",
  "common.save": "సేవ్ చేయండి",
  "common.saveChanges": "మార్పులు సేవ్ చేయండి",
  "common.retry": "మళ్ళీ ప్రయత్నించు",
  "common.loading": "లోడ్ అవుతోంది…",
  "common.close": "మూసివేయి",
  "common.yes": "అవును",
  "common.no": "కాదు",
  "common.areYouSure": "మీరు ఖచ్చితంగా చేయాలనుకుంటున్నారా?",
  "common.login": "లాగిన్",
  "common.logout": "లాగ్ అవ్వండి",
  "common.search": "వెతకండి",
  "common.filter": "ఫిల్టర్",
  "common.clear": "తుడిచివేయి",
  "common.note": "గమనిక",
  "common.none": "— ఏదీ లేదు —",
  "common.language": "భాష",
  "common.somethingWentWrong": "ఏదో తప్పు జరిగింది.",

  // ---------- app shell sidebar ----------
  "nav.group.overview": "సారాంశం",
  "nav.group.herd": "మందం",
  "nav.group.healthFeed": "ఆరోగ్యం & మేత",
  "nav.group.operations": "కార్యకలాపాలు",
  "nav.group.business": "వ్యాపారం",
  "nav.dashboard": "డాష్‌బోర్డు",
  "nav.animals": "మేకలు",
  "nav.buckets": "పెంటలు",
  "nav.breeding": "సంతానోత్పత్తి",
  "nav.kidding": "పిల్లల పుట్టుక",
  "nav.health": "ఆరోగ్యం",
  "nav.feeding": "మేత",
  "nav.purchases": "కొనుగోళ్లు",
  "nav.tasks": "పనులు",
  "nav.finance": "ఆర్థికం",
  "nav.planner": "ప్లానర్",
  "nav.simulation": "సిమ్యులేషన్",
  "nav.opsSimulation": "ఆప్స్ సిమ్యులేషన్",
  "nav.reports": "నివేదికలు",
  "nav.team": "బృందం",

  // ---------- tasks board ----------
  "tasks.title": "పనులు",
  "tasks.description": "గడువు తేదీల వారీగా పనులు మరియు ఆటోమేటిక్ పనులు చూపబడతాయి.",
  "tasks.tab.today": "ఈ రోజు",
  "tasks.tab.overdue": "ఆలస్యం",
  "tasks.tab.upcoming": "రాబోయేవి",
  "tasks.tab.awaiting": "ధృవీకరణ కోసం వేచివున్నవి",
  "tasks.tab.completed": "పూర్తైనవి",
  "tasks.pagination.today": "ఈ రోజు పనులు",
  "tasks.pagination.overdue": "ఆలస్య పనులు",
  "tasks.pagination.upcoming": "రాబోయే పనులు",
  "tasks.pagination.awaiting": "ధృవీకరణ కోసం వేచివున్న పనులు",
  "tasks.pagination.completed": "పూర్తైన పనులు",

  "tasks.empty.today": "ఈ రోజు పనులు లేవు.",
  "tasks.empty.overdue": "ఆలస్య పనులు లేవు.",
  "tasks.empty.upcoming": "రాబోయే పనులు లేవు.",
  "tasks.empty.awaiting": "ధృవీకరణ కోసం వేచి ఉన్న పనులు లేవు.",
  "tasks.empty.completed": "పూర్తైన పనులు లేవు.",
  "tasks.empty.fallbackTitle": "ఇంకా పనులు లేవు.",
  "tasks.empty.fallbackGuidance": "కొత్త పనులు మరియు ఆటోమేటిక్ పనులు ఇక్కడ కనిపిస్తాయి.",
  "tasks.guidance.today": "ఈ రోజు ఏ పనీ గడువు దాటలేదు. ఆలస్య పనులు 'ఆలస్యం' ట్యాబ్‌లో కనిపిస్తాయి.",
  "tasks.guidance.overdue": "ఏ పనీ ఆలస్యం కాలేదు. ఈ రోజు పనులు 'ఈ రోజు' ట్యాబ్‌లో కనిపిస్తాయి.",
  "tasks.guidance.upcoming": "ముందు షెడ్యూల్ చేసిన పనులు లేవు. పనులు వచ్చినప్పుడు వాటి ట్యాబ్‌లలో కనిపిస్తాయి.",
  "tasks.guidance.awaiting": "ధృవీకరణ కోసం ఏమీ వేచి లేదు. పూర్తయిన పనులు సమీక్ష కోసం ఇక్కడకు వస్తాయి.",
  "tasks.guidance.completed": "ఇంకా పూర్తైన లేదా వదిలిన పనులు లేవు. మీరు పూర్తి చేసిన పనులు ఇక్కడకు వస్తాయి.",

  "tasks.updatingBoard": "పనుల బోర్డు నవీకరిస్తోంది…",
  "tasks.loadingTasks": "పనులు లోడ్ అవుతున్నాయి…",
  "tasks.loadFailed": "పనులు లోడ్ కాలేదు.",
  "tasks.retryTasks": "పనులను మళ్లీ ప్రయత్నించు",
  "tasks.noAccess": "మీకు ఈ పేజీ యాక్సెస్ లేదు.",

  "tasks.col.due": "గడువు",
  "tasks.col.task": "పని",
  "tasks.col.category": "వర్గం",
  "tasks.col.assignedTo": "అప్పగించబడింది",
  "tasks.col.animal": "మేక",
  "tasks.col.status": "స్థితి",
  "tasks.col.finished": "పూర్తైంది",
  "tasks.everyDays": "ప్రతి {days} రోజులకు",
  "tasks.daysLate": "({days} రోజులు ఆలస్యం)",
  "tasks.sentBack": "తిరిగి పంపబడింది: {note}",
  "tasks.skipReasonLabel": "కారణం: {reason}",
  "tasks.viaRole": "{role} ద్వారా",
  "tasks.awaitingMarker": "వేచివుంది",
  "tasks.by": "{name} చేత",

  "tasks.openForm": "ఫారం తెరవండి",
  "tasks.notDueForm": "ఇంకా గడువు రాలేదు — ఫారం గడువు రోజున తెరుచుకుంటుంది.",
  "tasks.formUnavailable": "మీ అనుమతులతో లింక్ చేసిన ఫారం అందుబాటులో లేదు.",
  "tasks.notDueActions": "ఇంకా గడువు రాలేదు — చర్యలు గడువు రోజున తెరుచుకుంటాయి.",
  "tasks.complete": "పూర్తి చేయండి",
  "tasks.retryComplete": "పూర్తిని మళ్లీ ప్రయత్నించు",
  "tasks.skip": "వదిలివేయి",
  "tasks.verify": "ధృవీకరించు",
  "tasks.retryVerify": "ధృవీకరణను మళ్లీ ప్రయత్నించు",
  "tasks.reject": "తిరస్కరించు…",
  "tasks.retryReject": "తిరస్కారాన్ని మళ్లీ ప్రయత్నించు",
  "tasks.actionErrorSuffix": "పనిని సరిచూసి, మళ్లీ ప్రయత్నించండి.",

  "tasks.skip.title": "ఈ పనిని వదిలివేయాలా?",
  "tasks.skip.body":
    "వదిలివేయడం వల్ల ఈ పని చరిత్రకు వెళ్తుంది. ఎందుకు పూర్తి చేయలేదో బృందం అర్థం చేసుకోవడానికి కారణం చేర్చండి.",
  "tasks.skip.reason": "కారణం (ఐచ్ఛికం)",
  "tasks.skip.confirm": "పనిని వదిలివేయి",
  "tasks.skip.inFlight": "వదిలివేస్తోంది…",
  "tasks.skip.retry": "వదిలివేతను మళ్లీ ప్రయత్నించు",
  "tasks.skip.errorSuffix": "కారణాన్ని సరిచూసి, మళ్లీ ప్రయత్నించండి.",

  "tasks.reject.title": "పనిని తిరస్కరించు",
  "tasks.reject.body":
    "పూర్తి చేసిన ఈ పని కార్మికుడికి తిరిగి వెళ్తుంది. ఏమి సరిచేయాలో వారికి అర్థమయ్యేలా కారణం తప్పనిసరి.",
  "tasks.reject.reason": "కారణం *",
  "tasks.reject.reasonMissing": "కారణం అవసరం.",
  "tasks.reject.confirm": "పనిని తిరస్కరించు",
  "tasks.reject.inFlight": "తిరస్కరిస్తోంది…",

  "tasks.recurConfirm.title": "పునరావృత పనిని పూర్తి చేయాలా?",
  "tasks.recurConfirm.body":
    "ఈ పని ప్రతి {days} రోజులకు ఒకసారి వస్తుంది. ఇప్పుడు పూర్తి చేస్తే తర్వాతి పని {date} నాడు షెడ్యూల్ అవుతుంది.",
  "tasks.recurConfirm.confirm": "పనిని పూర్తి చేయండి",

  "tasks.toast.completed": "పని పూర్తయింది.",
  "tasks.toast.skipped": "పని వదిలివేయబడింది.",
  "tasks.toast.verified": "పని ధృవీకరించబడింది.",
  "tasks.toast.sentBack": "పని తిరిగి పంపబడింది.",
  "tasks.toast.created": "పని సృష్టించబడింది.",
  "tasks.newDuty": "కొత్త పని",

  // New-duty dialog.
  "tasks.form.intro":
    "పాత్రకు (ఆ పాత్ర ఉన్న ప్రతి ఒక్కరూ చూస్తారు) లేదా ఒక నిర్దిష్ట కార్మికుడికి అప్పగించండి. రోజువారీ శుభ్రం లాంటి పునరావృత పనుల కోసం \"ప్రతి ఎన్ని రోజులకు\" సెట్ చేయండి — ఒకటి పూర్తయితే తర్వాతది షెడ్యూల్ అవుతుంది.",
  "tasks.form.titleLabel": "శీర్షిక *",
  "tasks.form.titlePlaceholder": "ఉదా: బ్రీడింగ్ పెంటలో నీళ్ల తొట్లు శుభ్రం చేయడం",
  "tasks.form.titleRequired": "శీర్షిక అవసరం",
  "tasks.form.dueDateLabel": "గడువు తేదీ *",
  "tasks.form.dueRequired": "గడువు తేదీ అవసరం",
  "tasks.form.dueInvalid": "సరైన గడువు తేదీని ఎంచుకోండి",
  "tasks.form.yearBand": "సంవత్సరం 2000 నుండి 2100 మధ్య ఉండాలి",
  "tasks.form.categoryLabel": "వర్గం",
  "tasks.form.recurLabel": "ప్రతి ఎన్ని రోజులకు (పునరావృతం)",
  "tasks.form.recurPlaceholder": "ఖాళీ = ఒక్కసారి",
  "tasks.form.recurInvalid": "(1–{max}) రోజుల్లో పూర్ణ సంఖ్య ఉండాలి",
  "tasks.form.recurTooLate": "పునరావృత తేదీ చాలా ఆలస్యం — తర్వాతి పని షెడ్యూల్ కాదు",
  "tasks.form.animalLabel": "మేక (ఐచ్ఛికం)",
  "tasks.form.animalPlaceholder": "మేక లేదు",
  "tasks.form.animalDialogTitle": "ఈ పని కోసం మేకను ఎంచుకోండి",
  "tasks.form.animalHelp":
    "టీకా లేదా పురుగుల మందు పనులను మేకకు లింక్ చేస్తే సంబంధిత ఆరోగ్య ఫారం తెరుచుకుంటుంది. లింక్ చేయని పనులు మామూలు చెక్‌లిస్టులే.",
  "tasks.form.noAnimalAccess": "మీకు మేకల యాక్సెస్ లేదు — ఈ పని మేక లింక్ లేకుండా సృష్టించబడుతుంది.",
  "tasks.form.assignment": "కేటాయింపు",
  "tasks.form.loadingAssignments": "కేటాయింపు ఎంపికలు లోడ్ అవుతున్నాయి…",
  "tasks.form.assignmentsFailed": "కేటాయింపు ఎంపికలు లోడ్ కాలేదు.",
  "tasks.form.retryAssignments": "కేటాయింపులను మళ్లీ ప్రయత్నించు",
  "tasks.form.assignToRole": "పాత్రకు అప్పగించు",
  "tasks.form.assignToWorker": "లేదా కార్మికుడికి అప్పగించు",
  "tasks.form.workerFallback": "కార్మికుడు",
  "tasks.form.noTeamAccess": "మీకు బృందం యాక్సెస్ లేదు — ఈ పని ఎవరికీ అప్పగించకుండా సృష్టించబడుతుంది.",
  "tasks.form.createErrorSuffix": "పని వివరాలను సరిచూసి, మళ్లీ ప్రయత్నించండి.",
  "tasks.form.creating": "సృష్టిస్తోంది…",
  "tasks.form.retryCreate": "సృష్టింపును మళ్లీ ప్రయత్నించు",
  "tasks.form.create": "పనిని సృష్టించు",

  // ---------- feeding plan (common buttons/toasts) ----------
  "feeding.recordDispensing": "మేత నమోదు చేయండి",
  "feeding.record": "నమోదు చేయండి",
  "feeding.recording": "నమోదు చేస్తోంది…",
  "feeding.dispensedToast": "మేత నమోదు అయింది.",
  "feeding.edit": "సవరించు",
  "feeding.save": "సేవ్ చేయండి",
  "feeding.saving": "సేవ్ చేస్తోంది…",
  "feeding.dailyRation": "రోజువారీ పశుగ్రాసం — {bucket}",
  "feeding.kgPerHead": "రోజుకు ఒక్క మేకకు కిలోలు *",
  "feeding.savedToast": "{bucket} కోసం ఒక్కో మేకకు {kg} కిలో సేవ్ చేయబడింది.",

  // ---------- login ----------
  "login.invalidCredentials": "ఇమెయిల్ లేదా పాస్‌వర్డ్ తప్పు.",
  "login.networkError": "నెట్‌వర్క్ బలహీనంగా ఉంది — దయచేసి కనెక్షన్ సరిచూసి మళ్లీ ప్రయత్నించండి.",
  "login.forgotPassword": "పాస్‌వర్డ్ మర్చిపోయారా?",
  "login.forgotTitle": "పాస్‌వర్డ్ మర్చిపోయారా?",
  "login.forgotBody":
    "కార్మికుల పాస్‌వర్డ్‌లను ఫారం యజమానే 'బృందం' పేజీ నుండి రీసెట్ చేస్తారు. మీరు ఫారం యజమాని అయితే, మీ ఫారం ఆపరేటర్‌ను లేదా సపోర్ట్‌ను సంప్రదించండి — ఇంకా ఇమెయిల్ ద్వారా స్వయం రికవరీ లేదు.",

  // ---------- register ----------
  "register.networkError":
    "నెట్‌వర్క్ బలహీనంగా ఉంది — దయచేసి కనెక్షన్ సరిచూసి మళ్లీ ప్రయత్నించండి.",
};

export default te;

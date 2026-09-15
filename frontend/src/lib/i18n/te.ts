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
  "tasks.skip.reason": "కారణం *",
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

  // ---------- generated duty titles (taskGen) ----------
  "taskGen.pregnancy_check": "గర్భ పరీక్ష: {tag} (జత {breeding_date})",
  "taskGen.return_to_heat_watch":
    "తప్తు పునఃగమన గమనింపు: {tag} — జత అయిన 18–21 రోజులు; మళ్లీ తప్తులోకి వస్తే జత విఫలమైంది; పరిశీలనను వెంటనే నమోదు చేయండి",
  "taskGen.pre_kidding_vaccine": "ప్రసూతికి ముందు ET+TT టీకా: {tag}",
  "taskGen.pre_kidding_vaccine_booster": "ప్రసూతికి ముందు ET+TT టీకా బూస్టర్: {tag}",
  "taskGen.move_to_delivery": "{tag} ను డెలివరీ పెంటకు మార్చండి (సుమారు 2 వారాల్లో ప్రసవం)",
  "taskGen.move_to_pregnancy_late":
    "{tag} ను గర్భం B పెంటకు మార్చండి (గర్భం 100వ రోజు — పశుగ్రాసం పెంపు)",
  "taskGen.birthing_kit_check":
    "జనన కిట్ సరిచూడటం: {tag} — {kidding_date} నాటికి — 7% అయోడిన్+కప్పు, తువ్వాళ్లు, శుభ్రం చేసిన కత్తెర, లూబ్రికెంట్, గ్లోవ్జ్, లాంప్, థర్మామీటర్, ట్యూబ్+సిరింజి, కొలోస్ట్రమ్+ఎలక్ట్రోలైట్లు, బరువు వేలు, చెవి ట్యాగ్లు+యాప్లికేటర్",
  "taskGen.kidding_watch":
    "పిల్లల గమనింపు: {tag} ({kidding_date} నాటికి) — జొన్న నింపు, తొక దగ్గర లిగమెంట్లు, యోని స్రావం చూడండి",
  "taskGen.kidding_watch_due":
    "పిల్లల గమనింపు: {tag} ({kidding_date} నాటికి) — రాత్రంతా ప్రసవ గమనింపు; 30 నిమిషాలు శ్రమించి పురోగతి లేకపోతే సహాయం చేయండి; 15–20 నిమిషాల్లో సరికాకపోతే వెట్ కు కాల్ చేయండి",
  "taskGen.kidding_due": "ప్రసవం రానుంది: {tag}",
  "taskGen.wean_kids": "{tag} పిల్లలకు పాలు తొలగించండి; ఆడ మేక → విశ్రాంతి",
  "taskGen.move_to_resting": "ప్రసవానంతర కోలుకోవడం తర్వాత {tag} ను విశ్రాంతి పెంటకు మార్చండి",
  "taskGen.post_kidding_dam_check":
    "ప్రసవానంతర తల్లి పరీక్ష: {tag} — ప్లాసెంటా బయటపడిందా? జొన్న/మాస్టైటిస్ పరీక్ష, గోరువెచ్చని నీరు, తేలికపాటి మేత, వెనుక భాగం శుభ్రం",
  "taskGen.kidding_stall_cleanout":
    "ప్రసూతి గది శుభ్రం & వ్యాధినివారణ: {tag} — మురికి గడ్డపార తీసేయండి, వ్యాధి నివారణ మందు చల్లండి, పొడి గడ్డపార వేయండి",
  "taskGen.kid_support": "పిల్లల సహాయం: {tag} పిల్లలకు బాటిల్ పాలు / కొలోస్ట్రమ్ రీప్లేసర్",
  "taskGen.rebreed": "{tag} ను తిరిగి జత చేయండి (విశ్రాంతి పూర్తి — ఫ్లష్ కిటకీ ముగిసింది)",
  "taskGen.fmd_vaccination_round":
    "FMD టీకా రౌండ్ ({month} {year}) — అన్ని మేకలకు; పెంట/బ్యాచ్ టీకా ఆరోగ్య నమోదుతో మూసివేయండి",
  "taskGen.et_hs_premonsoon_round": "ET + HS వర్షాకాలపు ముందు రౌండ్ ({year}) — అన్ని మేకలకు",
  "taskGen.goat_pox_round": "మేక మచ్చల (గోట్ పాక్స్) రౌండ్ ({year})",
  "taskGen.ccpp_round": "CCPP రౌండ్ ({year})",
  "taskGen.deworming_round":
    "పురుగుల మందు రౌండ్ ({month} {year}) — పెద్ద మేకలు; 1–6 నెలల పిల్లలకు ప్రతి 3 నెలలకు",
  "taskGen.hoof_trimming_round":
    "గిట్టు కత్తిరింపు రౌండ్ (6 నెలలకు ఒకసారి) — అన్ని వయసుల మేకలకు, మడమ నుండి ముందు వరకు",
  "taskGen.ectoparasite_spray_round":
    "బాహ్య పరాన్నజీవి స్ప్రే/డిప్ రౌండ్ (బ్యుటాక్స్/డెల్టామెత్రిన్) — బరువుగా గర్భం ఉన్న ఆడ మేకలకు అస్సలు వద్దు",
  "taskGen.shed_disinfection_round":
    "గోరు శెడ్ వ్యాధి నివారణ రౌండ్ — వ్యాధి నివారణ మందు + సున్నం; ప్రసూతి గదులపై ప్రత్యేక శ్రద్ధ",
  "taskGen.monthly_weighing_round": "నెలవారీ బరువు రౌండ్ — బరువులు నమోదు చేయండి; పెరుగుదల పెంటలు ముందు",
  "taskGen.morning_feed_routine":
    "ఉదయం దినచర్య: ఉదయం 6:30 మేతకు ముందు దుబ్బులు ఊడ్చండి",
  "taskGen.daily_water_check": "నీరు చూడటం: అన్ని నీటి తొట్లు చూసి నింపండి",
  "taskGen.feed_reorder":
    "{ingredient} తిరిగి కొనండి: {qty_on_hand} కిలో మిగిలుంది (రీఆర్డర్ స్థాయి {reorder_level} కిలో)",
  "taskGen.buck_rotation":
    "మగ మేక {tag} మార్చండి/భర్తీ చేయండి — {age_months} నెలల వయసు (అంతఃసంతాన నిర్వహణ)",
  "taskGen.insurance_renewal": "భీమా నవీకరణ గడువు: పాలసీ {policy_number}",
  "taskGen.quarantine_arrival_inspection":
    "రోజు 0–1: రాక పరీక్ష — నీరు తగ్గడం (చర్మం/చిగుళ్లు), గాయాలు, కుంటి నడక, జ్వరం; అనారోగ్య మేకలను వెంటనే విడదీయండి; క్వారంటైన్ మేకలను చివరగా నడపండి (ప్రత్యేక బూట్లు/పరికరాలు)",
  "taskGen.quarantine_rest":
    "రోజులు 1–3: విశ్రాంతి, ఎలక్ట్రోలైట్/బెల్లం నీరు, పొడి మేత మాత్రమే, దృఢమేత అస్సలు వద్దు",
  "taskGen.quarantine_deworm": "రోజు 4: పురుగుల మందు — ఆల్బెండజోల్/క్లోసాంటెల్ నోటి ద్వారా + ఇవెర్మెక్టిన్ SC",
  "taskGen.quarantine_liver_tonic": "రోజులు 5–9: నీటిలో లివర్ టానిక్ + విటమిన్ AD3E ఇంజెక్షన్",
  "taskGen.quarantine_ppr_vaccine": "రోజు 10: PPR టీకా (లైవ్ వైరల్, SC)",
  "taskGen.quarantine_fecal_exam":
    "రోజు 13: మల/పేడ నమూనా పరీక్ష — రోజు 4 పురుగుల మందు పనిచేసిందా ధృవీకరించండి (ఫలితాన్ని FECAL_EXAM ఆరోగ్య నమోదుగా రాయండి)",
  "taskGen.quarantine_et_tetanus_vaccine": "రోజు 20: ET + ధనుర్వాతం (టాక్సాయిడ్, SC) టీకా",
  "taskGen.quarantine_goat_pox_vaccine": "రోజు 30: మేక మచ్చల (గోట్ పాక్స్) టీకా (లైవ్ వైరల్, SC)",
  "taskGen.quarantine_prerelease_review": "రోజు 30: విడుదలకు ముందు మల మళ్లీ పరీక్ష + క్లినికల్ సమీక్ష",
  "taskGen.quarantine_fmd_vaccine": "రోజు 40: FMD టీకా (కిల్డ్, SC)",
  "taskGen.quarantine_release": "రోజు 45: 10% జింక్ సల్ఫేట్ పాద స్నానం → ఫౌండేషన్ కు విడుదల",
  "taskGen.month.1": "జనవరి",
  "taskGen.month.2": "ఫిబ్రవరి",
  "taskGen.month.3": "మార్చి",
  "taskGen.month.4": "ఏప్రిల్",
  "taskGen.month.5": "మే",
  "taskGen.month.6": "జూన్",
  "taskGen.month.7": "జులై",
  "taskGen.month.8": "ఆగస్టు",
  "taskGen.month.9": "సెప్టెంబర్",
  "taskGen.month.10": "అక్టోబర్",
  "taskGen.month.11": "నవంబర్",
  "taskGen.month.12": "డిసెంబర్",

  // ---------- auth (login / register / brand panel) ----------
  "auth.welcomeBack": "తిరిగి స్వాగతం",
  "auth.signInSubtitle": "మీ ఖాతాలో సైన్ ఇన్ చేయండి",
  "auth.email": "ఇమెయిల్",
  "auth.password": "పాస్‌వర్డ్",
  "auth.signIn": "సైన్ ఇన్",
  "auth.signingIn": "సైన్ ఇన్ అవుతోంది…",
  "auth.noAccount": "ఖాతా లేదా?",
  "auth.registerLink": "నమోదు చేసుకోండి",
  "auth.emailInvalid": "సరైన ఇమెయిల్ చిరునామా ఇవ్వండి",
  "auth.emailTooLong": "ఇమెయిల్ గరిష్ఠంగా 254 అక్షరాలు ఉండాలి",
  "auth.passwordRequired": "పాస్‌వర్డ్ అవసరం",
  "auth.passwordTooLong": "పాస్‌వర్డ్ గరిష్ఠంగా 128 అక్షరాలు ఉండాలి",
  "auth.passwordTooShort": "పాస్‌వర్డ్ కనీసం 12 అక్షరాలు ఉండాలి",
  "auth.passwordHint": "కనీసం 12 అక్షరాలు.",
  "auth.createTitle": "మీ ఖాతా సృష్టించండి",
  "auth.createSubtitle": "నిమిషాల్లో మీ మంద నిర్వహణ ప్రారంభించండి",
  "auth.nameOptional": "పేరు (ఐచ్ఛికం)",
  "auth.creatingAccount": "ఖాతా సృష్టిస్తోంది…",
  "auth.createAccount": "ఖాతా సృష్టించండి",
  "auth.haveAccount": "ఇప్పటికే ఖాతా ఉందా?",
  "auth.brandTitleLine1": "మంద నిర్వహణ,",
  "auth.brandTitleLine2": "సరళంగా.",
  "auth.brandTagline":
    "మొదటి ట్యాగ్ నుండి చివరి అమ్మకం వరకు — ఆరోగ్యకరమైన, లాభదాయకమైన ఫారం నడపండి.",
  "auth.featureRecordsTitle": "పూర్తి మంద రికార్డులు",
  "auth.featureRecordsDesc": "ప్రతి మేక, ట్యాగ్ మరియు వంశావళిని ఒకే చోట గమనించండి.",
  "auth.featureHealthTitle": "ముందస్తు ఆరోగ్య సంరక్షణ",
  "auth.featureHealthDesc": "టీకాలు, చికిత్సలు, పరీక్షలు ముందుగా ప్లాన్ చేసుకోండి.",
  "auth.featureInsightsTitle": "లాభాలు చూపే సమాచారం",
  "auth.featureInsightsDesc": "సంతానోత్పత్తి, పిల్లల పుట్టుక, ఆర్థిక నివేదికలు ఒకే చూపులో.",
  "auth.brandFoot": "తెలంగాణ అంతటా ఉస్మానాబాదీ మేక మందల కోసం.",

  // ---------- app shell ----------
  "shell.skipToContent": "కంటెంట్ కు వెళ్లండి",
  "shell.tagline": "మేక ఫారం నిర్వహణ",
  "shell.passwordChangeNotice":
    "ఈ పాస్‌వర్డ్ ఫారం యజమాని ఇచ్చింది — కొనసాగే ముందు దాన్ని మార్చండి (ఖాతా → పాస్‌వర్డ్ మార్చండి). మీరు మార్చే వరకు ఫారం పేజీలు, చర్యలు బ్లాక్ అవుతాయి.",

  // ---------- browser tab titles ----------
  "doc.title.dashboard": "డాష్‌బోర్డు",
  "doc.title.addAnimal": "మేక చేర్చు",
  "doc.title.animal": "మేక",
  "doc.title.animals": "మేకలు",
  "doc.title.buckets": "పెంటలు",
  "doc.title.ultrasound": "అల్ట్రాసౌండ్",
  "doc.title.breeding": "సంతానోత్పత్తి",
  "doc.title.recordBirth": "పుట్టుక నమోదు",
  "doc.title.births": "పుట్టుకలు",
  "doc.title.addHealthEvent": "ఆరోగ్య నమోదు చేర్చు",
  "doc.title.vaccinationSchedule": "టీకాల షెడ్యూల్",
  "doc.title.health": "ఆరోగ్యం",
  "doc.title.feedInventory": "మేత నిల్వ",
  "doc.title.feedRecipes": "మేత రెసిపీలు",
  "doc.title.feeding": "మేత",
  "doc.title.purchases": "కొనుగోళ్లు",
  "doc.title.tasks": "పనులు",
  "doc.title.finance": "ఆర్థికం",
  "doc.title.planner": "ప్లానర్",
  "doc.title.simulation": "సిమ్యులేషన్",
  "doc.title.opsSimulation": "ఆప్స్ సిమ్యులేషన్",
  "doc.title.reports": "నివేదికలు",
  "doc.title.team": "బృందం",
  "doc.title.noAccess": "యాక్సెస్ లేదు",

  // ---------- health event log ----------
  "health.notForSaleUntil": "{date} వరకు అమ్మకానికి వద్దు",

  // ---------- feeding page static guidance ----------
  "feeding.shiftsLine":
    "షిఫ్టులు: {morning} ఉదయం 6:30 (ముందు దుబ్బులు ఊడ్చండి) · {afternoon} మధ్యాహ్నం 1:30 · {night} రాత్రి 7:30.",
  "feeding.phase.maintenance": "మెయింటెనెన్స్",
  "feeding.phase.flush": "ఫ్లష్",
  "feeding.frameBuilder": "ఫ్రేమ్-బిల్డర్",
  "feeding.fattening": "కొవ్వు పెంపు",
  "feeding.rotationNote":
    "{resting} 10వ రోజున {maintenance} → {flush} కు మారుతుంది; {maleKids} 91వ రోజున {frameBuilder} → {fattening} కు.",

  // ---------- animal phenotype ----------
  "animals.coatColor": "వెంట్రుకల రంగు",
  "animals.coatColor.black": "నలుపు",
  "animals.coatColor.black_patched": "నలుపు మచ్చలతో",
  "animals.coatColor.brown": "గోధుమ",
  "animals.coatColor.white": "తెలుపు",
  "animals.coatColor.spotted": "మచ్చల",
  "animals.horned": "కొమ్ములు",
  "animals.editPhenotype": "లక్షణాలు సవరించండి",
  "animals.phenotypeSaved": "లక్షణాలు సేవ్ అయ్యాయి.",
  "animals.notRecorded": "నమోదు లేదు",
  "common.unknown": "తెలియదు",

  // ---------- dashboard advisories ----------
  "dashboard.advisory.bakridHold":
    "{count} మగ మేకలు బక్రీద్ ({date}) కు 2 నెలల లోపు పూర్తవుతాయి — పండుగ ధర కోసం ఆపి ఉంచండి.",
};

export default te;

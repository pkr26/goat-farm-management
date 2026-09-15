"""Permission catalog and role presets for the RBAC system.

Permissions are code-defined strings ("module.action"). A Role bundles any set
of them; the farm owner implicitly holds ALL_PERMISSIONS. Role presets are
seeded per farm (see seed.seed_default_roles) and can be edited or extended
with custom roles by the owner.

Keep this module free of SQLAlchemy imports so routers, services and tests can
use it anywhere.
"""

from typing import TypedDict

# (code, label) — ordered for the role editor's permission matrix, grouped by
# module in PERMISSION_GROUPS below.
PERMISSIONS: list[tuple[str, str]] = [
    ("dashboard.view", "View dashboard"),
    ("animals.view", "View animals"),
    ("animals.create", "Add animals"),
    ("animals.move", "Move animals between buckets"),
    ("animals.weight", "Record weights"),
    ("animals.status", "Mark sold / dead / culled"),
    ("buckets.view", "View bucket board"),
    ("breeding.view", "View breeding records"),
    ("breeding.manage", "Add breedings / record ultrasounds"),
    ("kidding.view", "View kidding records"),
    ("kidding.manage", "Record kiddings"),
    ("health.view", "View health events & vaccine schedule"),
    ("health.manage", "Record health events"),
    ("purchases.view", "View purchase batches & quarantine"),
    ("purchases.manage", "Create purchase batches"),
    ("feeding.view", "View feeding plan, recipes, inventory"),
    ("feeding.manage", "Record dispensing, mix batches, add stock"),
    ("tasks.view", "View assigned duties"),
    ("tasks.create", "Create & assign duties"),
    ("tasks.complete", "Complete duties"),
    ("tasks.verify", "Verify completed duties"),
    ("finance.view", "View transactions & P&L"),
    ("finance.manage", "Record transactions"),
    ("simulation.view", "View & run simulations"),
    ("simulation.manage", "Save & manage scenarios"),
    ("reports.view", "View reports"),
    ("team.manage", "Manage team, roles & passwords"),
]

ALL_PERMISSIONS: set[str] = {code for code, _ in PERMISSIONS}

# An action permission is not usable unless its module can also be viewed.
# The frontend mirrors this mapping for immediate editor feedback, while the
# API remains authoritative for direct and older clients.
PERMISSION_DEPENDENCIES: dict[str, str] = {
    "animals.create": "animals.view",
    "animals.move": "animals.view",
    "animals.weight": "animals.view",
    "animals.status": "animals.view",
    "breeding.manage": "breeding.view",
    "kidding.manage": "kidding.view",
    "health.manage": "health.view",
    "purchases.manage": "purchases.view",
    "feeding.manage": "feeding.view",
    "tasks.create": "tasks.view",
    "tasks.complete": "tasks.view",
    "tasks.verify": "tasks.view",
    "finance.manage": "finance.view",
    "simulation.manage": "simulation.view",
}

# (group label, [permission codes]) — drives the role editor matrix.
PERMISSION_GROUPS: list[tuple[str, list[str]]] = [
    ("Dashboard", ["dashboard.view"]),
    (
        "Animals",
        ["animals.view", "animals.create", "animals.move", "animals.weight", "animals.status"],
    ),
    ("Buckets", ["buckets.view"]),
    ("Breeding", ["breeding.view", "breeding.manage"]),
    ("Kidding", ["kidding.view", "kidding.manage"]),
    ("Health", ["health.view", "health.manage"]),
    ("Purchases", ["purchases.view", "purchases.manage"]),
    ("Feeding", ["feeding.view", "feeding.manage"]),
    ("Tasks / duties", ["tasks.view", "tasks.create", "tasks.complete", "tasks.verify"]),
    ("Finance", ["finance.view", "finance.manage"]),
    ("Simulation", ["simulation.view", "simulation.manage"]),
    ("Reports", ["reports.view"]),
    ("Team", ["team.manage"]),
]


class _RolePresetFields(TypedDict):
    code: str
    name: str
    description: str
    permissions: list[str]


class RolePreset(_RolePresetFields):
    pass


# Role presets seeded for every farm. `code` is the stable key used to map
# auto-generated tasks to a default role (TASK_CATEGORY_ROLE_MAP) and must not
# be changed once seeded; the display name/description/permissions stay
# editable by the owner. Presets cannot be deleted (startup seeding would
# re-create them), so every entry must earn its place on the team page.
ROLE_PRESETS: list[RolePreset] = [
    {
        "code": "MANAGER",
        "name": "Farm Manager",
        "description": (
            "Runs day-to-day operations end to end: herd, breeding, health, feeding, "
            "purchases and duties. No payroll, simulations or team admin."
        ),
        "permissions": [
            "dashboard.view",
            "animals.view",
            "animals.create",
            "animals.move",
            "animals.weight",
            "animals.status",
            "buckets.view",
            "breeding.view",
            "breeding.manage",
            "kidding.view",
            "kidding.manage",
            "health.view",
            "health.manage",
            "purchases.view",
            "purchases.manage",
            "feeding.view",
            "feeding.manage",
            "tasks.view",
            "tasks.create",
            "tasks.complete",
            "tasks.verify",
            "finance.view",
            "simulation.view",
            "reports.view",
        ],
    },
    {
        "code": "MOVER",
        "name": "Animal Mover",
        "description": "Moves animals between buckets; completes bucket-move duties.",
        "permissions": [
            "dashboard.view",
            "animals.view",
            "animals.move",
            "buckets.view",
            "tasks.view",
            "tasks.complete",
        ],
    },
    {
        "code": "VET",
        "name": "Veterinarian",
        "description": (
            "Examines animals, records health events, ultrasounds, vaccinations and kiddings."
        ),
        # Kidding is part of the job, not an extra: the vet attends the
        # difficult deliveries and KIDDING_DUE duties are auto-assigned here
        # (TASK_CATEGORY_ROLE_MAP). Those duties refuse the bare complete
        # button, so without kidding.view/kidding.manage the assignee could
        # neither open the kidding form nor record the kidding.
        "permissions": [
            "dashboard.view",
            "animals.view",
            "animals.weight",
            "buckets.view",
            "breeding.view",
            "breeding.manage",
            "kidding.view",
            "kidding.manage",
            "health.view",
            "health.manage",
            "tasks.view",
            "tasks.complete",
        ],
    },
    {
        "code": "BUYER",
        "name": "Procurement Officer",
        "description": (
            "Buys new stock, creates purchase batches and runs the 45-day "
            "quarantine intake with the vet."
        ),
        "permissions": [
            "dashboard.view",
            "animals.view",
            "animals.create",
            "buckets.view",
            "health.view",
            "purchases.view",
            "purchases.manage",
            "tasks.view",
            "tasks.complete",
        ],
    },
    {
        "code": "FEEDER",
        "name": "Feeder",
        "description": "Runs the 3-shift feeding plan, records dispensing and feed mixing.",
        "permissions": [
            "dashboard.view",
            "feeding.view",
            "feeding.manage",
            "buckets.view",
            "tasks.view",
            "tasks.complete",
        ],
    },
    {
        "code": "CLEANER",
        "name": "Cleaner",
        "description": "Performs cleaning duties and marks them done.",
        "permissions": ["dashboard.view", "tasks.view", "tasks.complete"],
    },
    {
        "code": "CLEANER_MANAGER",
        "name": "Cleaner Manager",
        "description": "Verifies cleaning duties done by cleaners; rejects sloppy work.",
        "permissions": [
            "dashboard.view",
            "tasks.view",
            "tasks.complete",
            "tasks.verify",
        ],
    },
    {
        "code": "ACCOUNTANT",
        "name": "Accountant",
        "description": (
            "Records income and expenses and watches the P&L; no herd or parlour duties."
        ),
        # Office duties only: INSURANCE (policy renewals) routes here via
        # TASK_CATEGORY_ROLE_MAP, and a duty-facing role must be able to open
        # and close its duties.
        "permissions": [
            "dashboard.view",
            "animals.view",
            "tasks.view",
            "tasks.complete",
            "finance.view",
            "finance.manage",
            "reports.view",
        ],
    },
    {
        "code": "VIEWER",
        "name": "Auditor (read-only)",
        "description": ("Read-only access across the farm for investors, consultants and lenders."),
        "permissions": [
            "dashboard.view",
            "animals.view",
            "buckets.view",
            "breeding.view",
            "kidding.view",
            "health.view",
            "purchases.view",
            "feeding.view",
            "tasks.view",
            "finance.view",
            "simulation.view",
            "reports.view",
        ],
    },
]

# The finite database vocabulary for Role.code. Custom roles always use NULL;
# this identity is server-owned and never accepted from an API payload.
ROLE_PRESET_CODES: frozenset[str] = frozenset(preset["code"] for preset in ROLE_PRESETS)


# Auto-generated tasks default to the farm role with this code (if seeded).
TASK_CATEGORY_ROLE_MAP: dict[str, str] = {
    "ULTRASOUND": "VET",
    "VACCINE": "VET",
    "DEWORMING": "VET",
    "QUARANTINE": "VET",
    "KIDDING_DUE": "VET",
    "BUCKET_MOVE": "MOVER",
    "WEANING": "MOVER",
    "FEED": "FEEDER",
    "CLEANING": "CLEANER",
    # Husbandry-standards categories. INSURANCE is the one office duty:
    # policy renewals are the accountant's paperwork, not the crew's.
    "KIDDING_WATCH": "CLEANER",
    "BIRTHING_KIT": "MANAGER",
    "HEALTH_CHECK": "VET",
    "HEAT_WATCH": "CLEANER",
    "HOOF_TRIMMING": "VET",
    "SPRAYING": "VET",
    "DISINFECTION": "CLEANER",
    "WEIGHING": "MOVER",
    "REBREED": "MANAGER",
    "BUCK_ROTATION": "MANAGER",
    "INSURANCE": "ACCOUNTANT",
    # The daily trough round is the feed crew's work, like the feed routine.
    "WATER": "FEEDER",
}


def preset_codes() -> set[str]:
    """Preset codes every farm is seeded with."""
    return {preset["code"] for preset in ROLE_PRESETS}


# Every preset code a generated duty can route to.
TASK_ROLE_CODES: frozenset[str] = frozenset(TASK_CATEGORY_ROLE_MAP.values())


def task_role_codes(category: str) -> tuple[str, ...]:
    """Ordered candidate preset codes for a generated duty."""
    base = TASK_CATEGORY_ROLE_MAP.get(category)
    return (base,) if base is not None else ()


# What an assignee must be able to DO with each auto-assigned category, on top
# of the tasks.view / tasks.complete every duty-facing preset role holds
# (the read-only VIEWER receives no duties by design; the ACCOUNTANT receives
# only office duties — INSURANCE — and holds the pair for them).
#
# A form-linked duty (api._shared.task_action_url) refuses the bare complete
# button: completing it means recording data, so it can only be closed by
# submitting the form it points at. Its assignee therefore needs the
# permission guarding that form's endpoint —
#
#   ULTRASOUND  -> POST /api/breeding/{id}/ultrasound  (breeding.manage)
#   VACCINE     -> POST /api/health/events             (health.manage)
#   DEWORMING   -> POST /api/health/events             (health.manage)
#   KIDDING_DUE -> POST /api/kidding                   (kidding.manage)
#
# — otherwise the duty lands on a role that structurally cannot close it.
# The remaining categories are closed by the generic complete endpoint; their
# side effects (quarantine release, bucket moves, weaning) run server-side
# under tasks.complete alone. Module view permissions are implied through
# PERMISSION_DEPENDENCIES and are not repeated here.
# The husbandry-standards categories are all generic-completed for now; each
# grows its action permission when the domain form that records it lands (the
# invariant tests then hold the map and the form in lockstep).
TASK_CATEGORY_ACTION_PERMISSIONS: dict[str, frozenset[str]] = {
    "ULTRASOUND": frozenset({"breeding.manage"}),
    "VACCINE": frozenset({"health.manage"}),
    "DEWORMING": frozenset({"health.manage"}),
    "QUARANTINE": frozenset(),
    "KIDDING_DUE": frozenset({"kidding.manage"}),
    "BUCKET_MOVE": frozenset(),
    "WEANING": frozenset(),
    "FEED": frozenset(),
    "CLEANING": frozenset(),
    "KIDDING_WATCH": frozenset(),
    "BIRTHING_KIT": frozenset(),
    "HEALTH_CHECK": frozenset(),
    "HEAT_WATCH": frozenset(),
    "HOOF_TRIMMING": frozenset(),
    "SPRAYING": frozenset(),
    "DISINFECTION": frozenset(),
    "WEIGHING": frozenset(),
    "REBREED": frozenset(),
    "BUCK_ROTATION": frozenset(),
    "INSURANCE": frozenset(),
    "WATER": frozenset(),
}

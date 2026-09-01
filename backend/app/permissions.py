"""Permission catalog and role presets for the RBAC system.

Permissions are code-defined strings ("module.action"). A Role bundles any set
of them; the farm owner implicitly holds ALL_PERMISSIONS. Role presets are
seeded per farm (see seed.seed_default_roles) and can be edited or extended
with custom roles by the owner. A preset scoped with ``farm_types`` is seeded
only on farms of those types (dairy parlour roles are meaningless on a meat
goat farm).

Keep this module free of SQLAlchemy imports so routers, services and tests can
use it anywhere.
"""

from typing import TypedDict

# Farm-type vocabulary mirrored from models.enums.FarmType (kept as plain
# strings so this module stays import-free).
GOAT = "GOAT"
BUFFALO_DAIRY = "BUFFALO_DAIRY"
FARM_TYPES = (GOAT, BUFFALO_DAIRY)

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
    ("milk.view", "View milk yield records & herd totals"),
    ("milk.manage", "Record per-animal milk yields"),
    ("milk.quality", "Record fat tests (sets the ₹/kg-fat pricing input)"),
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
    "milk.manage": "milk.view",
    # Fat testing rides the same parlour form as yield recording, so a
    # quality-only role could never reach the endpoint that uses it.
    "milk.quality": "milk.manage",
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
    ("Milk", ["milk.view", "milk.manage", "milk.quality"]),
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


class RolePreset(_RolePresetFields, total=False):
    # Farm types this preset is seeded on; omit for every farm type. Dairy
    # parlour presets (milker, calf shed, milk QC) set ["BUFFALO_DAIRY"].
    farm_types: list[str]


# Role presets seeded for every farm. `code` is the stable key used to map
# auto-generated tasks to a default role (TASK_CATEGORY_ROLE_MAP) and must not
# be changed once seeded; the display name/description/permissions stay
# editable by the owner. Presets cannot be deleted (startup seeding would
# re-create them), so every entry must earn its place on the team page —
# farm-type scoping keeps dairy-only roles off goat farms.
ROLE_PRESETS: list[RolePreset] = [
    {
        "code": "MANAGER",
        "name": "Farm Manager",
        "description": (
            "Runs day-to-day operations end to end: herd, breeding, health, feeding, "
            "milking, purchases and duties. No payroll, simulations or team admin."
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
            "milk.view",
            "milk.manage",
            "milk.quality",
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
            "milk.view",
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
        # milk.view only: seeing yields groups the ration, but recording milk
        # (and fat tests) belongs to the parlour roles — a feed error must not
        # be correctable by editing the milk ledger.
        "permissions": [
            "dashboard.view",
            "feeding.view",
            "feeding.manage",
            "milk.view",
            "buckets.view",
            "tasks.view",
            "tasks.complete",
        ],
    },
    {
        "code": "MILKER",
        "name": "Milking Attendant",
        "description": (
            "Records per-shift milk yields in the parlour; fat testing stays with "
            "the milk quality / manager roles."
        ),
        "farm_types": [BUFFALO_DAIRY],
        "permissions": [
            "dashboard.view",
            "animals.view",
            "buckets.view",
            "milk.view",
            "milk.manage",
            "tasks.view",
            "tasks.complete",
        ],
    },
    {
        "code": "MILK_QC",
        "name": "Milk Quality Supervisor",
        "description": (
            "Owns fat testing and milk-record corrections — the recorder who "
            "keys litres never sets the fat number that pricing pays on."
        ),
        "farm_types": [BUFFALO_DAIRY],
        "permissions": [
            "dashboard.view",
            "animals.view",
            "buckets.view",
            "milk.view",
            "milk.manage",
            "milk.quality",
            "tasks.view",
            "tasks.complete",
        ],
    },
    {
        "code": "CALF_ATTENDANT",
        "name": "Calf-shed Attendant",
        "description": (
            "Cares for calves in the calf shed: separation moves, calf buckets "
            "and the 90-day milk-weaning duties."
        ),
        "farm_types": [BUFFALO_DAIRY],
        "permissions": [
            "dashboard.view",
            "animals.view",
            "animals.move",
            "buckets.view",
            "kidding.view",
            "milk.view",
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
        "permissions": [
            "dashboard.view",
            "animals.view",
            "finance.view",
            "finance.manage",
            "reports.view",
        ],
    },
    {
        "code": "VIEWER",
        "name": "Auditor (read-only)",
        "description": (
            "Read-only access across the farm for investors, consultants and lenders."
        ),
        "permissions": [
            "dashboard.view",
            "animals.view",
            "buckets.view",
            "breeding.view",
            "kidding.view",
            "health.view",
            "purchases.view",
            "feeding.view",
            "milk.view",
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


def preset_codes_for_farm_type(farm_type: str) -> set[str]:
    """Preset codes a farm of this type is seeded with."""
    return {
        preset["code"]
        for preset in ROLE_PRESETS
        if farm_type in preset.get("farm_types", FARM_TYPES)
    }


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
}

# Dairy overrides on top of TASK_CATEGORY_ROLE_MAP. Goat weaning (day 60) is a
# pen move the mover already performs; dairy weaning (day ~90) is the calf
# shed's whole job — calf feeding, milk-step-down and the move to FOUNDATION —
# so the duty lands on the CALF_ATTENDANT preset instead.
DAIRY_TASK_CATEGORY_ROLE_MAP: dict[str, str] = {
    "WEANING": "CALF_ATTENDANT",
}

# Every preset code a generated duty can route to, across farm types.
TASK_ROLE_CODES: frozenset[str] = frozenset(
    [*TASK_CATEGORY_ROLE_MAP.values(), *DAIRY_TASK_CATEGORY_ROLE_MAP.values()]
)


def task_role_codes(farm_type: str, category: str) -> tuple[str, ...]:
    """Ordered candidate preset codes for a generated duty on a farm of this
    type: the dairy override first (dairy farms), then the base code as the
    fallback when the override's role is not seeded or was tombstoned."""
    base = TASK_CATEGORY_ROLE_MAP.get(category)
    if base is None:
        return ()
    override = (
        DAIRY_TASK_CATEGORY_ROLE_MAP.get(category) if farm_type == BUFFALO_DAIRY else None
    )
    return (override, base) if override is not None else (base,)

# What an assignee must be able to DO with each auto-assigned category, on top
# of the tasks.view / tasks.complete every duty-facing preset role holds
# (officer presets — ACCOUNTANT, VIEWER — receive no duties by design).
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
}

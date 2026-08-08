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


class RolePreset(TypedDict):
    code: str
    name: str
    description: str
    permissions: list[str]


# Role presets seeded for every farm. `code` is the stable key used to map
# auto-generated tasks to a default role (TASK_CATEGORY_ROLE_MAP) and must not
# be changed once seeded; the display name/description/permissions stay
# editable by the owner.
ROLE_PRESETS: list[RolePreset] = [
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
        "description": "Examines animals, records health events, ultrasounds and vaccinations.",
        "permissions": [
            "dashboard.view",
            "animals.view",
            "animals.weight",
            "buckets.view",
            "breeding.view",
            "breeding.manage",
            "health.view",
            "health.manage",
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
]

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

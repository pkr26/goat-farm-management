"""Compare unchanged physical/cash contracts with pre-remediation HEAD, using no DB."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['GOATFARM_TEST_DB'] = 'goatfarm_test_fix29_domain_104'
os.environ['GOATFARM_DATABASE_URL'] = 'postgresql+asyncpg://localhost:5432/goatfarm_test_fix29_domain_104'
os.environ['GOATFARM_MIGRATION_DATABASE_URL'] = os.environ['GOATFARM_DATABASE_URL']
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _run_core

old_code = subprocess.run(['git', 'show', 'HEAD:backend/app/simulation/engine.py'], cwd=ROOT, text=True, capture_output=True, check=True).stdout
with tempfile.TemporaryDirectory(prefix='goatfarm-fix29-old-engine-') as folder:
    old_path = Path(folder) / 'engine.py'
    old_path.write_text(old_code)
    name = 'app.simulation._pre_remediation_control'
    spec = importlib.util.spec_from_file_location(name, old_path)
    assert spec is not None and spec.loader is not None
    old_module = importlib.util.module_from_spec(spec)
    sys.modules[name] = old_module
    spec.loader.exec_module(old_module)
    rng = random.Random(261029)
    max_difference = 0.0
    old_time = new_time = 0.0
    cases = 40
    for _ in range(cases):
        a = SimulationAssumptions()
        a.herd.does = rng.randint(0, 200)
        a.herd.bucks = rng.randint(0, 8)
        a.meta.horizon_months = rng.choice([12, 24, 60, 120])
        a.mortality.adult = rng.uniform(0, .3)
        a.culling.doe_cull_rate_annual = rng.uniform(0, .5)
        a.finance.income_tax_rate = 0
        begin = time.perf_counter()
        old = old_module._run_core(a)
        old_time += time.perf_counter()-begin
        begin = time.perf_counter()
        new = _run_core(a)
        new_time += time.perf_counter()-begin
        for before, after in zip(old.months, new.months, strict=True):
            for field in ('total_herd', 'births', 'deaths', 'culls_head', 'sales_head', 'purchases_head',
                          'breeding_stock_capex', 'net_cash_flow', 'terminal_value', 'feed_cost'):
                delta = abs(getattr(before, field)-getattr(after, field))
                max_difference = max(max_difference, delta)
                assert delta < 1e-6, (field, delta)
        assert abs(old.npv-new.npv) < 1e-6
    print(json.dumps({'cases':cases,'basis':'40 seeded valid herd/rate/horizon combinations with zero tax; compare all months physical flow, cash and terminal value against HEAD; no scheduled-event rotation changes included', 'max_absolute_difference':max_difference,'old_core_seconds':old_time,'new_core_seconds':new_time,'time_ratio':new_time/old_time},indent=2))

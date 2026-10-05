"""Independent finite numerical controls, not application/test-source changes.

Oracle: exact Fraction polynomial products, using exactly representable binary
coefficients. For P(z), z=(1+r)^(-1/periods); hence r=q^(-periods)-1
for every known positive root q. Cashflow times are index/periods.
No database, network, providers, NumPy root solver, or production data.
"""
import json
import math
import os
from fractions import Fraction as F
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.update(GOATFARM_TEST_DB='goatfarm_test_a26_domain_numerics', GOATFARM_DATABASE_URL='postgresql+asyncpg://localhost:5432/goatfarm_test_a26_domain_numerics', GOATFARM_MIGRATION_DATABASE_URL='postgresql+asyncpg://localhost:5432/goatfarm_test_a26_domain_numerics')
from app.simulation.finance import IRR_BRACKET, IRRIsolationUnsupported, amortization_schedule, assess_irr, irr_roots, npv
from app.simulation.assumptions import FinanceAssumptions

def multiply(a, b):
    out = [F(0)] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            out[i + j] += x * y
    return out

def polynomial(roots):
    out = [F(1)]
    for root in roots:
        out = multiply(out, [-root, F(1)])
    return out

known = [
    ('unique_crossing', [F(7,8)]),
    ('two_crossings', [F(7,8), F(9,8)]),
    ('unique_tangent', [F(1), F(1)]),
    ('two_tangencies', [F(7,8), F(7,8), F(9,8), F(9,8)]),
    ('flat_odd_and_simple', [F(15,16)] * 3 + [F(17,16)]),
    ('tangent_and_simple', [F(7,8), F(9,8), F(9,8)]),
    ('close_pair', [F(1)-F(1,2**20), F(1)+F(1,2**20)]),
    ('period_dependent_bracket', [F(3,4), F(1), F(3,2)]),
    ('outside_roots_excluded', [F(1,16), F(1), F(128)]),
]
cases = [(name, polynomial(roots), roots) for name, roots in known]
# Strictly positive quadratic contributes no real roots; tests distinguish
# sign variations from actual mathematical ambiguity.
positive = [F(17,16), F(-2), F(1)]
cases.extend([
    ('no_root_positive_quadratic', positive, []),
    ('unique_with_extra_sign_variations', multiply(polynomial([F(15,16)]), positive), [F(15,16)]),
])
start = time.perf_counter()
results = []
for name, coefficients, known_roots in cases:
    for periods in (1, 12):
        for scale in (F(1,2**32), F(1), F(2**32)):
            exact = [c * scale for c in coefficients]
            flows = [float(c) for c in exact]
            assert all(F.from_float(x) == c for x, c in zip(flows, exact)), (name, 'inexact input')
            times = [i / periods for i in range(len(flows))]
            expected = sorted(set(float(q**(-periods)-1) for q in known_roots if IRR_BRACKET[0] < float(q**(-periods)-1) < IRR_BRACKET[1]))
            assessment = assess_irr(flows, times)
            roots = irr_roots(flows, times)
            status = 'multiple_roots' if len(expected)>1 else 'unique' if expected else 'no_root'
            error = max((abs(x-y) for x,y in zip(roots,expected)), default=0)
            residuals = [abs(npv(r,flows,times)) / max(sum(abs(x) for x in flows),1e-300) for r in roots]
            passed = assessment.status == status and list(assessment.roots) == roots and len(roots)==len(expected) and error < 5e-10 and max(residuals, default=0)<1e-8
            results.append({'name':name, 'periods_per_year':periods, 'scale':str(scale), 'coefficients_exact_binary':True, 'expected_status':status, 'actual_status':assessment.status, 'expected_roots':expected, 'actual_roots':roots, 'max_rate_error':error, 'max_scaled_npv_residual':max(residuals,default=0), 'passed':passed})
# The product explicitly refuses certification beyond its exact isolation
# budget; these are supported contract outcomes, not missed-root bugs.
domain_checks=[]
for name, flows, times, expected in [
    ('identically_zero', [1.,-1.], [0.,0.], 'indeterminate'),
    ('nonconventional_25_terms', [float(x) for x in polynomial([F(1)]*24)], [float(i) for i in range(25)], 'indeterminate'),
    ('unsupported_irregular_periods', [1.,-2.,1.], [0., math.sqrt(2), 3.], 'indeterminate'),
    ('same_timestamp_aggregation', [-1.,.5,1.], [0.,0.,1.], 'unique'),
]:
    assessment=assess_irr(flows,times)
    raised=False
    try: roots=irr_roots(flows,times)
    except IRRIsolationUnsupported: raised=True; roots=[]
    passed=assessment.status==expected and (raised==(expected=='indeterminate'))
    if name=='same_timestamp_aggregation': passed=passed and abs(roots[0]-1)<1e-12
    domain_checks.append({'name':name,'expected':expected,'actual':assessment.status,'irr_roots_raises_unsupported':raised,'roots':roots,'passed':passed})

loan_results=[]
for principal, annual_rate, term, moratorium in [
    (120000.,0.,12,0),
    (120000.,0.,13,12),
    (100000.,.125,72,12),
    (1e9,.5,180,60),
    (.01,2**-60,180,60),
]:
    FinanceAssumptions(interest_rate_annual=annual_rate,loan_term_months=term,moratorium_months=moratorium)
    rows=amortization_schedule(principal,annual_rate,term,moratorium)
    # Independent discrete recurrence and accounting identities.
    principal_error=abs(math.fsum(row.principal for row in rows)-principal)
    balance_error=max(abs(row.opening_balance-row.principal-row.closing_balance) for row in rows)
    payment_error=max(abs(row.payment-row.principal-row.interest) for row in rows)
    chain_error=max([abs(rows[i].closing_balance-rows[i+1].opening_balance) for i in range(len(rows)-1)] or [0.])
    zero_moratorium=all(row.principal==0 for row in rows[:moratorium])
    # Effective annual discount matches contractual nominal annual/12 accrual.
    effective_rate=math.expm1(12*math.log1p(annual_rate/12))
    payment_npv=npv(effective_rate,[-principal]+[row.payment for row in rows],[i/12 for i in range(term+1)])
    tol=max(principal*2e-12,1e-12)
    passed=all(error<=tol for error in [principal_error,balance_error,payment_error,chain_error,abs(payment_npv)]) and rows[-1].closing_balance==0 and zero_moratorium
    loan_results.append({'principal':principal,'nominal_annual_rate':annual_rate,'term_months':term,'moratorium_months':moratorium,'sum_principal_error':principal_error,'balance_identity_error':balance_error,'payment_identity_error':payment_error,'chain_error':chain_error,'final_balance':rows[-1].closing_balance,'effective_annual_discount_rate':effective_rate,'lender_cashflow_npv':payment_npv,'tolerance':tol,'passed':passed})

# Precision stress is intentionally outside meaningful rupee materiality.
# Keep it explicit rather than certifying universal scale invariance or
# treating 10^-72-rupee differences as a demonstrated business defect.
precision=[]
for scale in [F(1,2**200),F(1,2**240)]:
    coefficients=polynomial([F(7,8),F(9,8)])
    flows=[float(c*scale) for c in coefficients]
    roots=irr_roots(flows,[0.,1.,2.])
    expected=sorted([float(F(7,8)**-1-1),float(F(9,8)**-1-1)])
    precision.append({'scale':str(scale),'largest_absolute_cashflow':max(abs(x) for x in flows),'expected_roots':expected,'actual_roots':roots,'interpretation':'Absolute Decimal zero tolerance dominates far below currency materiality; not promoted to a business issue.'})
output={'oracle':'Exact Fraction polynomial expansion; all finite-control cashflow coefficients exactly representable in binary64; rates independently q**(-periods)-1. Monthly times are intentionally rational periods represented by ordinary floats. Bracket controls avoid numerically ambiguous endpoints.','root_control_count':len(results),'root_controls_passed':sum(x['passed'] for x in results),'domain_checks':domain_checks,'loan_checks':loan_results,'root_controls':results,'precision_stress_limitations':precision,'elapsed_seconds':time.perf_counter()-start}
print(json.dumps(output,indent=2,allow_nan=False))
assert all(x['passed'] for x in results+domain_checks+loan_results)

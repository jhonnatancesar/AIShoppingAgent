import subprocess

with open('modified_all_tmp.txt', encoding='utf-8') as f:
    modified = [l.strip() for l in f if l.strip()]
with open('format_only_tmp.txt', encoding='utf-8') as f:
    format_only = set(l.strip() for l in f if l.strip())

untracked_raw = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True).stdout
untracked = []
for line in untracked_raw.splitlines():
    if line.startswith('??'):
        path = line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        untracked.append(path)

EXCLUDE_PREFIXES = ('.claude/', '.context-compress/', '.forensics/', 'checkpoints/')
EXCLUDE_EXACT = {'.forensicsline_7375_normal.json', '.forensicsline_8324_full.json', 'frontend/img/Nova pasta/'}

untracked_files = []
for p in untracked:
    if p in EXCLUDE_EXACT or any(p.startswith(pre) for pre in EXCLUDE_PREFIXES):
        continue
    untracked_files.append(p)

B2 = {  # identidade global
    'backend/app/products/identity_ai.py', 'backend/app/products/identity_arbiter.py',
    'backend/app/products/identity_candidates.py', 'backend/app/products/identity_learning.py',
    'backend/app/products/identity.py',
    'backend/migrations/versions/20260912_0003_add_product_identity_candidates.py',
    'backend/migrations/versions/20260913_0001_add_identity_candidate_sku_fields.py',
    'tests/test_product_identity.py', 'tests/test_product_identity_engine.py',
    'tests/test_product_identity_ai.py', 'tests/test_product_identity_arbiter.py',
    'tests/test_product_identity_learning_matcher.py',
    'tests/integration/test_product_identity_learning.py',
}

B3 = {  # search_history + DEV panel
    'backend/app/quotas/query.py', 'backend/app/quotas/models.py', 'backend/app/quotas/service.py',
    'backend/app/authorization/policy.py', 'backend/app/webapp/dependency.py', 'backend/app/missions/query.py',
    'backend/migrations/versions/20260912_0004_add_search_history.py',
    'frontend/src/pages/dev/AllSearchesPage.tsx', 'frontend/src/pages/dev/DevSearchesShell.tsx',
    'frontend/src/pages/dev/MySearchesPage.tsx', 'frontend/src/pages/dev/SearchHistoryList.tsx',
    'tests/test_quotas_service.py', 'tests/integration/test_search_history.py',
    'tests/integration/test_search_history_http.py',
}

B5 = {  # rodada de cobertura
    'tests/test_event_catalog.py',
    'tests/test_cadence_async.py', 'tests/test_edge_cdp_supervisor_construction.py',
    'tests/test_edge_cdp_transport_failures.py', 'tests/test_historical_bootstrap_service.py',
    'tests/test_historical_bootstrap_service_async.py', 'tests/test_market_research_service.py',
    'tests/test_market_research_service_async.py', 'tests/test_missions_monitoring.py',
    'tests/test_offer_detail_partial_failure.py', 'tests/test_ops_adapter_boundaries.py',
    'tests/test_orchestration_prelist_async.py', 'tests/test_orchestration_scheduler_async.py',
    'tests/test_privacy_cli_guards.py', 'tests/test_shared_claim_async.py',
    'tests/test_shared_collection_async.py', 'tests/test_store_providers_pure_functions.py',
}

B6 = {  # chore config/docs
    '.gitleaks.toml', 'pyproject.toml', 'docs/tasks/TASK-121.md', 'docs/internal/project-context.md',
}

B1_EXTRA = {'docs/tasks/TASK-112.md'}  # cosmetic reformat, joins the format-only bucket

all_known = set(modified) | set(untracked_files)
assigned_specific = B2 | B3 | B5 | B6 | B1_EXTRA
B1 = format_only | B1_EXTRA
# B4 = everything else not yet assigned (the broad fix/parcelamento/offer_supersession/coupon_evidence bucket)
B4 = all_known - B1 - B2 - B3 - B5 - B6

buckets = {'B1_refactor_format': B1, 'B2_identity': B2, 'B3_search_history': B3,
           'B4_fix_collection_offers': B4, 'B5_coverage_tests': B5, 'B6_chore_config_docs': B6}

total_assigned = set()
for name, files in buckets.items():
    total_assigned |= files
    with open(f'_bucket_{name}.txt', 'w', encoding='utf-8') as out:
        out.write('\n'.join(sorted(files)))
    print(f"{name}: {len(files)} arquivos")

missing = all_known - total_assigned
extra = total_assigned - all_known
print()
print(f"TOTAL conhecido (modified+untracked, excluindo lixo/deferidos): {len(all_known)}")
print(f"TOTAL atribuido a buckets: {len(total_assigned)}")
print(f"Arquivos conhecidos SEM bucket (ERRO se nao vazio): {sorted(missing)}")
print(f"Arquivos em bucket mas NAO conhecidos (ERRO se nao vazio): {sorted(extra)}")
print()
print("Excluidos/deferidos (nao vao em nenhum commit agora):")
for p in untracked:
    if p in EXCLUDE_EXACT or any(p.startswith(pre) for pre in EXCLUDE_PREFIXES):
        print(" -", p)

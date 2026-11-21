"""V1/V2 replay commands; second endpoint failure yields UNKNOWN."""
import argparse,json
from collections import Counter
from os import link
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from .common import read_json,write_json
from .current_study import allowed_data,combine_sources,compute,digest,replay_claim,equal,publish,report_claims
from .publication_arithmetic import check_claim


def _bound_sources(paths, expected_hashes, expected_dataset_sha256):
    dataset, records = combine_sources(paths)
    if Counter(record['content_sha256'] for record in records) != Counter(expected_hashes):
        raise ValueError('Raw RPC recordings do not match the frozen source binding')
    if digest(dataset) != expected_dataset_sha256:
        raise ValueError('Raw RPC recordings do not reproduce the frozen dataset')

    names, hosts = set(), set()
    for path, record in zip(paths, records):
        if record['schema_version'] == 'erc20-supplement-1':
            record = read_json(Path(path).with_name(record['base_source_file']))
        for attempt in record['provider_attempts']:
            if attempt['status'] == 'available':
                provider = attempt['provider']
                names.add(provider['name'].casefold())
                hosts.add(urlsplit(provider['url']).hostname)
    if not names or not hosts or None in hosts:
        raise ValueError('Bound raw RPC recordings lack an available provider identity')
    return dataset, names, hosts


def bound_source_pair(binding, primary_paths, secondary_paths):
    first = binding.get('source_hashes', [])
    second = binding.get('secondary_source_hashes', [])
    if not first or not second or set(first) & set(second):
        raise ValueError('Primary and secondary must have separate frozen raw source bindings')
    primary, primary_names, primary_hosts = _bound_sources(primary_paths, first, binding['dataset_sha256'])
    secondary, secondary_names, secondary_hosts = _bound_sources(
        secondary_paths, second, binding['secondary_dataset_sha256'])
    if primary_names & secondary_names or primary_hosts & secondary_hosts:
        raise ValueError('Primary and secondary RPC recordings must use distinct providers')
    return primary, secondary


def _source_paths(source_dir, expected_hashes):
    paths = []
    for path in sorted(source_dir.glob('*.json')):
        value = read_json(path)
        if isinstance(value, dict) and value.get('content_sha256') in expected_hashes:
            paths.append(path)
    return paths


def bound_recorded_sources(binding, source_dir):
    return bound_source_pair(binding, _source_paths(source_dir, binding.get('source_hashes', [])),
                             _source_paths(source_dir, binding.get('secondary_source_hashes', [])))


def read_study_binding(preparation):
    binding = read_json(preparation / 'binding.json')
    freeze_path = preparation / 'formal_freeze.json'
    if freeze_path.is_file():
        freeze = read_json(freeze_path)
        contents = {key: value for key, value in freeze.items() if key != 'freeze_sha256'}
        if digest(contents) != freeze['freeze_sha256'] or binding != freeze['binding']:
            raise ValueError('Source binding does not match the formal freeze')
    return binding


def verify_v2(primary,secondary,task,report,*,expected_primary_sha256=None,allow_partial=False):
    """Check claims over normalized data from a caller-validated source pair."""
    claims = report_claims(task, report, allow_empty=True, allow_partial=allow_partial)
    checks={};first={r['target_timestamp_utc']:r for r in allowed_data(primary,task)['snapshots']}
    second_data=allowed_data(secondary,task) if secondary else None
    second={r['target_timestamp_utc']:r for r in second_data['snapshots']} if second_data else {}
    # A disagreement is attributable only when the second archive matches the frozen primary.
    primary_changed=(expected_primary_sha256 is not None and secondary is not None
                     and digest(primary)!=expected_primary_sha256
                     and digest(secondary)==expected_primary_sha256)
    for claim in claims:
        cid=claim.get('claim_id');check=replay_claim(primary,task,claim)
        expected=check.get('expected')
        if expected and (check['verdict']=='PASS' or
                         (check['verdict']=='FAIL' and claim.get('unit')==expected['unit'])):
            ids=set(expected['evidence_ids'])
            needed=[r for r in first.values() if 'E_'+r['id'] in ids]
            missing=not needed or any(r['target_timestamp_utc'] not in second for r in needed)
            if not missing and any(any(r.get(k)!=second[r['target_timestamp_utc']].get(k) for k in ['block_number','block_hash','raw_balances','raw_quotes','calldata']) for r in needed):
                if primary_changed:
                    check={'verdict':'FAIL','reason':'secondary_source_state_mismatch'}
                else:
                    check={'verdict':'UNKNOWN','reason':'source_conflict'}
            elif check['verdict']=='PASS' and missing:
                check={'verdict':'UNKNOWN','reason':'secondary_source_unavailable'}
            elif check['verdict']=='PASS':
                try:
                    actual=compute(second_data,claim['query'],independent=True)
                    if claim.get('unit')!=actual['unit'] or not equal(claim['value'],actual['value'],claim['query']['metric']):check={'verdict':'FAIL','reason':'secondary_recomputation_mismatch'}
                    else:check={**check,'source_recomputed':True,'secondary_value':actual['value']}
                except (ValueError,KeyError):check={'verdict':'UNKNOWN','reason':'secondary_recomputation_unavailable'}
        checks[cid]=check
    return checks

def publish_verified(primary, task, report, v2):
    """Apply the independent arithmetic check before resolving publication dependencies."""
    checks = dict(v2)
    arithmetic = {}
    requests = {component['slot_id']: component['request']
                for component in task['required_components']}
    for claim in report_claims(task, report, allow_empty=True):
        cid = claim['claim_id']
        if v2[cid]['verdict'] != 'PASS':
            continue
        result = check_claim(primary, task, requests[claim['slot_id']], claim)
        if result is not None:
            arithmetic[cid] = result
            if result['verdict'] != 'PASS':
                checks[cid] = result
    published, verification = publish(primary, task, report, checks_override=checks)
    return published, verification, arithmetic


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--preparation',type=Path,required=True);p.add_argument('--task',required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--primary-sources',type=Path,nargs='+');p.add_argument('--secondary-sources',type=Path,nargs='*');p.add_argument('--output',type=Path,required=True);a=p.parse_args(argv)
    try:
        if a.output.exists() or a.output.is_symlink():
            raise ValueError('Output already exists; choose a new path')
        binding = read_study_binding(a.preparation)
        primary = read_json(a.preparation / 'dataset.json')
        tasks = read_json(a.preparation / 'tasks.json')
        if digest(primary) != binding['dataset_sha256'] or digest(tasks) != binding['tasks_sha256']:
            raise ValueError('Prepared dataset or tasks do not match the frozen binding')
        task = next((item for item in tasks if item['task_id'] == a.task), None)
        if task is None:
            raise ValueError(f'Unknown task: {a.task}')
        record = read_json(a.report)
        report = record.get('report', record) if isinstance(record, dict) else record
        recorded_failure = isinstance(record, dict) and 'report' in record and record.get('status') == 'failed'
        claims = report_claims(task, report, allow_empty=recorded_failure)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        p.error(str(exc))
    secondary=None;reason=None
    if a.secondary_sources:
        try:
            source_dir = a.preparation.resolve().parents[1] / 'data/current_study'
            primary_paths = a.primary_sources or _source_paths(source_dir, binding['source_hashes'])
            _, secondary = bound_source_pair(binding, primary_paths, a.secondary_sources)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            reason=str(exc)
    v1={c['claim_id']:replay_claim(primary,task,c) for c in claims}
    v2=verify_v2(primary,secondary,task,report,expected_primary_sha256=binding['dataset_sha256'])
    published, verification, arithmetic = publish_verified(primary, task, report, v2)
    result = {'V1': v1, 'V2': v2, 'independent_arithmetic': arithmetic,
              'verification': verification, 'published': published, 'secondary_error': reason,
              'scope': 'structured numerical claims; free prose requires human review'}
    try:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='.verify-', dir=a.output.parent) as directory:
            temporary = Path(directory) / 'verified.json'
            write_json(temporary, result)
            link(temporary, a.output)
    except FileExistsError:
        p.error('Output already exists; choose a new path')
    except OSError as exc:
        p.error(str(exc))
    print(json.dumps({'output':str(a.output),'V1_pass':sum(c['verdict']=='PASS' for c in v1.values()),'V2_pass':sum(c['verdict']=='PASS' for c in v2.values())}));return 0
if __name__=='__main__':raise SystemExit(main())

"""Contract checker: variable scope and nested array elements (false positives from the blind QA-workbench test)."""
from src.services.app_validate import OpSig, contract_issues


def run(src, sigs):
    return contract_issues([{"path": "views/V.tsx", "content": src}], sigs)


COVERAGE = OpSig({"cells", "requirements", "targets", "totals", "unplaced"}, True, elements_known=False)
LINK = OpSig({"changed", "linked", "mode"}, True)
CASES = OpSig({"id", "title", "status"}, True)
SIGS = {"getCoverage": COVERAGE, "postLink": LINK, "getCases": CASES}


def test_nested_array_element_reads_are_not_top_level_reads():
    src = """
    const load = () => api.getCoverage().then((r: any) => setData(r))
    const reqs = data?.requirements || []
    export default function V() {
      const [data, setData] = useState<any>(null)
      const res = await api.getCoverage()
      return res.requirements.map((r) => <i key={r.req_key}>{r.title}</i>)
    }
    """
    assert run(src, SIGS) == []


def test_then_param_name_is_scoped_to_the_callback():
    src = """
    function load() { return api.getCoverage().then((r: any) => setData(r)) }
    function view() { return reqs.map((r) => <b>{r.req_key}</b>) }
    """
    assert run(src, SIGS) == []


def test_same_name_in_another_function_is_not_the_api_result():
    src = """
    async function link() {
      const r: any = await api.postLink({ a: 1 })
      toast(r.changed)
    }
    const cols = [{ render: (r: Row) => r.case_key }, { render: (r) => r.title }]
    rows.filter((r) => r.type === 'UI')
    """
    assert run(src, SIGS) == []


def test_true_positive_direct_read_still_flagged():
    src = """
    async function link() {
      const r: any = await api.postLink({ a: 1 })
      toast(r.bogus)
    }
    """
    out = run(src, SIGS)
    assert len(out) == 1 and "r.bogus" in out[0]


def test_true_positive_then_callback_read_still_flagged():
    src = "api.getCoverage().then((r) => setX(r.nope))"
    out = run(src, SIGS)
    assert len(out) == 1 and "r.nope" in out[0]


def test_true_positive_row_callback_over_the_result_still_flagged():
    src = """
    const rows = await api.getCases()
    rows.map((x) => x.titel)
    """
    out = run(src, SIGS)
    assert len(out) == 1 and "x.titel" in out[0]


def test_shadowing_inside_the_binding_scope_is_respected():
    src = """
    async function f() {
      const r = await api.postLink({})
      items.map((r) => r.whatever)
      return r.changed
    }
    """
    assert run(src, SIGS) == []


def test_usepoll_data_still_checked():
    src = """
    const cov = usePoll(() => api.getCoverage())
    const x = cov.data.nonsense
    """
    out = run(src, SIGS)
    assert len(out) == 1 and "nonsense" in out[0]


def test_nested_elements_unchecked_when_signature_has_only_top_level_keys():
    sigs = {"getCoverage": OpSig({"cells", "requirements"}, True, elements_known=False)}
    src = """
    const res = await api.getCoverage()
    res.requirements.map((r) => r.req_key)
    res.bogus
    """
    out = run(src, sigs)
    assert len(out) == 1 and "res.bogus" in out[0]


def test_nested_elements_checked_when_signature_knows_them():
    sigs = {"getCoverage": OpSig({"cells", "requirements", "req_key"}, True, elements_known=True)}
    src = """
    const res = await api.getCoverage()
    res.requirements.map((r) => r.req_key + r.nope)
    """
    out = run(src, sigs)
    assert len(out) == 1 and "r.nope" in out[0]

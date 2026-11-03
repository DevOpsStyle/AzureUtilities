import ast
import contextlib
import gzip
import inspect
import io
import json
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import requests


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
REPORT = SKILLS / "azure-cost-details" / "scripts" / "AzureCostDetails-Report.py"
READ = SKILLS / "azure-cost-details" / "scripts" / "AzureCostDetails-Read.py"
QUERY = SKILLS / "azure-cost-details" / "scripts" / "AzureCostDetails-Query.py"
COMMITMENTS = SKILLS / "azure-commitments" / "scripts" / "AzureCommitments-List.py"
SCRIPTS = (REPORT, READ, QUERY, COMMITMENTS)
INPUTS = (
    {"subscription_id": "subscription", "resume_state": "unchanged"},
    {"subscription_id": "subscription", "operation_url": "private", "offset": 100},
    {"subscription_id": "subscription", "start_date": "2026-10-01", "end_date": "2026-10-31"},
    {"include_inactive": False, "offset": 50},
)
SUB = "00000000-0000-0000-0000-000000000001"
ARM = "https://management.azure.com"
TOKEN = "eyJ0eXAiOiJKV1QifQ.eyJhdWQiOiJodHRwczovL21hbmFnZW1lbnQuYXp1cmUuY29tIn0.c2lnbmF0dXJl"
OPERATION = (f"{ARM}/subscriptions/{SUB}/providers/Microsoft.CostManagement/"
             "costDetailsOperationResults/operation1?api-version=2023-03-01")
BLOB = "https://account.blob.core.windows.net/reports/part0.csv.gz?sv=2024-01-01&sig=SAS-SECRET"
NO_AZURE_IDENTITY = {"azure": None, "azure.identity": None}
LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)\)")
BUDGETS = {REPORT: "max_wait_seconds", READ: "max_seconds", QUERY: "max_wait_seconds"}


def with_cli_budget(script, parameters):
    """Parameters as the CLI passes them to main(): an 80-second budget unless the request sets one."""
    budget = BUDGETS.get(script)
    return {**parameters, budget: parameters.get(budget, 80)} if budget else dict(parameters)


def load_script(path):
    """Define everything except the CLI entry, as importing the module would."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    entry = tree.body[-1]
    namespace = {"__name__": "bundled_script"}
    exec(compile(ast.Module(body=tree.body[:-1], type_ignores=[]), str(path), "exec"), namespace)
    return namespace, entry


def run_cli(path, parameters, *, args=None, fake_main=None):
    """Run the CLI entry of a script. fake_main replaces main() and keeps its signature."""
    namespace, entry = load_script(path)
    calls = []
    if fake_main is not None:
        signature = inspect.signature(namespace["main"])

        def main(**kwargs):
            calls.append(kwargs)
            return fake_main(namespace, kwargs)

        main.__signature__ = signature
        namespace["main"] = main
    namespace["__name__"] = "__main__"
    text = parameters if isinstance(parameters, str) else json.dumps(parameters)
    stdout, stderr, code = io.StringIO(), io.StringIO(), 0
    with tempfile.TemporaryDirectory() as directory:
        request = Path(directory) / "request.json"
        request.write_text(text, encoding="utf-8")
        argv = ["--input", "{input}"] if args is None else args
        argv = [str(request) if value == "{input}" else value for value in argv]
        with patch("sys.argv", [str(path), *argv]), patch("sys.stdin", io.StringIO(text)), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                exec(compile(ast.Module(body=[entry], type_ignores=[]), str(path), "exec"), namespace)
            except SystemExit as exc:
                code = exc.code or 0
    return SimpleNamespace(code=code, out=stdout.getvalue(), err=stderr.getvalue(), calls=calls)


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, content=b"", duration=None):
        self.status_code, self._body, self.headers, self._content = status, body, headers or {}, content
        self.duration = duration

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self._content), chunk_size):
            yield self._content[start:start + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeClock:
    """Simulated time: sleeps and slow responses advance it instantly."""

    def __init__(self):
        self.now = 1_800_000_000.0

    def advance(self, seconds):
        self.now += seconds

    def active(self):
        return patch.multiple("time", time=lambda: self.now, monotonic=lambda: self.now, sleep=self.advance)


class FakeAzure:
    """Stand-in for requests: answers by method and URL prefix and records every call.

    A route can answer with a list of responses, used in order. With a clock, every response
    takes its own duration or `duration` seconds, and a response slower than the read
    timeout of the call raises ReadTimeout, as requests does.
    """

    def __init__(self, *routes, clock=None, duration=0):
        self.routes, self.calls, self.clock, self.duration = routes, [], clock, duration

    def request(self, method, url, headers=None, **kwargs):
        self.calls.append(SimpleNamespace(method=method.upper(), url=url, headers=dict(headers or {}), kwargs=kwargs))
        for route_method, prefix, response in self.routes:
            if route_method == method.upper() and url.startswith(prefix):
                break
        else:
            raise AssertionError(f"unexpected {method} {url}")
        if isinstance(response, list):
            response = response.pop(0) if len(response) > 1 else response[0]
        if self.clock:
            duration = self.duration if response.duration is None else response.duration
            read_timeout = (kwargs.get("timeout") or (None, None))[1]
            if read_timeout is not None and duration > read_timeout:
                self.clock.advance(read_timeout)
                raise requests.ReadTimeout("simulated slow response")
            self.clock.advance(duration)
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def active(self):
        return patch.multiple("requests", request=self.request, get=self.get, post=self.post)


class SkillPackagingTests(unittest.TestCase):
    def test_plugin_discovers_all_skills(self):
        plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["skills"], ["skills/"])
        self.assertEqual(
            {path.parent.name for path in SKILLS.glob("*/SKILL.md")},
            {"azure-cost-advisor", "azure-cost-details", "azure-commitments",
             "azure-cost-increase-assessment"},
        )

    def test_skill_frontmatter(self):
        for path in SKILLS.glob("*/SKILL.md"):
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                frontmatter = text.split("---", 2)[1]
                self.assertRegex(frontmatter, rf"(?m)^name: {path.parent.name}$")
                self.assertRegex(frontmatter, r"(?m)^description: \S")
                self.assertNotRegex(frontmatter, r"(?m)^tools:")
                self.assertLess(len(text.splitlines()), 500)
                description = re.search(r"(?m)^description: (.*)$", frontmatter).group(1)
                if description[:1] not in ("'", '"', ">", "|"):
                    self.assertNotRegex(description, r": | #", "a plain YAML value cannot contain ': ' or ' #'")

    def test_skill_frontmatter_is_strict_yaml(self):
        # The plugin importer parses the front matter as YAML. If that fails, the skill is imported
        # with an empty description and the agent never selects it.
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        for path in SKILLS.glob("*/SKILL.md"):
            with self.subTest(skill=path.parent.name):
                data = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
                self.assertEqual(data["name"], path.parent.name)
                self.assertTrue(1 <= len(data["description"]) <= 1024)

    def test_markdown_links_stay_inside_their_skill(self):
        # The agent receives only the skill folder, so a link outside it does not resolve.
        for skill in (path.parent for path in SKILLS.glob("*/SKILL.md")):
            for document in skill.rglob("*.md"):
                fenced = False
                for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
                    if line.lstrip().startswith(("```", "~~~")):
                        fenced = not fenced
                        continue
                    for target in [] if fenced else LINK.findall(re.sub(r"`[^`]*`", "", line)):
                        if target.startswith(("http://", "https://", "mailto:", "#")):
                            continue
                        resolved = (document.parent / target.split("#", 1)[0]).resolve()
                        with self.subTest(file=document.relative_to(SKILLS).as_posix(), line=number, link=target):
                            self.assertTrue(resolved.is_relative_to(skill.resolve()), "link leaves the skill folder")
                            self.assertTrue(resolved.is_file(), "link target is missing")

    def test_executable_skills_document_bundled_execution(self):
        for name in ("azure-cost-details", "azure-commitments"):
            with self.subTest(skill=name):
                folder = SKILLS / name
                text = (folder / "SKILL.md").read_text(encoding="utf-8")
                requirements = (folder / "scripts" / "requirements.txt").read_text(encoding="utf-8")
                self.assertIn("requests", requirements)
                self.assertIn("azure-identity", requirements)
                for needle in (f"runtime-content/skills/plugins/*/live/{name}", "read_skill_file", "CreateFile",
                               "RunInTerminal", "RunAzCliReadCommands", "get-access-token", "--token-file",
                               "--output", "--input", "DefaultAzureCredential", "auth_required"):
                    self.assertIn(needle, text)

    def test_scripts_load_without_azure_identity(self):
        for script in SCRIPTS:
            with self.subTest(script=script.name), patch.dict(sys.modules, NO_AZURE_IDENTITY):
                namespace, _ = load_script(script)
                self.assertIsNone(namespace["credential"])
                with self.assertRaises(namespace["_AuthUnavailable"]):
                    namespace["_arm_token"]()

    def test_missing_credential_is_reported_before_any_request(self):
        requests = {
            REPORT: {"subscription_id": SUB, "start_date": "2026-10-01", "end_date": "2026-10-01"},
            READ: {"subscription_id": SUB, "operation_url": OPERATION},
            QUERY: {"subscription_id": SUB, "start_date": "2026-10-01", "end_date": "2026-10-31"},
            COMMITMENTS: {},
        }
        azure = FakeAzure()
        for script, parameters in requests.items():
            with self.subTest(script=script.name), azure.active(), patch.dict(sys.modules, NO_AZURE_IDENTITY):
                namespace, _ = load_script(script)
                result = namespace["main"](**parameters)
                self.assertEqual(result["status"], "error")
                self.assertTrue(result["auth_required"])
        self.assertEqual(azure.calls, [])

    def test_without_a_token_file_the_scripts_use_the_agent_managed_identity(self):
        # The workspace sets AZURE_CLIENT_ID, so DefaultAzureCredential is the agent's managed identity.
        created = []

        class DefaultAzureCredential:
            def __init__(self):
                created.append(self)

            def get_token(self, *scopes, **kwargs):
                assert scopes == ("https://management.azure.com/.default",)
                return SimpleNamespace(token="managed-identity-token")

        identity = ModuleType("azure.identity")
        identity.DefaultAzureCredential = DefaultAzureCredential
        package = ModuleType("azure")
        package.identity = identity
        requests_by_script = {
            REPORT: {"subscription_id": SUB, "start_date": "2026-10-01", "end_date": "2026-10-01"},
            READ: {"subscription_id": SUB, "operation_url": OPERATION},
            QUERY: {"subscription_id": SUB, "start_date": "2026-10-01", "end_date": "2026-10-31"},
            COMMITMENTS: {},
        }
        denied = FakeResponse(403, {"error": {"code": "AuthorizationFailed", "message": "denied"}})
        for script, parameters in requests_by_script.items():
            azure = FakeAzure(("POST", ARM, denied), ("GET", ARM, denied))
            with self.subTest(script=script.name), tempfile.TemporaryDirectory() as directory, azure.active(), \
                    patch.dict(sys.modules, {"azure": package, "azure.identity": identity}):
                output = Path(directory) / "result.json"
                run = run_cli(script, parameters, args=["--input", "{input}", "--output", str(output)])
                self.assertEqual(run.code, 0, run.err)
                self.assertNotIn("auth_required", json.loads(output.read_text(encoding="utf-8")))
                self.assertTrue(azure.calls)
                for call in azure.calls:
                    self.assertEqual(call.headers["Authorization"], "Bearer managed-identity-token")
        self.assertEqual(len(created), len(requests_by_script), "one lazily created credential per run")

    def test_cli_forwards_json_and_preserves_results(self):
        for script, parameters in zip(SCRIPTS, INPUTS):
            for status in ("ok", "ready", "pending", "retry", "partial", "error"):
                with self.subTest(script=script.name, status=status):
                    result = {"status": status, "data_complete": False,
                              "resume_state": "opaque", "next_offset": 200}
                    run = run_cli(script, parameters, fake_main=lambda namespace, kwargs: result)
                    self.assertEqual(run.code, 0, run.err)
                    self.assertEqual(json.loads(run.out), result)
                    self.assertEqual(run.calls, [with_cli_budget(script, parameters)])

    def test_cli_stdin_and_help(self):
        for script, parameters in zip(SCRIPTS, INPUTS):
            with self.subTest(script=script.name):
                run = run_cli(script, parameters, args=[],
                              fake_main=lambda namespace, kwargs: {"status": "ok", "parameters": kwargs})
                self.assertEqual(run.code, 0, run.err)
                self.assertEqual(json.loads(run.out)["parameters"], with_cli_budget(script, parameters))
                self.assertEqual(run.calls, [with_cli_budget(script, parameters)])
                run = run_cli(script, "{}", args=["--help"], fake_main=lambda namespace, kwargs: {})
                self.assertEqual(run.code, 0, run.err)
                for option in ("--input", "--output", "--token-file"):
                    self.assertIn(option, run.out)
                self.assertEqual(run.calls, [])

    def test_invalid_input_never_calls_azure(self):
        for script in SCRIPTS:
            for invalid in ("{", "[]", "null", '{"unexpected": "private-value"}'):
                with self.subTest(script=script.name, invalid=invalid):
                    run = run_cli(script, invalid, fake_main=lambda namespace, kwargs: {"status": "ok"})
                    self.assertEqual(run.code, 2)
                    self.assertEqual(run.out, "")
                    self.assertIn("error:", run.err)
                    self.assertNotIn("private-value", run.err)
                    self.assertEqual(run.calls, [])
        for script in SCRIPTS[:3]:
            with self.subTest(missing_required=script.name):
                run = run_cli(script, "{}", fake_main=lambda namespace, kwargs: {"status": "ok"})
                self.assertEqual(run.code, 2)
                self.assertEqual(run.calls, [])

    def test_cli_token_file_is_used_and_never_printed(self):
        for script, parameters in zip(SCRIPTS, INPUTS):
            with self.subTest(script=script.name), tempfile.TemporaryDirectory() as directory:
                token_file = Path(directory) / "arm-token"
                token_file.write_text(f'"Bearer {TOKEN}"\r\n', encoding="utf-8")
                run = run_cli(script, parameters, args=["--input", "{input}", "--token-file", str(token_file)],
                              fake_main=lambda namespace, kwargs: {"status": "ok",
                                                                   "token_used": namespace["_arm_token"]() == TOKEN})
                self.assertEqual(run.code, 0, run.err)
                self.assertTrue(json.loads(run.out)["token_used"])
                self.assertNotIn(TOKEN, run.out + run.err)

    def test_cli_rejects_a_bad_token_file_without_echoing_it(self):
        for script, parameters in zip(SCRIPTS, INPUTS):
            with tempfile.TemporaryDirectory() as directory:
                bad = Path(directory) / "arm-token"
                bad.write_text("not a token private-secret", encoding="utf-8")
                for token_file in (bad, Path(directory) / "missing"):
                    with self.subTest(script=script.name, token_file=token_file.name):
                        run = run_cli(script, parameters,
                                      args=["--input", "{input}", "--token-file", str(token_file)],
                                      fake_main=lambda namespace, kwargs: {"status": "ok"})
                        self.assertEqual(run.code, 2)
                        self.assertEqual(run.calls, [])
                        self.assertNotIn("private-secret", run.out + run.err)

    def test_cli_output_file_keeps_private_fields_out_of_the_terminal(self):
        result = {"status": "ready", "operation_url": OPERATION, "resume_state": '{"url": "private"}',
                  "records": [{"cost": "1"}], "available_columns": ["Cost"], "notes": ["note"], "has_more": False}
        for script, parameters in zip(SCRIPTS, INPUTS):
            with self.subTest(script=script.name), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "new-folder" / "result.json"
                run = run_cli(script, parameters, args=["--input", "{input}", "--output", str(output)],
                              fake_main=lambda namespace, kwargs: result)
                self.assertEqual(run.code, 0, run.err)
                self.assertEqual(json.loads(output.read_text(encoding="utf-8")), result)
                self.assertEqual(json.loads(run.out), {"status": "ready", "has_more": False, "output": str(output)})
                self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def run_real(self, script, parameters, azure, directory):
        """Run the real script through its CLI, as the skill does, against fake Azure endpoints."""
        token_file, output = Path(directory) / "arm-token", Path(directory) / f"{script.stem}.json"
        token_file.write_text(TOKEN + "\n", encoding="utf-8")
        with azure.active(), patch.dict(sys.modules, NO_AZURE_IDENTITY):
            run = run_cli(script, parameters, args=["--input", "{input}", "--token-file", str(token_file),
                                                    "--output", str(output)])
        self.assertEqual(run.code, 0, run.err)
        written = output.read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, run.out + run.err + written)
        return json.loads(written), json.loads(run.out)

    def test_report_creates_then_resumes_the_same_report(self):
        create = f"{ARM}/subscriptions/{SUB}/providers/Microsoft.CostManagement/generateCostDetailsReport"
        manifest = {"blobCount": 1, "byteCount": 10, "blobs": [{"blobLink": BLOB}]}
        azure = FakeAzure(
            ("POST", create, FakeResponse(202, {}, {"Location": OPERATION, "Retry-After": "1"})),
            ("GET", OPERATION, FakeResponse(200, {"id": "report", "status": "Completed", "manifest": manifest})),
        )
        request = {"subscription_id": SUB, "start_date": "2026-10-01", "end_date": "2026-10-31",
                   "max_wait_seconds": 0}
        with tempfile.TemporaryDirectory() as directory:
            first, summary = self.run_real(REPORT, request, azure, directory)
            self.assertEqual(first["status"], "pending")
            self.assertTrue(first["resume_state"])
            self.assertNotIn("resume_state", summary)
            self.assertNotIn("operation_url", summary)
            resumed = {**request, "resume_state": first["resume_state"], "max_wait_seconds": 30}
            second, summary = self.run_real(REPORT, resumed, azure, directory)
        self.assertEqual((second["status"], second["operation_url"]), ("ready", OPERATION))
        self.assertEqual(summary["status"], "ready")
        self.assertEqual([call.method for call in azure.calls], ["POST", "GET"])
        for call in azure.calls:
            self.assertEqual(call.headers["Authorization"], "Bearer " + TOKEN)
            self.assertEqual(call.headers["ClientType"], "DevOpsStyle-AzureUtilities-CostDetails")

    def test_read_aggregates_every_row_and_never_sends_the_token_to_storage(self):
        vm = f"/subscriptions/{SUB}/resourcegroups/rg/providers/microsoft.compute/virtualmachines/vm1"
        disk = f"/subscriptions/{SUB}/resourcegroups/rg/providers/microsoft.compute/disks/disk1"
        rows = ("ResourceId,ResourceName,ResourceGroup,CostInBillingCurrency,BillingCurrencyCode,"
                "ServiceName,PricingModel,ChargeType\n"
                f"{vm},vm1,rg,10.50,EUR,Virtual Machines,OnDemand,Usage\n"
                f"{vm},vm1,rg,1.25,EUR,Virtual Machines,OnDemand,Usage\n"
                f"{disk},disk1,rg,3.00,EUR,Storage,OnDemand,Usage\n"
                ",,,0.75,EUR,Support,OnDemand,Purchase\n")
        blob = gzip.compress(rows.encode("utf-8"))
        manifest = {"blobCount": 1, "byteCount": len(blob), "dataFormat": "Csv", "blobs": [{"blobLink": BLOB}],
                    "requestContext": {"requestScope": f"subscriptions/{SUB}", "requestBody": {"metric": "ActualCost"}}}
        azure = FakeAzure(("GET", OPERATION, FakeResponse(200, {"status": "Completed", "manifest": manifest})),
                          ("GET", BLOB, FakeResponse(200, content=blob)))
        with tempfile.TemporaryDirectory() as directory:
            result, summary = self.run_real(
                READ, {"subscription_id": SUB, "operation_url": OPERATION, "max_seconds": 60}, azure, directory)
        self.assertEqual((result["status"], result["data_complete"]), ("ok", True))
        self.assertEqual(result["totals_by_currency"], {"EUR": "15.50"})
        self.assertEqual(result["unattributed_totals_by_currency"], {"EUR": "0.75"})
        self.assertEqual({record["resource_name"]: record["cost"] for record in result["records"]},
                         {"vm1": "11.75", "disk1": "3.00", "Unattributed": "0.75"})
        self.assertNotIn("records", summary)
        self.assertNotIn("SAS-SECRET", json.dumps(result))
        arm, storage = azure.calls
        self.assertEqual(arm.headers["Authorization"], "Bearer " + TOKEN)
        self.assertNotIn("Authorization", storage.headers)

    def test_query_reads_both_metrics(self):
        vm = f"/subscriptions/{SUB}/resourcegroups/rg/providers/microsoft.compute/virtualmachines/vm1"
        columns = [{"name": "Cost"}, {"name": "ResourceId"}, {"name": "PricingModel"}, {"name": "Currency"}]
        body = {"properties": {"columns": columns, "rows": [[12.5, vm, "OnDemand", "EUR"]]}}
        azure = FakeAzure(("POST", f"{ARM}/subscriptions/{SUB}/providers/Microsoft.CostManagement/query",
                           FakeResponse(200, body)))
        request = {"subscription_id": SUB, "start_date": "2026-10-01", "end_date": "2026-10-31",
                   "meter_categories": "Virtual Machines,Storage"}
        with tempfile.TemporaryDirectory() as directory:
            result, summary = self.run_real(QUERY, request, azure, directory)
        self.assertEqual((result["status"], result["data_complete"]), ("ok", True))
        self.assertEqual((result["records"][0]["cost"], result["records"][0]["amortized_cost"]), ("12.5", "12.5"))
        self.assertEqual([call.kwargs["json"]["type"] for call in azure.calls], ["ActualCost", "AmortizedCost"])
        self.assertTrue(all(call.headers["Authorization"] == "Bearer " + TOKEN for call in azure.calls))
        self.assertEqual(summary["totals_by_currency"], {"cost": {"EUR": "12.5"}, "amortized_cost": {"EUR": "12.5"}})

    def test_commitments_lists_reservations_and_savings_plans(self):
        reservation = {
            "id": "/providers/Microsoft.Capacity/reservationOrders/order1/reservations/reservation1",
            "location": "northeurope", "sku": {"name": "Standard_D4s_v5"},
            "properties": {"displayName": "vm-reservation", "provisioningState": "Succeeded",
                           "reservedResourceType": "VirtualMachines", "quantity": 2, "term": "P3Y",
                           "effectiveDateTime": "2025-01-01T00:00:00Z", "expiryDateTime": "2099-01-01T00:00:00Z",
                           "appliedScopeType": "Single",
                           "appliedScopeProperties": {"subscriptionId": f"/subscriptions/{SUB}"}}}
        azure = FakeAzure(
            ("GET", f"{ARM}/providers/Microsoft.Capacity/reservations", FakeResponse(200, {"value": [reservation]})),
            ("GET", f"{ARM}/providers/Microsoft.BillingBenefits/savingsPlans", FakeResponse(200, {"value": []})),
        )
        with tempfile.TemporaryDirectory() as directory:
            result, summary = self.run_real(
                COMMITMENTS, {"subscription_id": SUB, "effective_date": "2027-02-01"}, azure, directory)
        self.assertEqual((result["status"], result["data_complete"], summary["visible_count"]), ("ok", True, 1))
        record = result["records"][0]
        self.assertEqual((record["name"], record["applies_to_subscription"], record["covers_effective_date"]),
                         ("vm-reservation", "yes", True))
        self.assertTrue(all(call.headers["Authorization"] == "Bearer " + TOKEN for call in azure.calls))

    def test_http_401_asks_for_a_new_token_and_403_stays_a_permission_error(self):
        cases = (
            (REPORT, {"subscription_id": SUB, "start_date": "2026-10-01", "end_date": "2026-10-01"}, "POST",
             f"{ARM}/subscriptions/{SUB}/providers/Microsoft.CostManagement/generateCostDetailsReport"),
            (READ, {"subscription_id": SUB, "operation_url": OPERATION}, "GET", OPERATION),
            (QUERY, {"subscription_id": SUB, "start_date": "2026-10-01", "end_date": "2026-10-31"}, "POST",
             f"{ARM}/subscriptions/{SUB}/providers/Microsoft.CostManagement/query"),
            (COMMITMENTS, {}, "GET", f"{ARM}/providers/"),
        )
        body = {"error": {"code": "ExpiredAuthenticationToken", "message": "The access token expired."}}
        for script, request, method, url in cases:
            for status in (401, 403):
                azure = FakeAzure((method, url, FakeResponse(status, body)))
                with self.subTest(script=script.name, http=status), tempfile.TemporaryDirectory() as directory:
                    result, summary = self.run_real(script, request, azure, directory)
                    self.assertEqual(result.get("auth_required", False), status == 401)
                    self.assertEqual(summary.get("auth_required", False), status == 401)
                    expected = "forbidden" if status == 403 and script in (QUERY, COMMITMENTS) else "error"
                    self.assertEqual(result["status"], expected)
                    if script is QUERY:
                        self.assertEqual(len(azure.calls), 1, "a failed token must not be retried for AmortizedCost")

    def test_cli_defaults_to_an_80_second_budget(self):
        # Terminal commands stop after 120 seconds: a run without a budget would lose its result.
        for script, parameters in zip(SCRIPTS, INPUTS):
            budget = BUDGETS.get(script)
            with self.subTest(script=script.name):
                run = run_cli(script, parameters, fake_main=lambda namespace, kwargs: {"status": "ok"})
                self.assertEqual(run.calls, [{**parameters, budget: 80} if budget else parameters])
                if budget:
                    run = run_cli(script, {**parameters, budget: 200},
                                  fake_main=lambda namespace, kwargs: {"status": "ok"})
                    self.assertEqual(run.calls[0][budget], 200)

    def test_query_stops_within_its_budget_when_pages_are_slow(self):
        url = f"{ARM}/subscriptions/{SUB}/providers/Microsoft.CostManagement/query"
        columns = [{"name": "Cost"}, {"name": "ResourceId"}, {"name": "PricingModel"}, {"name": "Currency"}]
        page = {"properties": {"columns": columns, "rows": [[1.0, "", "OnDemand", "EUR"]],
                               "nextLink": url + "?api-version=2023-03-01&$skiptoken=next"}}
        clock = FakeClock()
        azure = FakeAzure(("POST", url, FakeResponse(200, page)), clock=clock, duration=30)
        start = clock.now
        with tempfile.TemporaryDirectory() as directory, clock.active():
            result, _ = self.run_real(QUERY, {"subscription_id": SUB, "start_date": "2026-10-01",
                                              "end_date": "2026-10-31"}, azure, directory)
        self.assertEqual((result["status"], result["data_complete"]), ("split_required", False))
        self.assertLessEqual(clock.now - start, 80)
        self.assertEqual(len(azure.calls), 3, "no request for AmortizedCost after the budget ran out")

    def test_query_returns_retry_instead_of_waiting_past_its_budget(self):
        url = f"{ARM}/subscriptions/{SUB}/providers/Microsoft.CostManagement/query"
        throttled = FakeResponse(429, {}, {"Retry-After": "29"})
        slow = FakeResponse(200, {"properties": {"columns": [], "rows": []}}, duration=60)
        clock = FakeClock()
        azure = FakeAzure(("POST", url, [throttled, throttled, slow]), clock=clock, duration=1)
        start = clock.now
        with tempfile.TemporaryDirectory() as directory, clock.active():
            result, _ = self.run_real(QUERY, {"subscription_id": SUB, "start_date": "2026-10-01",
                                              "end_date": "2026-10-31"}, azure, directory)
        self.assertEqual((result["status"], result["http_status"]), ("retry", 429))
        self.assertLessEqual(clock.now - start, 80)
        self.assertEqual(len(azure.calls), 2)

    def test_read_starts_a_new_run_after_a_long_wait(self):
        clock = FakeClock()
        azure = FakeAzure(("GET", OPERATION, FakeResponse(429, {}, {"Retry-After": "5"})), clock=clock)
        request = {"subscription_id": SUB, "operation_url": OPERATION, "max_seconds": 80}
        with tempfile.TemporaryDirectory() as directory, clock.active():
            result, _ = self.run_real(READ, {**request, "not_before_unix": int(clock.now) + 30}, azure, directory)
            self.assertEqual((result["status"], result["not_before_unix"]), ("retry", 0))
            self.assertEqual((result["operation_url"], result["subscription_id"]), (OPERATION, SUB))
            self.assertEqual(azure.calls, [], "the download must get the whole budget of a new run")
            result, _ = self.run_real(READ, {**request, "not_before_unix": int(clock.now) + 10}, azure, directory)
        self.assertEqual((result["status"], result["http_status"]), ("retry", 429))
        self.assertEqual(len(azure.calls), 1)

    def test_read_that_runs_out_of_time_after_a_wait_retries_instead_of_splitting(self):
        rows = "ResourceId,CostInBillingCurrency,BillingCurrencyCode\n,1.00,EUR\n"
        manifest = {"blobCount": 3, "dataFormat": "Csv", "blobs": [{"blobLink": BLOB}] * 3,
                    "requestContext": {"requestScope": f"subscriptions/{SUB}"}}
        clock = FakeClock()
        azure = FakeAzure(("GET", OPERATION, FakeResponse(200, {"manifest": manifest}, duration=0)),
                          ("GET", BLOB, FakeResponse(200, content=rows.encode("utf-8"), duration=25)), clock=clock)
        request = {"subscription_id": SUB, "operation_url": OPERATION, "max_seconds": 80}
        with tempfile.TemporaryDirectory() as directory, clock.active():
            # A 15-second wait leaves too little time for three 25-second downloads.
            result, _ = self.run_real(READ, {**request, "not_before_unix": int(clock.now) + 15}, azure, directory)
            self.assertEqual((result["status"], result["not_before_unix"]), ("retry", 0))
            self.assertEqual((result["operation_url"], result["subscription_id"]), (OPERATION, SUB))
            result, _ = self.run_real(READ, {**request, "not_before_unix": result["not_before_unix"]}, azure, directory)
        self.assertEqual((result["status"], result["data_complete"]), ("ok", True))
        self.assertEqual(result["totals_by_currency"], {"EUR": "3.00"})

    def test_query_reports_split_required_when_only_the_second_metric_runs_out_of_time(self):
        columns = [{"name": "Cost"}, {"name": "ResourceId"}, {"name": "PricingModel"}, {"name": "Currency"}]
        clock = FakeClock()
        azure = FakeAzure(("POST", f"{ARM}/subscriptions/{SUB}/providers/Microsoft.CostManagement/query",
                           FakeResponse(200, {"properties": {"columns": columns, "rows": []}})),
                          clock=clock, duration=45)
        with tempfile.TemporaryDirectory() as directory, clock.active():
            result, _ = self.run_real(QUERY, {"subscription_id": SUB, "start_date": "2026-10-01",
                                              "end_date": "2026-10-31"}, azure, directory)
        self.assertEqual((result["status"], result["data_complete"]), ("split_required", False))
        self.assertEqual({metric: source["status"] for metric, source in result["sources"].items()},
                         {"ActualCost": "ok", "AmortizedCost": "split_required"})


if __name__ == "__main__":
    unittest.main()

"""Eleven closed model tools; human grants stay outside model tools."""
from .errors import safe_file
from .capabilities import probe_capabilities
from .chat_contracts import validate_call
from .intent import compile_intent
from .intake import inspect_input, list_inputs
from .reporting import build_report, write_record


class ToolAPI:
    def __init__(self, controller):
        self.controller = controller

    def summary(self, value: dict) -> dict:
        return {**{key: value[key] for key in ("workflowId", "revision", "specSha256", "state")},
                "grantPresent": value["grant"] is not None, "reference": value.get("reference"),
                "resourceWaiting": value.get("resourceWaiting", False) or self.controller.resources.waiting(value["workflowId"]),
                "stages": [{"id": entry["id"], "state": entry["state"]} for entry in value["stages"]],
                "attempts": [{**{key: attempt[key] for key in ("attemptId", "stageId", "state")},
                              "progress": self.controller.progress(attempt["attemptId"])} for attempt in value["attempts"]],
                "record": write_record(self.controller.project, value)}

    def _compile(self, intent: dict) -> dict:
        toolchain = None
        if intent["profile"] in {"protein-ligand-external", "small-molecule-water"}:
            toolchain = probe_capabilities(self.controller.python, cache_path=self._cache_path())["toolchainSha256"]
        return compile_intent(self.controller.project, intent, toolchain_sha256=toolchain)

    def _cache_path(self):
        return safe_file(self.controller.project, "chat/environment-hashes.json")

    def call(self, name: str, arguments: dict) -> dict:
        args = validate_call(name, arguments)
        controller = self.controller
        if name == "bio_capabilities":
            value = probe_capabilities(controller.python, cache_path=self._cache_path())
            return {**{key: value[key] for key in ("capabilities", "blockers", "supportedProfiles", "toolchainSha256")},
                    "packages": value["environment"]["packages"], "record": write_record(controller.project, value)}
        if name == "bio_inputs": return list_inputs(controller.project, **args)
        if name == "bio_inspect": return inspect_input(controller.project, **args)
        if name == "bio_list": return controller.store.list(**args)
        if name == "bio_plan": return self.summary(controller.plan(self._compile(args["intent"])))
        if name == "bio_continue":
            return self.summary(controller.continue_plan(args["sourceWorkflowId"], args["sourceAttemptId"], self._compile(args["intent"])))
        workflow_id = args["workflowId"]
        if name == "bio_get": return self.summary(controller.tick(workflow_id, advance=False))
        if name == "bio_submit": return self.summary(controller.submit(workflow_id, args["revision"]))
        if name == "bio_cancel": return self.summary(controller.cancel(workflow_id))
        if name == "bio_artifacts": return controller.artifacts(workflow_id, args["stageId"], args["offset"], args["limit"])
        return build_report(controller, workflow_id)

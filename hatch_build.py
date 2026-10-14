from __future__ import annotations

import importlib
import runpy
from pathlib import Path


def get_build_hook() -> type:
    interface = importlib.import_module("hatchling.builders.hooks.plugin.interface")

    class CustomBuildHook(interface.BuildHookInterface):
        root: str
        target_name: str

        def initialize(self, version: str, build_data: dict) -> None:
            if self.target_name == "wheel" and version == "editable":
                return
            root = Path(self.root)
            artifacts = runpy.run_path(str(root / "src/basicly/lefthook_artifacts.py"))
            directory = artifacts["prepared_directory"](root)
            artifacts["validate_artifacts"](directory)
            destination = (
                f"build/lefthook/{artifacts['VERSION']}"
                if self.target_name == "sdist"
                else f"basicly/lefthook_assets/{artifacts['VERSION']}"
            )
            for entry in artifacts["MANIFEST"]["assets"]:
                name = entry["name"]
                build_data["force_include"][str(directory / name)] = f"{destination}/{name}"

    return CustomBuildHook
